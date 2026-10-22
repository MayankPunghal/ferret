# Knowledge Search

**CPU-first, retrieval-only enterprise document search that tells you when it has no evidence.**

Upload Markdown, text and PDF files into permissioned collections, search them with hybrid
keyword + semantic + exact-identifier retrieval, and open the exact passage and original source.
No LLM, GPU, cloud API or agent runtime is needed — the application makes **no outbound AI calls**.

- **Hybrid retrieval:** PostgreSQL full-text + local vector search (pgvector) + exact identifier match
  (error codes, API paths, config keys), fused with Reciprocal Rank Fusion.
- **Evidence-first answers:** every search returns an explicit outcome — `evidence_found`,
  `conflicting_evidence`, `no_adequate_evidence`, `index_coverage_incomplete`, `retrieval_degraded`,
  `retrieval_failure` — instead of an invented confidence score.
- **Permissions enforced in the database query:** users only ever see collections their groups are
  granted; restricted documents never influence results, counts or errors.
- **Traceable results:** ranked passages with heading/page anchors, a passage viewer and the original file.
- **Reliable ingestion:** versioning, unchanged-file detection, atomic updates, deletes, retries and
  visible failure reasons.

> Status: first-release Gate 1 verified; Gate 2 largely verified. Exact verification status, measured
> numbers and open items: [docs/HANDOFF.md](docs/HANDOFF.md).

## Table of contents

[Requirements](#requirements) · [Quick start](#quick-start) · [Test run with a few documents](#test-run-with-a-few-documents) ·
[Usage](#usage) · [Configuration](#configuration) · [Testing](#testing) · [Deployment](#deployment) ·
[Architecture](#architecture) · [Troubleshooting](#troubleshooting) · [Project layout](#project-layout) · [License](#license)

## Requirements

| | Minimum (tested) |
|---|---|
| OS | Linux (tested), macOS, Windows 10/11 (setup script written, **not yet tested**) |
| CPU / RAM | 2+ cores, 4 GB free RAM (tested on 4 vCPU / 15 GB) — no GPU required |
| Disk | ~1 GB (Python deps ~500 MB, model ~70 MB, database) |
| Software | [Docker](https://docs.docker.com/get-docker/) (for PostgreSQL); [uv](https://docs.astral.sh/uv/) is installed automatically by the setup script |

## Quick start

**Linux / macOS**

```bash
git clone https://github.com/MayankPunghal/knowledge-search && cd knowledge-search
./setup.sh --demo --start
```

**Windows** (Docker Desktop running)

```bat
git clone https://github.com/MayankPunghal/knowledge-search && cd knowledge-search
setup.bat -Demo -Start
```

Open <http://127.0.0.1:8000> and sign in as `alice` / `demo-password-alice`.
The admin password is printed once and saved to `.admin-password.txt`.

The setup script: checks Docker, installs `uv` if missing, starts PostgreSQL+pgvector
(`docker compose up -d db`), installs Python dependencies, downloads the embedding model, applies
migrations and creates the `admin` user. `--demo` additionally loads the **fictional** sample corpus and
demo users. It is safe to re-run. Without `--demo` you get an empty system — see
[docs/GUIDE.md](docs/GUIDE.md) to add your own users, collections and documents.

## Test run with a few documents

After `./setup.sh --demo --start` (or with your own files — see the guide):

| Try this search (as `alice`) | What you should see |
|---|---|
| `ERR-PAY-4012` | **Evidence found**, exact-identifier match, error code section of the payments reference |
| `POST /api/v2/invoices/{id}/void` | the "Void an invoice" section |
| `per diem for domestic travel` | **Conflicting evidence**: 2024 and 2025 policies with their effective dates |
| `ERR-PAY-4013` | **No adequate evidence** (the code does not exist; look-alikes are not treated as evidence) |
| `who won the company football tournament` | **No adequate evidence** |
| `how many weeks of paid parental leave` | as `bob`: nothing from HR; as `hannah`: the confidential HR policy |

Then: click **View passage** and **Open source**; on **Documents**, upload a `.md` file, watch it go
`pending → indexed`, search for it, delete it; check **Index status**.
Full walkthrough with your own documents: [docs/GUIDE.md](docs/GUIDE.md#3-test-run-with-your-own-documents).

## Usage

* **UI:** <http://127.0.0.1:8000> — Search, Documents (upload/retry/delete), Index status.
* **API:** interactive OpenAPI docs at <http://127.0.0.1:8000/docs>. Example:

  ```bash
  TOKEN=$(curl -s localhost:8000/api/auth/login -H 'content-type: application/json' \
    -d '{"username":"alice","password":"demo-password-alice"}' | jq -r .token)
  curl -s localhost:8000/api/search -H "Authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' -d '{"query":"ERR-PAY-4012"}' | jq '.outcome, .results[0].title'
  ```
  External workflows (e.g. retrieval → your own reranker/LLM summariser) can call `/api/search` with a
  service account; results are already permission-filtered and carry source links.
* **CLI** (`uv run knowledge-search --help`): `migrate`, `create-user`, `create-collection`, `grant`,
  `add-to-group`, `remove-from-group`, `ingest <collection> <dir> [--wait]`, `serve [--workers N]`,
  `worker`, `download-model`.

## Configuration

All settings are environment variables with the `KS_` prefix (or a `.env` file; see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `KS_DATABASE_URL` | `postgresql://ks:devpass@127.0.0.1:55432/ks` | PostgreSQL connection |
| `KS_DATA_DIR` | `./data` | original files (`blobs/`) |
| `KS_MODEL_CACHE` | `./data/models` | embedding model files |
| `KS_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | one of the registry models in `embedder.py`; changing it requires reindex + gate re-tuning |
| `KS_OFFLINE` | `false` | `true` forbids model downloads at runtime |
| `KS_INPROCESS_WORKERS` | `1` | ingestion threads inside the API; set `0` and run `knowledge-search worker` on servers |
| `KS_MAX_UPLOAD_MB` | `25` | upload limit |
| `KS_SESSION_HOURS` / `KS_COOKIE_SECURE` | `12` / `false` | session lifetime / set `true` behind HTTPS |
| `KS_GATE_DENSE_HIGH`, `KS_GATE_DENSE_MIN`, `KS_GATE_LEX_COVERAGE` | `0.88`, `0.76`, `0.34` | evidence gate (tuned on the fixture val split — re-tune on real data) |

There are deliberately no LLM / provider / API-key settings in this release.

## Testing

Tests are integration tests against a **real** PostgreSQL+pgvector database and the **real** local
model (no mocks of the search path). They create and drop their own database `ks_test`.

```bash
docker compose up -d db          # if not already running (setup.sh does this)
uv run pytest -q                 # 19 tests, ~5 s after the model is cached
```

| Test module | Covers |
|---|---|
| `tests/test_search.py` | exact-identifier, API-path and conceptual queries; PDF page source opening; inert source serving; abstention on unknown IDs and unrelated queries; conflicting versions; restricted-collection isolation (search, passages, sources, listings, status, admin); revocation; auth + CSRF; no outbound network during search |
| `tests/test_lifecycle.py` | upload → pending → indexed; unchanged re-upload; update replaces old chunks; delete propagation incl. stored file; rejected/failed ingestion with reason codes; manual and automatic retry; degraded dense branch; backend failure → explicit 503 |

Evaluation and load tests (see [docs/EVALUATION.md](docs/EVALUATION.md)):

```bash
uv run python eval/run_eval.py --models BAAI/bge-small-en-v1.5      # retrieval + abstention metrics
uv run python eval/bench_concurrency.py --user alice --password demo-password-alice --concurrency 1 4 8 16
```

## Deployment

**Dedicated CPU server** (same code, persistent volumes, separate ingestion worker, optional TLS):

```bash
cp .env.example .env               # set KS_DB_PASSWORD, KS_HOSTNAME, KS_COOKIE_SECURE=true
docker compose --profile tls up -d --build
docker compose exec app /app/.venv/bin/knowledge-search create-user admin --admin --password -
```

Services: `db` (PostgreSQL+pgvector, volume `ks-pg`), `app` (API+UI), `worker` (ingestion; scale with
`--scale worker=N`), `caddy` (TLS, profile `tls`). The image bakes in the model and runs offline.
**Verified:** the same topology run natively (API + separate worker + PostgreSQL container), multi-user
HTTP access, 0 errors at concurrency 16. **Not verified:** `docker compose build` (base-image pulls were
rate-limited in the development environment) and the Caddy/TLS profile.

**GPU:** optional and not implemented/verified; the embedding adapter is the only place it would plug in.

**Backups:** `pg_dump` the database *and* copy `KS_DATA_DIR/blobs`. **Reindex** after changing the model:
re-run `knowledge-search ingest`, or requeue jobs (see [docs/GUIDE.md](docs/GUIDE.md#5-operations)).

## Architecture

One Python process (FastAPI) + one PostgreSQL database (pgvector + full-text) + a file store for
originals; optional separate worker processes. Design decisions, rejected alternatives and sources:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `connection refused` on port 55432 | `docker compose up -d db` |
| `cannot load model … local_files_only` | run `uv run knowledge-search download-model` (or unset `KS_OFFLINE`) |
| Document stuck in `pending` | no worker running: start `serve` (in-process worker) or `knowledge-search worker` |
| Document `failed` with `no_extractable_text` | scanned PDF without a text layer — OCR is not supported |
| Search says *index coverage incomplete* | some documents in your scope are pending/failed/outdated; see **Index status** |
| Search says *no adequate evidence* but a result looks right | it is shown under "lower-relevance matches"; the gate is conservative by design |

## Project layout

```
src/knowledge_search/   app code (api, search, ingest, parsing, embedder, auth, cli, migrations, static UI)
tests/                  integration tests (pytest)
eval/                   labelled queries, evaluation + load-test scripts, committed results
fixtures/               FICTIONAL sample corpus (+ PDF generator)
deploy/                 Caddyfile (TLS reverse proxy)
docs/                   GUIDE, ARCHITECTURE, EVALUATION, HANDOFF, brief/ (scope), screenshots/
setup.sh / setup.bat    one-command setup (Linux/macOS / Windows)
```

## License

No license has been chosen yet (all rights reserved by the repository owner). Third-party components
are permissively licensed (MIT/BSD/Apache-2.0; psycopg is LGPL-3.0, used as an unmodified library);
re-check model licences on their model cards before commercial use.
