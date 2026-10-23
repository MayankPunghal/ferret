# Knowledge Search

**Private, offline document search that shows its evidence — and says so when it has none.**

Knowledge Search indexes your Markdown, text, HTML, Word and PDF documents and lets people search them in plain
language or by exact terms (error codes, file names, API paths). Every answer points to the exact passage
and the original file. Everything — database, AI models, search — runs on your own machine or server:
no cloud account, no GPU and no external AI provider are needed.

![Search results with evidence](docs/screenshots/07-conflict.png)

## Features

- **Hybrid search** — keyword + semantic (local embedding model) + exact-identifier matching, fused by rank.
- **Offline reranker** (on by default) — a local cross-encoder re-orders the top results for higher precision.
- **Evidence-first answers** — each search states whether adequate evidence was found, sources conflict,
  the index is incomplete, or nothing adequate exists in the sources *you* can access. No invented
  confidence numbers.
- **Traceable results** — passage, heading/page, neighbouring context and a link to the original file.
- **Connections** — links between documents (Markdown/wiki links, HTML links, `depends_on`-style front matter) with a
  "what depends on this" blast-radius view, permission-filtered.
- **Link-aware search** — questions that name a page also get passages from the pages it links to and from
  ("who owns the services X depends on"); on a labelled synthetic wiki, multi-hop evidence recall 0.24 → 0.60
  with no single-hop loss ([evaluation](docs/evaluation.md#6-link-aware-search-graph-assisted-retrieval)).
- **Permissions** — collections with group-based read/write access, enforced inside every query.
- **Admin UI** — upload by drag and drop, change models/chunking/reranking, manage users and access, reindex.
- **One-click install** — embedded PostgreSQL + pgvector; nothing else to install.

## Quick start

**Windows 10/11** — open *Command Prompt* (or PowerShell) and run:

```bat
git clone https://github.com/MayankPunghal/knowledge-search
cd knowledge-search
setup.bat --demo
```

(No git? Download the ZIP from GitHub, extract it and double-click `setup.bat` — it asks whether to load the
sample documents.)

**macOS / Linux:**

```bash
git clone https://github.com/MayankPunghal/knowledge-search
cd knowledge-search
./setup.sh --demo
```

| Option (both scripts) | Effect |
|---|---|
| `--demo` | load the fictional sample documents and demo users without asking |
| `--no-demo` | skip the sample data |
| `--no-start` | install only; do not start the app afterwards |
| `--no-shortcut` | Windows only: do not create the desktop shortcut |

Setup installs everything for the current user (no administrator rights, no Docker): uv, Python 3.13, the
app, an embedded PostgreSQL and the offline AI models (~900 MB in total, one time). It is safe to re-run —
if the internet connection drops during a download, run it again and completed files are reused. At the end
it opens <http://127.0.0.1:8000>, where you create the administrator account.

Start the app later with the **Knowledge Search** desktop shortcut or `start.bat` (Windows), or `./start.sh`
(macOS/Linux). Full walkthrough and a test run with your own documents:
**[Getting started](docs/getting-started.md)**.

## Documentation

| Guide | For |
|---|---|
| [Getting started](docs/getting-started.md) | Installing, first run, trying it with sample or your own documents |
| [How it works](docs/how-it-works.md) | Plain-language explanation: embeddings, search, links, evidence check, why one database |
| [User guide](docs/user-guide.md) | Searching, reading results and outcomes, uploading documents |
| [Admin guide](docs/admin-guide.md) | Settings (models, chunking, reranker, evidence thresholds), users and access, reindexing |
| [Configuration](docs/configuration.md) | All `KS_*` environment variables |
| [Deployment](docs/deployment.md) | Laptop vs. dedicated server, Docker/external PostgreSQL, TLS, backups, GPU notes |
| [Development](docs/development.md) | Project layout, running tests, evaluation scripts, API |
| [Evaluation](docs/evaluation.md) | Measured retrieval quality, abstention, latency — and their limits |
| [Architecture](docs/architecture.md) | Design, decisions and alternatives considered, sources |
| [Research: better search](docs/research-search.md) · [graph](docs/research-graph.md) | Techniques evaluated or planned, with sources |
| [Troubleshooting](docs/troubleshooting.md) | Common problems and fixes |
| [Handoff](docs/HANDOFF.md) | Project status, verified vs. unverified items, next steps |

## Requirements

64-bit Windows 10/11, macOS or Linux; 4 GB free RAM (8 GB recommended for the larger models); ~2 GB disk;
internet access during setup only (to download the app's dependencies and models). After setup it runs
offline.

## Status

First release: retrieval-only (no generated answers). See [Handoff](docs/HANDOFF.md) for exactly what has
been verified and what has not.

## License

No license has been chosen yet; all rights reserved by the repository owner. Third-party components and
models are under their own licences (listed in [Architecture](docs/architecture.md#licences)).
