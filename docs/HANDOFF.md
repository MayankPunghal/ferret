# Handoff — Knowledge Search

State at the end of the cloud session (2026-10-22). Read with `CLAUDE.md` (owner instructions and end
goal), `README.md`, and the docs listed there. Scope authority: `docs/brief/…master_prompt.md` §11.

## 1. Resume locally

```bash
git clone https://github.com/MayankPunghal/knowledge-search && cd knowledge-search
./setup.sh --demo --no-start          # Windows: setup.bat -Demo -NoStart
uv sync                               # adds dev tools
uv run pytest -q                      # expect 41 passed (embedded PostgreSQL; no Docker)
uv run --group eval python eval/run_eval.py --reranker Xenova/ms-marco-MiniLM-L-6-v2
./start.sh                            # or start.bat → http://127.0.0.1:8000
```

## 2. What the product is now

Offline knowledge search for technical teams: upload Markdown/text/PDF into permissioned collections;
hybrid retrieval (PostgreSQL full-text + local embeddings + exact identifiers, RRF, optional local
reranker); every search returns ranked passages with source links **and an explicit evidence outcome**
(`evidence_found`, `conflicting_evidence`, `no_adequate_evidence`, `index_coverage_incomplete`,
`no_searchable_scope`, `retrieval_degraded`, `retrieval_failure`). One-click setup with an embedded
PostgreSQL (no Docker). Admin UI for models, chunking, reranker, thresholds, users and access.
No LLM generation yet (deliberately — see §7).

Code map: `src/knowledge_search/` — `api.py` (routes), `admin.py` (admin routes), `search.py`
(retrieval + gate), `ingest.py` (versions, worker, progress), `parsing.py`, `embedder.py` (model
registry + per-model thresholds), `runtime.py` (settings, model cache, reranker), `vectors.py`
(in-memory vector search), `auth.py`, `db.py` (embedded PG, migrations, Windows DLL fix), `cli.py`,
`static/` (UI), `graph.py` (link graph), `migrations/0001-0004`. Tests in `tests/`, evaluation in `eval/`.

## 3. Verification status (what was actually run)

| Item | Status | Evidence |
|---|---|---|
| Fresh install with shipped defaults (bge-base + MiniLM reranker, weighted fusion) | **verified** on Linux: `setup.sh --demo` into an empty data dir, `start.sh`, full UI walkthrough, screenshots regenerated | `docs/screenshots/` |
| Test suite | **verified** | `uv run pytest -q` → 41 passed (search, link graph + link-aware search, permissions, lifecycle, admin/settings/reindex/reranker, in-memory vs pgvector parity, DB-failure 503, embedded-DB helpers) |
| Linux setup | **verified** (`setup.ps1` logic run end-to-end under PowerShell 7 on Linux; CLI setup/demo/serve on Linux) | session logs |
| Windows setup | **partially verified by the owner** on Windows 11: install, model download, DB init and indexing worked after the DLL fix; searches then crashed the embedded DB (pgvector distance functions on Windows) → fixed with in-memory vector search (commit 7ec7a93). **That fix is not yet verified on Windows.** | owner logs |
| UI walkthrough | **verified** (Playwright/Chromium, `scripts/ui_check.py`): first-run admin, settings save + reindex, collections/grants/users, multi-file upload incl. rejection, clean state after sign-out/in, search outcomes, passage drawer, source open, mobile width | `docs/screenshots/` |
| Server profile (web + separate worker, external PG, concurrency) | verified natively earlier (0 errors at c=16, ~60–75 req/s on 4 vCPU, bge-small). Docker image build **not verified** (registry rate limits) | docs/evaluation.md |
| GPU | **not available, not implemented** | — |
| Backup/restore | documented, **not rehearsed** | docs/deployment.md |

## 4. Measured results (tables and caveats in docs/evaluation.md)

- **Embedding models, BEIR SciFact subset (2,000 abstracts, 300 human-labelled queries), CPU:**
  bge-small nDCG@10 0.779 (8.3 docs/s, query 6.6 ms) · **bge-base 0.818** (3.3 docs/s, 23 ms) ·
  arctic-embed-m 0.814 (3.7 docs/s, 22 ms). nomic-v1.5 was OOM-killed (now bounded by batch/token caps;
  not re-measured). Qwen3-Embedding-0.6B **not measured** (too slow for the session on this CPU).
- **Fusion:** equal-weight RRF hurt natural-language retrieval (0.746 vs dense 0.815); keyword weight
  0.1 restores 0.794 and keeps identifiers perfect on fixtures → default.
- **Rerankers (top-10, real pipeline):** MiniLM 0.795 → 0.809 at ~0.4 s (default on); bge-reranker-base
  0.812 at ~1.9 s (selectable). Neither improves abstention on hard negatives.
- **Abstention at scale (SciFact, 122 hard-negative unanswerable / 150 answerable):** no gate reaches
  ≤5–10% false answers; best trade-offs ≈ 15% false answers at ~47–51% coverage. Similarity/rerankers
  measure relevance, not support → wording changed to "Relevant passages found"; real claim verification
  is part of the planned answer-generation stage.
- Fixtures (12 docs, 56 queries): hybrid ranking at ceiling; similarity gate test false-answer 1/12 at
  56–63% coverage; reranker gates 81% coverage with 2/12 false answers.

## 4a. Windows setup — latest report (2026-10-23)

Owner's fresh clone on Windows 11: dependencies and embedded DB installed; **model download failed** with
`WinError 10054/10038` (connection reset / "not a socket") from Hugging Face's parallel *xet* transfer, and
fastembed's 3 quick retries gave up (the file then finished in the background). Fix (commit after
16ed591…): `HF_HUB_DISABLE_XET=1` + longer timeouts set in `knowledge_search/__init__.py`, our own retry
with backoff for network-type errors (`download.py`, 5 attempts, tested), a plain-language error in setup,
and `setup.bat --demo/--no-start/...` flag support. Verified on Linux (fresh cache download in 20 s);
**not yet re-verified on Windows** — next step for the owner: `git pull` then `setup.bat --demo`.

**Update:** owner confirmed on Windows 11 — setup completes and search returns results (the
download and pgvector fixes work). Remaining console noise (`ConnectionResetError` in
`_call_connection_lost`, a harmless CPython/Windows quirk) is now filtered, and an admin **Logs** tab
(diagnostics + errors with tracebacks, "Copy for support", file `data/logs/knowledge-search.log`) makes
future reports easy.

## 4b. Graph navigation v1 (latest session)

Implemented and verified (tests + Playwright walkthrough, screenshot `docs/screenshots/11-connections.png`):
deterministic links from Markdown links, `[[wiki links]]` and front-matter relations → `doc_links`
(migration 0004); `GET /api/documents/{id}/links` and `/impact?depth=1..3`; Connections panel in the
passage drawer; permission filtering on every hop (tested); demo wiki `fixtures/corpus/platform-wiki/`.
Details and what is still open: `docs/research-graph.md`. **Not evaluated yet** for answer quality.

## 4c. Link-aware search (graph-assisted retrieval, Gate 3 item)

Search now follows the links of the page a question names (`search._linked`, setting `graph_expansion`,
on by default, badge "linked from …"). Measured on a labelled synthetic interlinked wiki
(`eval/make_wiki.py`, `eval/graph_eval.py`; docs/evaluation.md §6): multi-hop evidence recall
dependencies 0.24 → 0.60, workflows 0.36 → 0.57, impact 0.23 → 0.40; single-hop unchanged (0.96);
0 restricted passages; fixture eval identical; +15 ms. Verified: tests (incl. permission test and
pgvector/in-memory parity), Playwright walkthrough (screenshot `13-linked-search.png`). Not verified on a
real corpus. Also found: the evidence gate rejects most correct answers on terse wiki sections
(similarity 0.6–0.7 vs threshold 0.72) — re-tune thresholds on real data.

## 5. Decisions made this session (details: docs/architecture.md)

Embedded PostgreSQL + vendored Windows DLL; in-memory vector search for laptops; runtime-editable
settings with index signatures (outdated documents are reported, never silently mixed); per-model tuned
thresholds; reranker optional and off unless it measurably helps; first-run admin in the browser;
auto-migrations on start; GraphRAG deferred in favour of a deterministic link-graph research track.

## 6. Explored vs unexplored

| Explored (outcome) | Unexplored / open |
|---|---|
| Existing products (Onyx/RAGFlow/R2R) — rejected for v1 | Connectors (Confluence, SharePoint, Git, Jira) |
| Docker-based laptop install — replaced (failed on the owner's Windows) | OIDC / company SSO |
| Embedded PG on Windows — two platform bugs found and fixed (DLL, pgvector distance) | Verify the Windows search fix; Windows service install (NSSM) |
| 6 embedding models screened (3 measured on SciFact) | Qwen3-Embedding-0.6B, bge-large, mxbai-large; multilingual corpora |
| 3 rerankers (MiniLM measured harmful on technical text) | Rerankers on the owner's real documents |
| Gate families: similarity, IDF-weighted, reranker, reranker+similarity | Calibration on real labelled data |
| Concurrency (single/multi process) | Large corpora (10⁵+ chunks), HNSW |
| — | Office formats, OCR, Docling for complex PDFs |
| Graph navigation v1 (links, impact, Connections UI) + link-aware search, evaluated on a synthetic wiki | Graph: real-corpus multi-hop evaluation, shared-identifier/entity edges, Graphify import, MCP tools |
| — | **Local LLM answers with citations ("second brain")** |

## 7. Next steps (highest value first)

1. **Owner retry on Windows** (`git pull`, `start.bat`): confirm search works.
2. **Real-corpus evaluation:** index 200–1,000 real technical documents (e.g. a Graphify /
   codebase-documenter wiki + runbooks); write 50–100 labelled questions including unanswerable ones;
   run the gate tuning (`eval/gates.py`) on them; store per-model thresholds in `embedder.py`.
3. **Graph navigation, next increments** (docs/research-graph.md): run `eval/graph_eval.py`-style questions on
   a real Graphify/codebase-documenter wiki; shared-identifier (entity) edges; REST/MCP tools (`search`,
   `get_passage`, `get_links`, `impact`) for agent navigation.
4. **Second brain:** local LLM (Ollama/llama.cpp via an OpenAI-compatible endpoint, opt-in) answering only
   from retrieved passages with checked citations; abstains on `no_adequate_evidence`; surfaces conflicts.
   Expose `search`/`get_passage`/`get_links` as REST/MCP tools for agent navigation.
5. Office/Docling parsing, OIDC, Docker build verification, backup/restore rehearsal.

## 8. Gotchas

- `KS_DATABASE_URL` empty = embedded PG in `data/pg`; set it for external servers (vector search then runs
  in pgvector unless `KS_VECTOR_SEARCH=memory`).
- Changing the model or chunking marks documents outdated until reindexed (Settings → Reindex).
- Thresholds are per model (`ModelSpec.gate_high/gate_min`); untuned models show a warning in Settings.
- Never `pkill -f "knowledge-search serve"` from a shell command line that contains that text.
- The cloud environment rewrote the literal `NUL` in shell commands; Windows scripts were written with the
  editor tool and `.gitattributes` forces CRLF for `.bat/.ps1`.
- The embedded PG refuses to run as root on Linux; the package creates a `pgserver` user automatically.
