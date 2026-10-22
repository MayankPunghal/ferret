# Architecture, decisions and source register

## Overview

```
 Browser UI (static JS) ──┐        external agent / script (bearer token) ──┐
                          ▼                                                  ▼
               ┌──────────────────────── FastAPI process ───────────────────────────┐
               │ auth (sessions, groups→collections)   /api/search  /api/documents  │
               │ search.py: lexical ─┐                                              │
               │            dense  ──┼─ RRF ─ evidence gate ─ outcome + citations   │
               │            exact  ──┘                                              │
               │ embedder.py (fastembed / ONNX Runtime, CPU)                        │
               │ [optional in-process ingestion worker threads]                     │
               └───────────────┬────────────────────────────────────────────────────┘
                               │ SQL (every query filtered by readable collection ids)
          ┌────────────────────▼───────────────────────┐     ┌──────────────────────────┐
          │ PostgreSQL 17 + pgvector 0.8.1             │     │ KS_DATA_DIR/blobs/<sha>  │
          │ users/groups/grants · documents/versions   │     │ original files (content- │
          │ chunks(tsvector, vector(384), idents[])    │     │ addressed, GC on delete) │
          │ jobs (SKIP LOCKED queue) · audit_events    │     └──────────────────────────┘
          └────────────────────▲───────────────────────┘
                               │
               `knowledge-search worker` process(es): claim job → parse → chunk → embed →
               atomic chunk swap (same transaction marks the version indexed)
```

**Data flow (ingest):** upload → validate type/size → store blob (SHA-256) → if SHA equals latest
version: `unchanged`, stop → new `document_versions` row + job (older queued jobs superseded) →
worker parses (Markdown front matter + heading anchors / PDF page anchors / text), chunks
(~900 chars, paragraph-aware, 150 overlap), embeds `title — heading path\npassage` → in one transaction:
delete document's chunks, insert new ones, set `indexed_version_id`. If a newer upload arrived meanwhile
the job is marked `superseded` and discarded. Parse errors fail permanently with a code; embedding /
storage errors retry with exponential backoff (3 attempts).

**Data flow (query):** principal → readable collection ids (∩ optional user filter) → coverage counts for
that scope → three branches (each ≤50 candidates) → RRF (k=60) → top N with signals → evidence gate →
outcome. Trust boundary: everything inside the database query is already permission-filtered; documents
are untrusted content (served as `text/plain` or PDF with `CSP: sandbox`, rendered with `textContent`).

## Decisions (ADR-style, short)

| # | Decision | Candidates compared | Why | Revisit when |
|---|----------|--------------------|-----|--------------|
| 1 | Focused custom app, not an existing product (Onyx/RAGFlow/R2R) | Onyx, R2R, custom | Products bring many services, LLM-centric flows and their own permission models; the brief needs retrieval-only, explicit abstention states and auditable ACLs. A ~1.5k-line app is easier to verify. | If connectors (Confluence, SharePoint…) become the main need — Onyx's connectors are the strongest reason to reconsider. |
| 2 | Python (FastAPI) single process, no .NET service | ASP.NET Core + Python ML sidecar; Python only | No .NET SDK in the environment; the ML runtime (fastembed/ONNX) is Python; one process avoids a network contract and a second deployable. | If the team mandates .NET for the control plane: keep `search`/`embedder` as the Python data plane behind the same JSON API. |
| 3 | PostgreSQL + pgvector + PG full-text as the **only** store | Qdrant, OpenSearch, PG+pgvector | ACL filtering, versions, jobs, FTS and vectors in one transactional store; delete/update cannot leave a second index stale; one backup. Exact KNN (no ANN index) gives exact recall at this scale. | >~10⁶ chunks or p95 regressions: add HNSW + `hnsw.iterative_scan` and measure filtered recall; Qdrant if hybrid/sparse features are needed beyond PG. |
| 4 | Embedding: `BAAI/bge-small-en-v1.5` via fastembed (ONNX, int8-quantized export) | bge-small (MIT), snowflake-arctic-embed-s (Apache-2.0), granite-embedding-small-english-r2 (Apache-2.0) | All three hit ceiling retrieval on fixtures; bge-small had the lowest held-out false-answer rate with usable coverage, smallest download (~67 MB), fastest load. Granite's similarity scale admitted no zero-false-answer threshold on val. | On real data: re-run `eval/run_eval.py` with candidates incl. Qwen3-Embedding-0.6B (fastembed supports an ONNX export; 1.1 GB, slower on CPU). |
| 5 | Parsers: pypdf (text layer) + markdown-it-py + plain text | pypdf, Docling, Apache Tika | Pure-Python, permissive licences, no JVM/torch; page anchors are enough for source navigation. Docling (layout/tables/OCR) pulls torch and was out of first-release scope. | Real corpus has complex PDFs/tables/scans or Office files → evaluate Docling first. |
| 6 | Lexical: PostgreSQL `english` FTS with OR-semantics + weighted title/heading/body, **plus** an exact identifier branch | PG FTS only; BM25 extension (ParadeDB/pg_search) | PG FTS ANDs terms by default (bad for questions) and its stemmer mangles codes like `ERR-PAY-4012`; an identifier array + GIN index makes exact lookups deterministic. No extra extension to operate. | If lexical quality is the bottleneck on real data, evaluate true BM25 (pg_search) — measure first. |
| 7 | Fusion: RRF (k=60) | RRF, weighted score blend | Scores are uncalibrated across branches; RRF needs no tuning. | When labelled real data exists, compare a tuned blend. |
| 8 | Evidence gate on deterministic signals; no reranker in product | cosine/lexical gate; MiniLM-L6 cross-encoder; bge-reranker-base | Measured on identical candidates: rerankers raised held-out coverage (56%→81–88%) but did **not** lower false answers (2/12 vs 1/12) and add 106 ms / 680 ms CPU per query. Preferring lower false positives and simplicity, deferred. | First experiment on a real labelled set; also consider reranker only as a tie-breaker for the gate. |
| 9 | Durable job queue in PostgreSQL (`SKIP LOCKED`) | Postgres queue, Redis/RQ, Celery | No broker to run; transactional with document state; supports N workers. | Only if job volume makes polling a measurable load. |
| 10 | Local accounts + opaque sessions (argon2id) with groups→collection grants | Local accounts, OIDC now | No SSO provider is known; local auth makes laptop and server identical. OIDC can map IdP groups onto the same `groups` table. | When the company IdP is known: add OIDC login that upserts users/groups. |
| 11 | Static UI (vanilla ES modules, no build) | React+TS, server templates, vanilla | Five screens; no toolchain to maintain; strict CSP (`script-src 'self'`). | If the UI grows (saved searches, admin console) move to React+TS. |
| 12 | Outcomes instead of confidence | numeric score, reason codes | Similarity is not a probability; the brief forbids invented confidence. Absence is only asserted when scope coverage is complete. | Only expose numbers after calibration on a large held-out set. |

Restricted-scope behaviour: the brief lists "access restrictions prevent use of required evidence" as an
outcome. Reporting it would reveal that restricted evidence exists, so the system never derives any state
from documents the caller cannot read; such queries look exactly like `no_adequate_evidence`. A filter to
a collection the user cannot read behaves exactly like a non-existent collection (`no_searchable_scope`).

## Source register (consulted for decisions)

* pgvector 0.8 — filtering, iterative index scans, exact vs approximate search: https://github.com/pgvector/pgvector
* PostgreSQL full-text search (`to_tsquery`, `ts_rank_cd`, `ts_headline`): https://www.postgresql.org/docs/17/textsearch.html
* Qdrant hybrid queries / RRF as safe default: https://qdrant.tech/documentation/search/hybrid-queries/
* fastembed supported models list (runtime-verified with `TextEmbedding.list_supported_models()`): https://github.com/qdrant/fastembed
* Model cards/licences: https://huggingface.co/BAAI/bge-small-en-v1.5 (MIT) · https://huggingface.co/Snowflake/snowflake-arctic-embed-s (Apache-2.0) · https://huggingface.co/ibm-granite/granite-embedding-small-english-r2 (Apache-2.0) · rerankers: https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2 (Apache-2.0), https://huggingface.co/BAAI/bge-reranker-base (MIT). Licences above are as reported by fastembed's registry/PyPI metadata; re-check the model cards before a commercial decision.
* pypdf (BSD-3-Clause): https://github.com/py-pdf/pypdf · Docling (deferred): https://github.com/docling-project/docling
* OWASP RAG Security Cheat Sheet (permission filtering before ranking, untrusted content): https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html
* Anthropic Contextual Retrieval (cheap heading/title prefix baseline used here instead of LLM-generated context): https://www.anthropic.com/research/contextual-retrieval
* Research brief: [docs/brief/](brief/final_enterprise_retrieval_research_and_master_prompt.md)

Unresolved questions: real corpus formats/size/languages; identity provider; whether connectors
(wiki/SharePoint/Git) are required; acceptable answer coverage vs false-answer rate; GPU availability.
