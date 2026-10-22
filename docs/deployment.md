# Deployment

One codebase, two supported profiles. GPU is optional and not yet implemented.

| | Laptop / single user | Dedicated CPU server (multi-user) |
|---|---|---|
| Install | `setup.bat` / `./setup.sh` | Docker Compose (below) or the same setup script + a service manager |
| Database | embedded PostgreSQL in `data/pg` | external PostgreSQL 16+ with pgvector (container or managed) |
| Indexing | thread inside the app | separate `knowledge-search worker` process(es) |
| Access | <http://127.0.0.1:8000> | HTTPS via reverse proxy (Caddy config included) |

## Laptop

Covered in [Getting started](getting-started.md). The app listens on `127.0.0.1` only. To let colleagues
reach it, prefer the server profile.

## Dedicated server with Docker Compose

```bash
cp .env.example .env        # set KS_DB_PASSWORD, KS_HOSTNAME, KS_COOKIE_SECURE=true
docker compose --profile tls up -d --build
```

Services: `db` (PostgreSQL 17 + pgvector, volume `ks-pg`), `app` (web + API, `KS_INPROCESS_WORKERS=0`),
`worker` (indexing; scale with `--scale worker=N` — jobs are claimed with `SKIP LOCKED`, never twice),
`caddy` (TLS on 443, profile `tls`, `deploy/Caddyfile`). The image bakes in the default models and runs
with `KS_OFFLINE=1`. Then open `https://<host>/` **from the server itself** to create the admin, or run
`docker compose exec app /app/.venv/bin/knowledge-search create-user admin --admin --password -`.

Verification status: the server topology (separate web and worker processes, PostgreSQL container,
multi-user HTTP, concurrency test) was verified natively; the Docker image build was not (registry rate
limits in the development environment). Run `docker compose build` before relying on it.

## Server without Docker

Use the setup script, set `KS_DATABASE_URL` to your PostgreSQL (with `CREATE EXTENSION vector` allowed),
`KS_INPROCESS_WORKERS=0`, then run two services (systemd / NSSM on Windows):

```bash
uv run --no-dev knowledge-search serve --host 127.0.0.1 --port 8000 --workers 2
uv run --no-dev knowledge-search worker
```

and put a TLS reverse proxy in front.

## Sizing (measured on 4 vCPU / 15 GB, no GPU — see [Evaluation](evaluation.md))

- Query latency is dominated by query embedding and (if enabled) reranking; both scale with model size.
- Indexing throughput depends heavily on model size and document length; large models index ~10× slower
  than small ones. Reindex after a model change can take hours for large corpora on CPU.
- Exact vector search is used (no ANN index): fine to roughly 10⁵–10⁶ passages per server. Beyond that,
  add an HNSW index with iterative scans and re-measure recall.

## Backups and restore

Back up **both** the database and the original files:

```bash
# external PostgreSQL
pg_dump -Fc "$KS_DATABASE_URL" > ks.dump
# embedded PostgreSQL (app stopped): copy the whole data directory
cp -a data/ backup-$(date +%F)/
```

Restore: `pg_restore -d <empty db> ks.dump` and put `data/blobs/` back (or copy the data directory back).
Passages and vectors are in the database; no reindex is needed unless the model changes.
(Procedure documented, not yet rehearsed — see Handoff.)

## Upgrades

`git pull` (or download the new release), re-run the setup script (applies database migrations). Migrations
are forward-only SQL in `src/knowledge_search/migrations/`.

## GPU (not implemented)

Planned shape: a `KS_DEVICE` setting that loads ONNX Runtime's CUDA provider (`fastembed-gpu`) for the same
models — same vectors, no reindex — with automatic CPU fallback reported on the status page. Not verified:
no GPU was available during development.
