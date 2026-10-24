# Deployment

One codebase, two supported profiles. GPU is optional and not yet implemented.

| | Laptop / single user | Dedicated CPU server (multi-user) |
|---|---|---|
| Install | `setup.bat` / `./setup.sh` | Docker Compose (below) or the same setup script + a service manager |
| Database | embedded PostgreSQL in `data/pg` | external PostgreSQL 16+ with pgvector (container or managed) |
| Indexing | thread inside the app | separate `ferret worker` process(es) |
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
`docker compose exec app /app/.venv/bin/ferret create-user admin --admin --password -`.

Verification status: the server topology (separate web and worker processes, PostgreSQL container,
multi-user HTTP, concurrency test) was verified natively; the Docker image build was not (registry rate
limits in the development environment). Run `docker compose build` before relying on it.

## Server without Docker

Use the setup script, set `KS_DATABASE_URL` to your PostgreSQL (with `CREATE EXTENSION vector` allowed),
`KS_INPROCESS_WORKERS=0`, then run two services (systemd / NSSM on Windows):

```bash
uv run --no-dev ferret serve --host 127.0.0.1 --port 8000 --workers 2
uv run --no-dev ferret worker
```

and put a TLS reverse proxy in front.

## Sizing (measured on 4 vCPU / 15 GB, no GPU — see [Evaluation](evaluation.md))

- Query latency is dominated by query embedding and (if enabled) reranking; both scale with model size.
- Indexing throughput depends heavily on model size and document length; large models index ~10× slower
  than small ones. Reindex after a model change can take hours for large corpora on CPU.
- Exact vector search is used (no ANN index): fine to roughly 10⁵–10⁶ passages per server. Beyond that,
  add an HNSW index with iterative scans and re-measure recall.

## Large corpora

Example: **40,000 PDFs of a few MB each** (an organisation's archive). Only the first two rows below are
measured; the rest are **estimates** to be confirmed with a scale test before committing hardware.

| Quantity | Estimate | Basis |
|---|---|---|
| Exact vector search, per query, 4 cores | 0.5 ms / 10k · 5 ms / 100k · 39 ms / 500k passages | **measured** (numpy, 768 dims) |
| Embedding throughput, bge-base, 4 vCPU | ~2.4 passages/s (contended run) | **measured** (1,873 docs / 3,573 passages in 25 min) |
| Passages | ~2–10 million (≈ 40 text pages × 3,000 characters per PDF → ~150 passages) | estimate; scanned PDFs have no text and need OCR (not supported) |
| Vector memory | 6 M × 768 × 4 bytes ≈ 18 GB (≈ 9 GB at half precision) | arithmetic |
| Exact search at 6 M passages | ~0.5 s per query on 4 cores | extrapolated from the measured row |
| Initial indexing on CPU | ~2 weeks on 4 vCPU; ~2–3 days on a 32-core server | extrapolated |
| Initial indexing on one GPU | hours | not measured (GPU not implemented yet) |
| Keyword search over 6 M passages | unknown — common words match millions of rows | **must be measured** |

What this means:

- **A laptop is not the right machine** for this archive (memory and indexing time). A dedicated server
  (≥ 64 GB RAM, many cores, ideally one GPU for the initial indexing) with external PostgreSQL is.
- **Accuracy does not have to drop.** Exact search stays affordable to a few million passages (< 1 s).
  Beyond that, an approximate index (pgvector HNSW in the same database, or a dedicated engine such as
  Qdrant) is used only to collect a generous candidate set, which is then **re-scored exactly** with the
  full-precision vectors and the reranker — near-exact recall at a fraction of the cost. Which engine is a
  measured decision (recall@k vs exact, latency, memory, permission filtering, deletes), not a default.
- **Indexing runs in the background** and searches work on what is already indexed (the outcome reports
  incomplete coverage), so the archive can be loaded over days.
- Required before such a rollout: GPU support for embedding, a scale test (1–10 M passages: vector index
  recall/latency, keyword branch latency, ingestion throughput, memory), OCR for scanned PDFs if they
  matter, and evidence-threshold calibration on real questions (a larger corpus has more near-misses).

## Backups and restore

Back up **both** the database and the original files (`data/blobs/`). Rehearsed end to end on a demo
install with `scripts/backup_restore_check.sh`: after each restore the same searches return the same
results and every original file opens.

**Laptop (embedded database).** Stop the app with **Ctrl+C** in its window (the embedded database stops
with it; wait until `data/pg/postmaster.pid` is gone), then copy the whole data folder:

```bash
cp -a data/ backup-$(date +%F)/        # Windows: robocopy data backup-2026-10-23 /E
```

Restore: put the copied folder back as `data/` (or point `KS_DATA_DIR` at it) and start the app.

**Server (external PostgreSQL), while running:**

```bash
pg_dump -Fc "$KS_DATABASE_URL" -f ks.dump          # consistent snapshot, no downtime
cp -a data/blobs/ blobs-backup/                    # original files (content-addressed, append-only)
```

Restore: create an empty database, `pg_restore -d <empty database URL> ks.dump`, put `data/blobs/` back,
start the app. Passages and vectors are in the database, so no re-indexing is needed unless the embedding
model changes.

## Upgrades

`git pull` (or download the new release), re-run the setup script (applies database migrations). Migrations
are forward-only SQL in `src/knowledge_search/migrations/`.

## GPU (not implemented)

Planned shape: a `KS_DEVICE` setting that loads ONNX Runtime's CUDA provider (`fastembed-gpu`) for the same
models — same vectors, no reindex — with automatic CPU fallback reported on the status page. Not verified:
no GPU was available during development.
