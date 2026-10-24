# Development

## Layout

```
src/knowledge_search/
  api.py        HTTP routes (search, documents, passages, sources, status, first-run setup)
  admin.py      admin routes (settings, reindex, collections/grants, users)
  deps.py       request dependencies (DB connection, authenticated principal)
  search.py     hybrid retrieval, RRF fusion, reranking, evidence gate, outcomes
  graph.py      document link graph: links / blast-radius routes (docs/research-graph.md)
  vectors.py    in-memory exact vector search (laptop profile)
  ingest.py     blob store, versioned submit, job worker, reindex, delete
  parsing.py    Markdown/text/PDF parsing, chunking, identifier extraction
  embedder.py   embedding model registry + ONNX adapter
  runtime.py    admin-editable settings, index signature, model cache, reranker
  auth.py       accounts, sessions, groups → collection permissions, audit
  db.py         connection pool, embedded PostgreSQL, migrations
  cli.py        command line (serve, worker, ingest, demo, users, …)
  migrations/   forward-only SQL
  static/       web UI (plain ES modules, no build step)
tests/          integration tests (real PostgreSQL + real models)
eval/           evaluation and benchmark scripts, labelled queries, results
fixtures/       FICTIONAL sample corpus
```

## Setup for development

```bash
uv sync                      # includes dev tools
uv run pytest -q             # starts its own throwaway embedded PostgreSQL; no Docker needed
```

Tests use the real embedding model from `data/models` (run `uv run ferret download-model` once).
To run against an external PostgreSQL set `KS_TEST_ADMIN_URL` and `KS_TEST_DATABASE_URL`.

| Test module | Covers |
|---|---|
| `tests/test_search.py` | exact/conceptual queries, PDF page sources, inert source serving, abstention, conflicts, permission isolation and revocation, CSRF, no outbound network |
| `tests/test_lifecycle.py` | upload → index, unchanged re-upload, versions replace old passages, delete, failures and retries, degraded states |
| `tests/test_admin.py` | first-run setup, settings validation, chunking changes → outdated → reindex, reranker search path, admin-only access |
| `tests/test_graph.py` | link extraction, anchor resolution, incoming links, blast radius, permission safety, delete |
| `tests/test_vectors.py` | in-memory vs pgvector parity, index refresh on delete, database failure → 503 |
| `tests/test_embedded_db.py` | Windows DLL placement, failed-init recovery |

## Evaluation scripts

```bash
uv run --group eval python eval/run_eval.py --reranker Xenova/ms-marco-MiniLM-L-6-v2   # fixture set
uv run --group eval python eval/beir_screen.py --max-docs 2000 --models <m1> <m2> --rerankers <r1>
uv run --group eval python eval/scale_eval.py --model <m> --reranker <r> --max-docs 2000
uv run python eval/bench_concurrency.py --user alice --password demo-password-alice --concurrency 1 4 8
python scripts/ui_check.py docs/screenshots      # Playwright UI walkthrough (needs a running demo server)
```

See [Evaluation](evaluation.md) for what each measures and the latest results.

## API

OpenAPI docs: <http://127.0.0.1:8000/docs>. Authenticate with `POST /api/auth/login` and send
`Authorization: Bearer <token>`; browser sessions use an HttpOnly cookie plus the `x-ks-csrf: 1` header.

```bash
curl -s localhost:8000/api/search -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"query":"ERR-PAY-4012","limit":5}'
```

Each result carries `chunk_id`, `document_id`, `text` (full passage), `snippet`, `heading_path`, `page`,
`source_url`, `passage_url`, `match_basis` and `signals`; the response carries `outcome`, `reason_codes`,
`coverage` and `timings_ms`. External pipelines (e.g. your own LLM summariser) can consume this directly.

## Conventions

Python 3.13, dependencies pinned in `pyproject.toml`/`uv.lock`. Keep it simple (see `CLAUDE.md`): one
process, one database; add components only with a measured reason. Never log document or query text.
