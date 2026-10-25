# Plan: one-click installer for a non-technical user (handoff, 2026-10-24)

Goal (owner): install like holler. Download `Ferret-Setup-<version>.exe`, click through, and the app
opens in the browser with a short setup wizard. No Python, no Docker, no command line. Nothing below is built
yet. Sizes and times are **unmeasured**.

## What holler does (github.com/MayankPunghal/holler), and what carries over

| Holler | Carries over? |
|---|---|
| PyInstaller folder build + Inno Setup installer, per-user install (`PrivilegesRequired=lowest`, no admin prompt) into `%LOCALAPPDATA%\Programs` | yes, per-user, no admin prompt |
| Settings and models in `%APPDATA%\Holler`, kept on uninstall unless the user says otherwise | yes: data folder (database, files, models) in `%LOCALAPPDATA%\Ferret`, asked on uninstall |
| CI (`windows-app.yml`): build, self-test the app, silent install, self-test the installed app, silent uninstall, attach to the release on a `v*` tag | yes, same workflow shape |
| Tray icon (`pystray`) and a Tk setup wizard | tray icon yes; the wizard lives **in the browser** (this is a web app; the first-run page exists already) |
| Not code-signed: SmartScreen warns, README explains "More info → Run anyway" | same at first; signing needs a certificate (owner's decision and cost) |
| PyPI package that the installer wraps | yes: the PyPI package is step 2 of docs/plan-release.md and must exist first |

## How to package the Python app

Two ways. Try A first and keep B as the fallback.

- **A. A portable Python folder (recommended).**
  - CI installs uv's standalone, relocatable Python 3.13 into `app\python`.
  - `uv pip install --target` (or a relocatable venv) adds the locked dependencies.
  - The installer copies the folder; a small launcher starts `pythonw -m knowledge_search.tray`.
  - Why: our heavy native packages (ONNX Runtime, the embedded PostgreSQL binaries inside
    `pixeltable-pgserver`, PyMuPDF, numpy) run exactly as they do from `pip`, with no PyInstaller hooks to
    maintain.
- **B. PyInstaller,** like holler. One `.exe` launcher and a smaller folder, but each native package needs
  hooks and data-file rules. The PostgreSQL binaries (`pixeltable_pgserver/pginstall`) and ONNX Runtime's
  DLLs are the risk.

Checked in `uv.lock`: `pixeltable-pgserver` 0.6.0 has wheels for Windows x64 and arm64, macOS arm64 and
x64, and Linux x64 and arm64. **ONNX Runtime 1.31 has no macOS x64 (Intel Mac) wheel for Python 3.13**, so
Intel Macs are not supported as pinned.

## What the user sees

1. **Installer:**
   - Licence; where to install (default per-user).
   - "Start when I sign in" (on), "Desktop shortcut" (off).
   - "Download the search models now" (on; about 0.5 GB with the reranker, unmeasured).
   - Finish → the app opens.
2. **Browser setup wizard** (extends the existing first-run page):
   - Create the administrator account.
   - Where to keep data (default shown, with free disk space).
   - Search model download with a progress bar (reuse the Settings download code), or "already done".
   - Optional: document summaries by the local model, with an honest size and speed note (Gemma 4 E2B,
     ~3.1 GB, about 40 s per document on a laptop CPU).
   - Optional: sample documents.
   - Optional: "Let others on my network use it". This binds to the network instead of `127.0.0.1`, and
     Windows then asks about the firewall. Off by default.
   - Done → Search page, with "Add your first documents" pointing at Documents → Upload and at site2md.
3. **Tray icon:** Open · Pause indexing · Start on sign-in ✓ · Check for updates · Quit. A notification when
   a long index finishes.

## Updates, data safety, uninstall

- **Update check:** daily against GitHub Releases (switch in Settings, as holler does). A banner says
  "Version X is available". The new installer upgrades in place.
- **Before an upgrade migrates the database:** a backup copy is made automatically (the rehearsed
  backup/restore path), keeping the last 2.
- **Uninstall:** removes the program and asks "Also delete your documents index and downloaded models
  (N GB)?" (default: keep).

## Other platforms

- **macOS (Apple silicon):** the same portable folder in a `.app` and a `.dmg`. Without an Apple Developer ID
  (signing and notarisation) users must right-click → Open. The owner decides when to pay for it.
- **Linux:** `uv tool install ferret-search`, with `ferret service install` to start it on login
  (a systemd user unit). Docker for servers (docs/plan-docker.md). An AppImage only if users ask.

## CI checks (copy holler's windows-app workflow)

- Build the folder, then run `ferret selftest` (defined in docs/plan-docker.md): embedded
  PostgreSQL, index the samples, 3 known searches, 1 refusal.
- Build the installer.
- Silent install `/VERYSILENT /MERGETASKS=!autostart`, run `selftest` from the installed folder, silent
  uninstall, check that the program is gone and the data folder is kept.
- Record the installer size in the release notes.
- On a `v*` tag, attach the installer to the GitHub release.

## Order

1. PyPI package and `selftest` (docs/plan-release.md, steps 1–2).
2. Tray launcher and the browser wizard steps (they also help PyPI users).
3. Windows folder build (A), Inno Setup script, CI workflow.
4. macOS `.dmg` (after the owner decides on signing).
