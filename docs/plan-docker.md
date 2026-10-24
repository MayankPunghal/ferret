# Plan: bring the Docker setup up to date with the app (handoff, 2026-10-24)

`Dockerfile` and `docker-compose.yml` were written before the local model, the reranker choice, the Settings
download buttons and the Activity view existed. Below is what they miss, checked by reading the files.
**Nothing was built:** this cloud container has the Docker client but no Docker daemon. Every point must be
tried with a real build, locally or in CI.

## Gaps

| # | Gap | Effect | Fix |
|---|---|---|---|
| 1 | The image bakes and selects **bge-small** (`ARG KS_EMBED_MODEL=BAAI/bge-small-en-v1.5`); the app default is **bge-base** | a Docker install searches less accurately than a laptop install, and its vectors differ | drop the `ARG`/`ENV` override so the image uses the app default; `download-model` already bakes the default reranker too |
| 2 | **No local model for summaries.** `KS_OFFLINE=1` blocks the first-use download of `llama-server` and the GGUF, and `/models` is read-only for the `ks` user | Summaries, types and topics never appear in Docker. The Settings **Download** buttons fail. | Two image tags: `:latest` without the local model (small), and `:full` with `ferret download-llm` run at build time (≈ +3.5 GB, unmeasured). Models live in a volume (`ks-models:/models`), seeded from the image on first start, so the Settings buttons work. |
| 3 | `llama-server` is the **Ubuntu** build of llama.cpp | It may need shared libraries missing from `python:3.13-slim` (for example `libgomp1`, `libcurl4`). Unverified. | `RUN apt-get install -y --no-install-recommends libgomp1 libcurl4` if the self-test (below) shows missing libraries; check with `ldd` in the build |
| 4 | The **worker** container runs the summariser (`_worker` starts `Enricher`); the app container does not | correct split, but it is not documented, and the model and server must be in the worker container | document it; give the worker the `ks-models` volume and the memory note below |
| 5 | **Memory:** compose sets no limits | Gemma 4 E2B needs about 3–4 GB in the worker, plus the embedding model in both containers | set `mem_limit` in compose comments with the measured figure (measure first), and a minimum host size in docs/deployment.md |
| 6 | **Single container** (no separate database) is not offered | A small team must run three containers plus PostgreSQL. The app can run its own embedded PostgreSQL in `/data/pg` (as on a laptop; it runs as non-root uid 10001, which PostgreSQL requires). | document `docker run -p 8000:8000 -v ks-data:/data -v ks-models:/models ghcr.io/mayankpunghal/knowledge-search`; healthcheck already uses `/healthz` |
| 7 | **No published image,** no versions | everyone builds locally | GitHub Actions on a `v*` tag: build `linux/amd64` and `linux/arm64`, run the self-test, push to `ghcr.io/mayankpunghal/knowledge-search:<version>` and `:latest`, with an SBOM and a vulnerability scan (Trivy) as a report, not a blocker at first |
| 8 | **Database image** `pgvector/pgvector:0.8.1-pg17` vs the embedded PostgreSQL | must stay the same major PostgreSQL version and a pgvector version with the same features, or backups made in one cannot be restored in the other | pin both; `scripts/backup_restore_check.sh` runs in CI against the compose database too |
| 9 | **Backups** in Docker | docs/deployment.md describes them, but nothing tests them in containers | CI job: compose up, load sample data, `pg_dump` + copy `/data/blobs`, restore into a fresh stack, search again |
| 10 | `uv` pinned at 0.11.32 in the Dockerfile | matches the uv that wrote `uv.lock` today (checked), so not a gap now | bump the Dockerfile pin whenever the lock is rewritten by a newer uv |
| 11 | `KS_COOKIE_SECURE=false` default in compose | sessions over plain HTTP on a network | keep `false` for `127.0.0.1` only; the `tls` profile sets `true` (Caddy). Say so in the compose header. |

## Self-test inside the image (CI, before pushing)

A new command `ferret selftest` (holler has the same idea), run as
`docker run --rm <image> ferret selftest`:
- starts the embedded PostgreSQL in a temporary folder;
- indexes the 16 sample documents;
- runs 3 known searches and checks the expected document is first, and that one off-topic question is
  refused;
- for `:full`, starts `llama-server` and writes one summary;
- exits 0 or 1.

The same command is used by the Windows installer test (docs/plan-installer.md).

## Order

1. Fix gaps 1, 2 and 3, and add `selftest`. Build and run locally; record the image sizes and the
   time from `docker run` to the first search in docs/deployment.md.
2. Single-container instructions (6), compose memory notes (4, 5).
3. CI build, test and publish (7), then the backup check in CI (9).
