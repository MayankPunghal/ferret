# Architecture, decisions and sources

## Overview

```
 Browser UI (static ES modules)        external agent / script (Bearer token, REST)
            │                                       │
            ▼                                       ▼
 ┌──────────────────────────── knowledge-search serve (FastAPI) ────────────────────────────┐
 │ deps.py auth: sessions → principal → readable/writable collection ids                    │
 │ api.py  search · documents · passages · sources · status · first-run setup               │
 │ admin.py settings · reindex · collections/grants · users                                  │
 │ search.py: lexical (PG FTS) ─┐                                                            │
 │            dense (vectors) ──┼─ RRF ─ optional reranker ─ evidence gate ─ outcome         │
 │            exact identifiers ┘                                                            │
 │ runtime.py settings + model cache (fastembed / ONNX Runtime, CPU)                         │
 │ vectors.py in-memory exact vector search (laptop profile)                                 │
 │ ingest.py Worker threads (or separate `knowledge-search worker` processes)                │
 └───────────────────────────────┬──────────────────────────────────────────────────────────┘
                                 │ SQL — every query filtered by the caller's collections
             ┌───────────────────▼────────────────────┐   ┌────────────────────────────┐
             │ PostgreSQL + pgvector                  │   │ data/blobs/<sha256>        │
             │ embedded (laptop, data/pg) or external │   │ original files             │
             │ users · groups · grants · documents ·  │   └────────────────────────────┘
             │ versions · chunks · jobs · settings ·  │
             │ audit                                  │
             └────────────────────────────────────────┘
```

**Ingest:** upload → validate → store original by SHA-256 (identical bytes = `unchanged`) → version + job →
worker: parse (Markdown headings/front matter, text, PDF pages) → chunk (size/overlap from settings) →
embed with the active model (`title — headings` prefix) → atomically replace the document's chunks and
record the *index signature* (model + parser + chunking). Progress (stage, N/M chunks) is written to the
job for the UI. Parse errors are permanent; embedding/storage errors retry with backoff.

**Query:** principal → readable collections (∩ user filter) → coverage for that scope (pending, failed,
outdated-signature documents) → three branches → RRF (k=60) → optional cross-encoder rerank of the top N →
evidence gate per result → outcome + reason codes. Restricted documents never enter any branch, statistic
(IDF), count or snippet.

## Decisions

| # | Decision | Alternatives considered | Reason | Revisit when |
|---|---|---|---|---|
| 1 | Focused custom app | Onyx, RAGFlow, R2R | Need retrieval-only mode, explicit abstention states and auditable ACLs; products bring many services and LLM-centric flows. | Connectors (Confluence, SharePoint, Git) become the main need (Onyx). |
| 2 | Python single process (FastAPI) | ASP.NET control plane + Python ML service | ML runtime is Python; one deployable; no .NET in the dev environment. | Org mandates .NET: keep this as the data plane behind the same API. |
| 3 | PostgreSQL + pgvector + full-text as the only store | Qdrant, OpenSearch | Permissions, versions, jobs, FTS and vectors in one transactional store; one backup. | > ~10⁶ chunks or measured latency issues. |
| 4 | **Embedded PostgreSQL** (pip `pixeltable-pgserver`) for laptops; external PostgreSQL for servers | Docker Desktop, SQLite + sqlite-vec | One-click install without Docker/admin rights (Docker failed for the owner's Windows setup); same schema and SQL as servers. Windows wheel needed a vendored `libwinpthread-1.dll` (vendor/win64/SOURCE.md). | Package abandoned or Windows issues persist → ship EDB binaries + prebuilt pgvector. |
| 5 | **In-memory exact vector search** with the embedded DB (`KS_VECTOR_SEARCH=auto`) | pgvector `<=>` everywhere | The Windows embedded pgvector crashes in distance functions; numpy over a cached matrix is exact and fast at laptop scale. Parity test vs pgvector. | Laptop corpora beyond ~10⁵–10⁶ chunks (RAM). |
| 6 | Embedding via fastembed/ONNX with a registry of 9 models; default chosen by measurement (see Evaluation) | sentence-transformers (torch), TEI server | No torch, small install, CPU-friendly, runs offline; model choice is a runtime setting with per-model tuned thresholds and index signatures (no vector-space mixing). | GPU available → fastembed-gpu / TEI. |
| 7 | Parsers: markdown-it, PDFium (pypdfium2; pypdf put one word per line for exported Google Docs); HTML and DOCX converted to Markdown with the standard library (`convert.py`) | Docling, Tika, MarkItDown | Permissive, pure Python, no new dependencies; one heading/anchor/link pipeline for every format. | Complex PDFs, tables, scans, PPTX/XLSX → Docling. |
| 8 | Lexical = PG FTS (OR semantics, weighted title/heading/body) + exact-identifier branch | BM25 extension (pg_search) | No extra extension; identifiers like `ERR-PAY-4012` need exact matching anyway. | Lexical quality proves to be the bottleneck. |
| 9 | Fusion: RRF | weighted blend | Uncalibrated scores across branches. | Large labelled real dataset. |
| 10 | Evidence gate on measurable signals, thresholds tuned on val and reported on held-out test; reranker optional | numeric confidence, LLM self-assessment | Similarity is not a probability; brief forbids invented confidence. | Calibration data on the real corpus. |
| 11 | Durable job queue in PostgreSQL (`SKIP LOCKED`) | Redis/Celery | No broker; transactional with document state. | Measured polling load. |
| 12 | Local accounts, groups → collection grants; first-run admin from localhost | OIDC now | No IdP known yet; identical laptop/server behaviour. | IdP known → OIDC mapping groups. |
| 13 | Static UI without build step | React + TS | Few screens, strict CSP, nothing to build for users. | UI grows substantially. |
| 14 | GraphRAG deferred; deterministic link graph (edge table `doc_links` in PostgreSQL, resolved at query time) + link expansion in search | GraphRAG / LightRAG; graph database (Neo4j) | Brief gates; LLM extraction cost on CPU; one store keeps permissions in every query; see [research-graph.md](research-graph.md). | Real-corpus multi-hop evaluation; entity/identifier edges; corpora beyond ~10⁶ edges. |

Restricted scope: the system never derives any state from documents the caller cannot read, so a query
whose only answer is restricted looks exactly like `no_adequate_evidence`; filtering to an unreadable
collection behaves like a non-existent one (`no_searchable_scope`).

## Licences

Application dependencies: FastAPI (MIT), uvicorn (BSD-3), psycopg/psycopg-pool (LGPL-3.0, unmodified
library), pgvector-python (MIT), fastembed (Apache-2.0), onnxruntime (MIT), pypdfium2 (Apache-2.0 / BSD-3; PDFium BSD-3),
markdown-it-py (MIT), PyYAML (MIT), argon2-cffi (MIT), pydantic-settings (MIT), pixeltable-pgserver
(Apache-2.0; bundles PostgreSQL — PostgreSQL License — and pgvector — PostgreSQL License),
libwinpthread (MIT, vendored for Windows). Models (as listed by fastembed/model cards — re-check before
commercial use): bge-small/base/large-en-v1.5 (MIT), snowflake-arctic-embed-s/m (Apache-2.0),
mxbai-embed-large-v1 (Apache-2.0), nomic-embed-text-v1.5 (Apache-2.0), granite-embedding-small-english-r2
(Apache-2.0), Qwen3-Embedding-0.6B (Apache-2.0); rerankers ms-marco-MiniLM-L-6-v2 (Apache-2.0),
bge-reranker-base (MIT), bge-reranker-v2-m3 (Apache-2.0). Evaluation data (not redistributed): BEIR
SciFact (CC-BY-SA-4.0).

## Sources

- pgvector (filtering, exact vs approximate search): https://github.com/pgvector/pgvector
- PostgreSQL full-text search: https://www.postgresql.org/docs/17/textsearch.html
- pixeltable-pgserver: https://github.com/pixeltable/pixeltable-pgserver
- fastembed supported models: https://github.com/qdrant/fastembed
- BEIR benchmark: https://github.com/beir-cellar/beir · SciFact: https://huggingface.co/datasets/BeIR/scifact
- Qdrant hybrid search / RRF guidance: https://qdrant.tech/documentation/search/hybrid-queries/
- OWASP RAG Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html
- Anthropic Contextual Retrieval: https://www.anthropic.com/research/contextual-retrieval
- MSYS2 libwinpthread package: https://packages.msys2.org/base/mingw-w64-winpthreads
- Research brief: [brief/](brief/final_enterprise_retrieval_research_and_master_prompt.md)
