# Plan: production-grade install and release (handoff, 2026-10-24)

Owner request: "a production grade deployment workflow … a docker image? or a setup wizard installer … an easy
setup like I did for holler". Holler (github.com/MayankPunghal/holler) ships three ways:
- **PyPI:** `pip install holler`, then `holler` opens a setup wizard.
- **Windows installer:** `Holler-Setup-<version>.exe`, built by GitHub Actions. PyInstaller makes the app and
  Inno Setup makes the installer. CI installs it, runs a self-test, uninstalls it and attaches it to the
  release.
- **Release automation:** CI publishes to PyPI with trusted publishing (no stored token).

Nothing below is built yet. Sizes and times are **unmeasured**.

## What exists today

| Path | Who it is for | State |
|---|---|---|
| `setup.sh` / `setup.ps1` / `setup.bat`, then `start.*` | one person on a laptop | Works without Docker or admin rights: installs `uv`, Python 3.13, dependencies, embedded PostgreSQL and models. Needs the source folder (git clone or zip). |
| `Dockerfile` + `docker-compose.yml` (+ `deploy/Caddyfile`) | a team server | PostgreSQL + pgvector container, app, separate indexing worker, optional Caddy HTTPS. Built locally; no published image. |
| CI, PyPI package, published image, Windows installer | — | **none** (no `.github/workflows`) |

Problems found while checking:
- The Docker image bakes in and uses **bge-small**, but the app's default (and best measured) model is
  **bge-base**. A Docker install would search less accurately than a laptop install. Fix: build with the same
  default.
- `site2md` is a local workspace package. It must be published too, or moved inside the main package,
  before `pip install knowledge-search` can work.
- Not checked yet: whether `pixeltable-pgserver==0.6.0` has wheels for every target (Windows x64, macOS
  arm64/x64, Linux x64/arm64). It is the piece that makes "no Docker, no admin rights" possible.

## Order (each step is useful on its own)

1. **CI first.** GitHub Actions runs `uv run pytest -q` on Linux, Windows and macOS (the tests start their own
   embedded PostgreSQL, so no services are needed), plus `ruff` and `scripts/ui_check.py` against a demo
   server on Linux. Without this, the packages below cannot be trusted.
2. **PyPI package and a browser setup wizard** (like holler's wizard, but in the browser because this app is
   a web app):
   - `uv tool install knowledge-search` (or `pipx install`), then `knowledge-search` opens the browser.
   - The first-run page already creates the administrator. Extend it into steps: data folder; the search
     model download with progress (reuse the Settings download button); optional summaries model; sample
     data yes/no; start on login yes/no.
   - Publish from CI on a `v*` tag with trusted publishing, as holler does.
   - `setup.sh`/`setup.ps1` stay for people who clone the repository.
3. **Docker image on GitHub's registry** (`ghcr.io/mayankpunghal/knowledge-search`), built and tested in CI on
   each tag:
   - **One-container mode** for a small team: `docker run -p 8000:8000 -v ks-data:/data ghcr.io/…`, using the
     embedded PostgreSQL inside the container.
   - **Compose** stays for a real server: separate PostgreSQL, worker and HTTPS.
   - Models are baked in, so it runs offline. Fix the model mismatch above.
4. **Windows installer** (`KnowledgeSearch-Setup-<version>.exe`), copying holler's PyInstaller + Inno Setup +
   CI install test:
   - A tray icon: "Knowledge Search is running · Open · Stop · Start on login".
   - Hard parts: bundling the embedded PostgreSQL binaries and onnxruntime; downloading `llama-server` on
     first use (already how it works); and installer size (several hundred MB, unmeasured).
   - Do it after steps 1–3, because the PyPI package is what it wraps.
5. **macOS app** last: signing and notarisation need an Apple developer account (the owner's decision and
   cost).

My view: the Windows installer matters most for a single non-technical user, and the Docker image matters
most for a team server. Both should wrap the **same** package. That is why CI and PyPI come first, not the
installer.

## Checks before calling a release production grade

- A fresh install on a clean Windows, macOS and Linux VM, by someone following only the README, reaches a
  first search. Time it.
- Upgrade from the previous version keeps data (migrations run on start) and keeps the rehearsed
  backup/restore working (`scripts/backup_restore_check.sh`).
- Uninstall removes the app but asks before deleting the data folder.
- Every artifact passes a self-test in CI before it is published, as holler's does.
