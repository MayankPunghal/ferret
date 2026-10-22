# Handoff — Knowledge Search (state at end of the cloud session, 2026-10-22)

Read this first, then `CLAUDE.md` (standing instructions), `README.md` (how to run),
`docs/ARCHITECTURE.md` (decisions + sources), `docs/EVALUATION.md` (numbers).
Authoritative scope: `docs/brief/…master_prompt.md` §11 (gates + acceptance criteria).

## 1. Resume locally

```bash
git clone https://github.com/MayankPunghal/knowledge-search && cd knowledge-search
./setup.sh --demo                      # Windows: setup.bat -Demo
uv run pytest -q                       # expect: 19 passed (needs the db container + model cache)
uv run python eval/run_eval.py         # rebuilds database ks_eval, prints metrics
```

## 2. What exists (modular monolith, ~1.6k lines of Python + small static UI)

| Path | Role |
|---|---|
| `src/knowledge_search/migrations/0001_init.sql` | schema: users/groups/grants, documents/versions, chunks (tsvector, vector(384), idents[]), jobs, audit, app_meta |
| `config.py` | all `KS_*` settings; **no generation/remote-AI settings exist** |
| `parsing.py` | validation, Markdown (front matter, heading anchors), text, PDF (page anchors), chunker, identifier extractor |
| `embedder.py` | fastembed/ONNX CPU adapter + pinned model registry (prefixes, dim, licence, vector-space key) |
| `ingest.py` | blob store, idempotent `submit`, delete+GC, retry, `Worker` (SKIP LOCKED, backoff, atomic chunk swap, superseded jobs, stale-lock recovery) |
| `search.py` | three permission-filtered branches, RRF, `gate_basis`, outcomes/reason codes, coverage, conflict detection |
| `auth.py` | argon2id accounts, opaque sessions, principal → readable/writable collection ids, audit |
| `api.py` | FastAPI routes (OpenAPI `/docs`), CSRF header for cookie sessions, 404-for-unauthorized, inert source serving, status |
| `cli.py` | migrate, users/groups/collections/grants, ingest dir, serve (`--workers`), worker, download-model |
| `static/` | search workspace UI (search, scope chips, outcome banner, evidence vs weaker matches, passage drawer, source open, uploads, retry/delete, status) |
| `fixtures/` | FICTIONAL corpus (12 docs, md/txt/pdf) + PDF generator |
| `tests/` | 19 integration tests on real Postgres + real model |
| `eval/` | 56 labelled queries, `run_eval.py`, `bench_concurrency.py`, committed results |
| `Dockerfile`, `docker-compose.yml`, `deploy/Caddyfile`, `.env.example` | laptop + server profiles |

## 3. Gate / acceptance checklist (status as of handoff)

Legend: **verified** = command run and output inspected in this session; **implemented** = code exists,
not independently verified; **blocked**; **not started**.

| Acceptance criterion (§11) | Status | Evidence |
|---|---|---|
| 1 Start from documented commands on laptop env | verified | native: `migrate`, `create-user`, `ingest --wait`, `serve`, `worker` all run (Linux 4 vCPU container) |
| 2 CPU-only search, no generator/GPU/outbound AI | verified | `test_search_makes_no_outbound_network_calls` (sockets blocked during search; settings have no AI/provider fields); `KS_OFFLINE=1` used throughout |
| 3 Ingest; real job status/errors; unchanged not reprocessed | verified | `test_ingest_update_unchanged_delete`, `test_rejected_and_failed_ingestion_are_explicit`; CLI re-ingest printed `unchanged`, 0 jobs |
| 4 Exact-identifier + conceptual queries with real source refs | verified | `test_exact_identifier_query`, `test_api_path_query`, `test_conceptual_paraphrase_query`, `test_pdf_result_opens_real_source_page` (bytes equal original) |
| 5 No-evidence vs incomplete indexing vs technical failure distinct | verified | tests for `no_adequate_evidence`, `index_coverage_incomplete` (pending + failed), `retrieval_degraded`, `retrieval_failure` (503) |
| 6 Authorization prevents cross-scope access (endpoints + UI paths) | verified (API) | `test_restricted_collection_is_invisible`, `..._equals_nonexistent`, `..._revocation_applies_immediately`, `test_write_permission_required`, CSRF test. UI uses the same endpoints. |
| 7 Update/delete/retry leave no stale or duplicate records | verified (exercised paths) | single version of chunks after update; old identifier unsearchable; delete removes chunks/blob; transient embedding failure retried |
| 8 Repeatable labelled evaluation incl. false-answer rate + coverage | verified | `eval/run_eval.py`; results in `eval/results/`, summarized in EVALUATION.md |
| 9 CPU server profile runnable with persistent storage + multi-user auth | partially verified | native server profile verified (separate API + worker processes, Postgres container volume, 4 users via HTTP, concurrency test 0 errors). **Docker image build blocked** (Docker Hub + ECR returned HTTP 429 on base-image pulls) → `docker compose build` unverified; Caddy/TLS unverified |
| 10 UI checked functionally and visually | verified | `scripts/ui_check.py` (Playwright/Chromium) against the live server after `./setup.sh --demo`; screenshots in `docs/screenshots/` reviewed |
| 11 No fabricated results | upheld | every number in docs comes from a command output in this session; unmeasured items are labelled |

Gate 2 items: incremental update/delete/dedup/retry — verified; failed/stale state + retry — verified;
permission regression tests (search, snippets, passages, source/download, listing, status, admin) —
verified (no cache exists, so cache leakage is N/A); evaluation set + metrics — verified; latency —
measured on fixtures; backup/restore/reindex docs — written, **restore not rehearsed**; health checks —
implemented and exercised (`/healthz`, `/readyz`, `/api/status`, `/api/admin/status`).

## 4. Measured results (fictional corpus — smoke-level evidence only)

* Retrieval (answerable, doc-level): Hit@1 1.0, Recall@5 1.0, MRR 1.0 for hybrid; lexical-only Hit@1
  0.969. Passage Hit@1: dense-only 0.938 > full 0.875 (title weighting; see EVALUATION.md).
* Abstention, held-out test: false-answer rate 1/12, answer coverage 9/16, precision among answered 0.9,
  conflict detected 1/1, restricted leaks 0.
* Latency: in-process warm p50 10 ms; HTTP single API process: c=1 p50 14.5 ms / p95 26.5 ms,
  ~60–75 req/s saturating 4 shared vCPUs, c=16 p95 261 ms, 0 errors. `--workers 3` gave no gain on
  this machine (CPU-bound). Full table in EVALUATION.md.
* Model comparison and reranker experiment: EVALUATION.md.

## 5. UI verification and one-command setup

* `scripts/ui_check.py` (headless Chromium) passed every step: wrong password shows an error; login;
  scope chips list only readable collections; exact search → *evidence found* (exact identifier badge);
  passage drawer shows the cited section with context; **Open source** opens the original file in a new
  tab; conceptual query about the PDF → abstains with the correct page as top lower-relevance match;
  unrelated query → *no adequate evidence*; per-diem query → *conflicting evidence* with both versions;
  upload → worker indexes it → delete; status tab; 390 px width has no horizontal overflow; logout.
  Console errors: only the expected 401s (pre-login `/api/me`, wrong password) and a 404 (no favicon).
  Screenshots reviewed visually: `docs/screenshots/`.
* `./setup.sh --demo` verified from an empty database volume (15 s with model cached; idempotent re-run
  reports all documents `unchanged`). Demo permissions verified (bob: no HR evidence; hannah: HR policy).
* `setup.bat` → `setup.ps1` (Windows) written but **not run** (no Windows/PowerShell available).

## 6. Explored paths and their outcome

| Path | Outcome |
|---|---|
| Existing products (Onyx, RAGFlow, R2R) as base | Rejected for v1 (operational surface, LLM-centric); Onyx remains the reference for connectors/permission sync |
| .NET control plane + Python ML service | Deferred: no .NET SDK available; single Python process is simpler. Contract (JSON API) allows a later split |
| Qdrant / OpenSearch vs PostgreSQL | PostgreSQL+pgvector chosen (one store, transactional ACL/versioning). Not benchmarked against Qdrant — only justified at larger scale |
| Embedding models: bge-small, arctic-embed-s, granite-small-r2 | Measured; bge-small shipped. Qwen3-Embedding-0.6B (fastembed ONNX, 1.1 GB) **not tried** |
| Cross-encoder rerankers (MiniLM-L6, bge-reranker-base) as evidence gate | Measured; +25–31 pts coverage, no false-answer gain, +106/+658 ms CPU → deferred |
| ONNX thread setting (`KS_EMBED_THREADS=1` vs default) | Measured; no throughput gain |
| Docker image build | Blocked by registry rate limit (429) in the cloud env |
| GPU | Not available; nothing verified |

## 7. Unexplored / next steps (highest value first)

1. **Run on real documents** (even 50–200 files) and write 50+ real queries with labels; re-run
   `eval/run_eval.py`; re-tune gate thresholds on a val split; keep a test split untouched.
2. **Verify `docker compose build` / `up`** on the local machine; then rehearse backup → restore
   (`pg_dump` + blobs) and record it.
3. Abstention coverage: with real data, re-test the MiniLM reranker gate and a lower title weight in
   the lexical branch (passage Hit@1 regression). Adopt only on measured gain.
4. Formats demanded by the real corpus: Office (`python-docx`), then Docling for complex PDFs/tables/OCR.
5. Identity: OIDC login mapping IdP groups → `groups` once the provider is known.
6. Gate 3 (one at a time, only with a concrete use case): authenticated MCP tool or a thin REST
   `/api/search` client for an external agent workflow (embedding → retrieval → rerank → LLM summary is
   possible *outside* this app today by calling `/api/search`); optional local generation with citation
   validation; graph retrieval only with labelled multi-hop queries.
7. Scale: HNSW + iterative scans past ~10⁵–10⁶ chunks, measured against exact KNN recall.

Not to do before the above (per brief): GraphRAG, generation, plugin system, agent harness, multiple
search engines, Kubernetes, trajectory replay.

## 8. Useful commands / gotchas

* Dev DB: `docker compose up -d db` (pgvector/pgvector:0.8.1-pg17 on 127.0.0.1:55432, user/pass `ks/devpass`).
* Tests drop/recreate database `ks_test`; eval drops/recreates `ks_eval`. They never touch `ks`.
* Model cache defaults to `./data/models`; tests and eval use offline mode against it.
* Server restarts: don't `pkill -f "knowledge-search serve"` from a shell whose command line contains
  that string (it kills itself).
* UI check: `pip install playwright && playwright install chromium`, start the demo server, then
  `python scripts/ui_check.py docs/screenshots` (set `CHROMIUM_PATH` to use a preinstalled Chromium).
* Gate thresholds are **model-specific** (`KS_GATE_*`); changing `KS_EMBED_MODEL` requires reindex
  **and** re-tuning.
