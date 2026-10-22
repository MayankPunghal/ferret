# Configuration

Process-level settings are environment variables with the `KS_` prefix, or lines in a `.env` file in the
project folder (see `.env.example`). Search/indexing behaviour (model, chunking, reranker, thresholds) is
changed by administrators in the **Settings** page; the variables below only provide the *defaults* for
those.

## Storage and runtime

| Variable | Default | Meaning |
|---|---|---|
| `KS_DATABASE_URL` | *(empty)* | Empty = embedded PostgreSQL under `KS_DATA_DIR/pg`. Set to use an external PostgreSQL with pgvector, e.g. `postgresql://ks:secret@db:5432/ks`. |
| `KS_DATA_DIR` | `./data` | Original files (`blobs/`) and the embedded database (`pg/`). Back this up. |
| `KS_MODEL_CACHE` | `./data/models` | Downloaded model files. |
| `KS_OFFLINE` | `false` | `true` = never download models at runtime (air-gapped servers; pre-fill the cache). |
| `KS_EMBED_THREADS` | `0` | ONNX Runtime threads per model (0 = automatic). |
| `KS_VECTOR_SEARCH` | `auto` | Where vector similarity is computed: `memory` (in the app process), `database` (pgvector in SQL), `auto` = memory with the embedded database, database with an external server. Memory mode avoids a crash in the Windows build of the embedded pgvector and is fast at laptop scale; servers with large corpora should use `database`. |
| `KS_INPROCESS_WORKERS` | `1` | Indexing threads inside the web process. Use `0` plus `knowledge-search worker` processes on servers. |
| `KS_WORKER_POLL_SECONDS` | `1.0` | Job queue poll interval. |

## Limits and sessions

| Variable | Default | Meaning |
|---|---|---|
| `KS_MAX_UPLOAD_MB` | `25` | Maximum upload size. |
| `KS_MAX_PDF_PAGES` | `2000` | PDFs with more pages are rejected. |
| `KS_MAX_EXTRACTED_CHARS` | `5000000` | Documents with more extracted text are rejected. |
| `KS_SESSION_HOURS` | `12` | Sign-in lifetime. |
| `KS_COOKIE_SECURE` | `false` | Set `true` when serving over HTTPS. |

## Defaults for admin-editable settings

| Variable | Default | Settings page field |
|---|---|---|
| `KS_EMBED_MODEL` | see `config.py` | Embedding model |
| `KS_CHUNK_SIZE` / `KS_CHUNK_OVERLAP` | `900` / `150` | Chunk size / overlap (0 disables) |
| `KS_RERANK_ENABLED` / `KS_RERANK_MODEL` / `KS_RERANK_CANDIDATES` | see `config.py` | Reranker |
| `KS_GATE_MODE`, `KS_GATE_LEXICAL`, `KS_GATE_DENSE_HIGH`, `KS_GATE_DENSE_MIN`, `KS_GATE_LEX_COVERAGE`, `KS_GATE_RERANK_MIN` | see `config.py` | Evidence thresholds |

Values saved on the Settings page are stored in the database (`app_settings`) and take precedence.

## What is *not* configurable

There are no settings for remote/cloud AI providers, API keys or generation: this release never sends
documents or queries to external services. Model files are downloaded from Hugging Face only during setup
or when an admin selects a model that is not yet cached.
