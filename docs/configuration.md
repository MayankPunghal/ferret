# Configuration

Process-level settings are environment variables with the `KS_` prefix, or lines in a `.env` file in the
project folder (see `.env.example`). Search/indexing behaviour (model, chunking, reranker, thresholds) is
changed by administrators in the **Settings** page; the variables below only provide the *defaults* for
those.

## Storage and runtime

| Variable | Default | Meaning |
|---|---|---|
| `KS_DATABASE_URL` | *(empty)* | Empty = embedded PostgreSQL under `KS_DATA_DIR/pg`. Set to use an external PostgreSQL with pgvector, e.g. `postgresql://ks:secret@db:5432/ks`. |
| `KS_READONLY_DATABASE_URL` | *(empty)* | For Admin → Database → SQL with an external database: the URL of a role that can only read (no access to `users.password_hash` or `sessions.token_hash`). With the embedded database the app creates such a role (`ks_reader`) itself. |
| `KS_DATA_DIR` | `./data` | Original files (`blobs/`) and the embedded database (`pg/`). Back this up. |
| `KS_MODEL_CACHE` | `./data/models` | Downloaded model files. |
| `KS_OFFLINE` | `false` | `true` = never download models at runtime (air-gapped servers; pre-fill the cache). |
| `KS_EMBED_THREADS` | `0` | ONNX Runtime threads per model (0 = automatic). |
| `KS_VECTOR_SEARCH` | `auto` | Where vector similarity is computed: `memory` (in the app process), `database` (pgvector in SQL), `auto` = memory with the embedded database, database with an external server. Memory mode avoids a crash in the Windows build of the embedded pgvector and is fast at laptop scale; servers with large corpora should use `database`. |
| `KS_INPROCESS_WORKERS` | `1` | Indexing threads inside the web process. Use `0` plus `ferret worker` processes on servers. |
| `KS_WORKER_POLL_SECONDS` | `1.0` | Job queue poll interval. |
| `KS_AUTO_REINDEX` | `true` | At startup, queue documents indexed with an older model/chunking for background re-indexing. |

## Limits and sessions

| Variable | Default | Meaning |
|---|---|---|
| `KS_MAX_UPLOAD_MB` | `500` | Maximum upload size (also for folder ingest). |
| `KS_MAX_PDF_PAGES` | `2000` | PDFs with more pages are rejected. |
| `KS_MAX_EXTRACTED_CHARS` | `5000000` | Documents with more extracted text are rejected. |
| `KS_SESSION_HOURS` | `12` | Sign-in lifetime. |
| `KS_COOKIE_SECURE` | `false` | Set `true` when serving over HTTPS. |

## Document summaries (optional local language model)

| Variable | Default | Meaning |
|---|---|---|
| `KS_ENRICH_ENABLED` | `true` | Default of the Settings switch "Summarise each document in the background". While on and no model is installed, the app downloads the default one in the background (not with `KS_OFFLINE=true`). Setup writes `false` to `.env` when you answer No. |
| `KS_LLM_URL` | *(empty)* | Empty = use the model in `KS_MODEL_CACHE/llm` (downloaded by setup, by the app on first start, or by `ferret download-llm`): the app starts llama.cpp's server on a free `127.0.0.1` port and stops it on exit. Set it to use another **local** OpenAI-compatible server instead, e.g. Ollama `http://127.0.0.1:11434/v1` or LM Studio `http://127.0.0.1:1234/v1`. Document text is sent to this address, so never point it at a service outside your control. |
| `KS_LLM_MODEL` | *(empty)* | Model name for `KS_LLM_URL` (e.g. `qwen3.5:0.8b` in Ollama), or the `.gguf` file name in `KS_MODEL_CACHE/llm` to use instead of the one selected by `download-llm`. |
| `KS_LLM_THREADS` | `0` | CPU threads for the installed model (0 = half the cores, so indexing and search keep the rest). |

`ferret download-llm [--model gemma-4-e2b|qwen3.5-0.8b]` downloads llama.cpp's CPU server (release
`b11836`, from github.com) and the model (from huggingface.co) into `KS_MODEL_CACHE/llm`. Gemma 4 E2B
(~3.1 GB, Apache-2.0) is the default: in a blind review it made no wrong claim in 9 summaries, Qwen3.5-0.8B
(~0.5 GB, Apache-2.0, about 3x faster) made 3 (`eval/results/blind-review.json`). Take Qwen only on a machine
with less than ~4 GB free RAM.
Progress is shown on the Index status tab; the server's log is `KS_DATA_DIR/logs/llm-server.log`.

## Defaults for admin-editable settings

| Variable | Default | Settings page field |
|---|---|---|
| `KS_EMBED_MODEL` | see `config.py` | Embedding model |
| `KS_CHUNK_SIZE` / `KS_CHUNK_OVERLAP` | `900` / `150` | Chunk size / overlap (0 disables) |
| `KS_CONTEXT_CHARS` | `0` | Document summary added to each passage's embedding (0 = off; Settings page) |
| `KS_RERANK_ENABLED` / `KS_RERANK_MODEL` / `KS_RERANK_CANDIDATES` | see `config.py` | Reranker |
| `KS_GRAPH_EXPANSION` | `true` | Linked documents: follow links of pages named in the question |
| `KS_GATE_MODE`, `KS_GATE_LEXICAL`, `KS_GATE_DENSE_HIGH`, `KS_GATE_DENSE_MIN`, `KS_GATE_LEX_COVERAGE`, `KS_GATE_RERANK_MIN` | see `config.py` | Evidence thresholds |

Values saved on the Settings page are stored in the database (`app_settings`) and take precedence.

## What is *not* configurable

There are no settings for remote/cloud AI providers, API keys or answer generation: this release never sends
documents or queries to external services. The only language model is the optional local one above, and it
only sees document text, never queries. Model files are downloaded from Hugging Face only during setup
or when an admin selects a model that is not yet cached.
