# Open work (only what is still to do; updated 2026-10-25)

Audited against every owner request of the build session, the code, the tests and the git history. Done work
and settled decisions are in [HANDOFF.md](HANDOFF.md) (§4 built and measured, §5 decided); plans with sizes
and a suggested order are in [PLANS.md](PLANS.md). Dates and times are the owner's request (UTC). Remove an
item here when it is done, and record the result in HANDOFF.

Status words: **bug** · **plan ready** (written, not built) · **not built** · **not measured** · **needs owner**.

## 1. Fix first

| # | Item | Status | Source / where |
|---|---|---|---|
| — | Nothing open. | | |

## 2. Measurements to finish

| # | Item | Status | Source / where |
|---|---|---|---|
| 2 | Vision: run **Gemma 4 E2B** on the same 20 pictures as GLM-4.6V-Flash (GLM's results are in evaluation.md §9), and GLM on the scanned page. Re-run locally: `uv run --group eval --with playwright --with pillow python eval/image_set.py`, start `llama-server -m <model>.gguf --mmproj <mmproj>.gguf -c 16384 --temp 0 --jinja --port 8082`, then `uv run --group eval python eval/image_ocr.py --name gemma-4-e2b` (GLM: `--name glm-4.6v-flash-scan --no-thinking --only scan-postmortem`). Then decide the images plan (item 10) | **not measured** | 23 Oct 11:51, 24 Oct 03:27, 11:16 |
| 26 | Tables, step 5 of [plan-tables.md](plan-tables.md): are table rows found better when indexed as "column: value" lines (shown as a table)? Needs about 20 labelled questions on table pages (AWS quotas pages, Kubernetes reference tables, a Word file with merged cells) and `eval/tricks.py`; keep only if clearly better | **not measured** | 24 Oct; tables built 2026-10-25 |

## 3. Plans ready, not built (owner asked for the plan first)

| # | Item | Source / plan |
|---|---|---|
| 5 | Release: the first publish. Built 2026-10-25: packages `ferret-search` and `ferret-crawler`, `release.yml` (Docker image to GHCR and PyPI on a `v*` tag), the image fixed (bge-base, writable models, libgomp1, one-container mode) and built and searched in CI. PyPI pending publishers registered 2026-10-25; `v1.0.0` tagged the same day. Left: make the GHCR package public after the first image push. Not built: the browser setup wizard beyond the existing first-run page (hardware defaults cover model choice), the backup check in CI (plan-docker 9) | 24 Oct 10:20 · [plan-release.md](plan-release.md), [plan-docker.md](plan-docker.md) |

### Later phase (owner, 2026-10-25: hold until after the first release)

| # | Item | Source / plan |
|---|---|---|
| 5b | Windows `.exe` installer (PyInstaller + Inno Setup, tray icon) and the macOS app | [plan-installer.md](plan-installer.md), plan-release steps 4–5 |
| 7 | Invite links and password reset (Google sign-in and e-mail/SMTP were discussed, not chosen) | 24 Oct 10:05–10:27 · [plan-invites.md](plan-invites.md) |
| 10 | Images: the crawler saves images, a local vision model writes their text, indexed through the same path as other files (crawler, folder sync and MCP all included) | 23 Oct 11:51, 12:21 (owner: hold), 24 Oct 11:55 · [plan-images-and-models.md](plan-images-and-models.md); waits for item 2 |
| 16 | **Ask**: a chat window like Claude Code / OpenCode / the DeepSeek harness. Saved conversations, follow-ups, step cards, pictures, Think first; a local model (Gemma 4 E2B or E4B) answers from what search finds, with checked citations; search's evidence check decides whether to answer; optional MCP `ask` tool. Six phases | 23 Oct 07:37, 24 Oct (owner: separate tab, then a full chat window) · [plan-answers.md](plan-answers.md) |

## 4. Needs the owner

| # | Item | Source |
|---|---|---|
| 11 | Windows check of the current version: `git pull`, `setup.bat --summaries`, summary model download on Windows (not verified here), Crawler tab sign-in with installed Chrome/Edge, a real intranet copy sent to a collection | 23 Oct 17:02, 24 Oct 12:38 |
| 12 | Your own documents (runbooks, the codebase-documenter `repo-kit` folder) with 50–100 labelled questions, including ones that must be refused. Every search setting so far was tuned on public docs | 23 Oct 04:01, 24 Oct 11:27 |

## 5. Search and graph ideas not built (each only with a measured gain)

| # | Item | Status | Source |
|---|---|---|---|
| 17 | Shared-identifier (entity) links plus PageRank-style spreading (HippoRAG idea) | not built | 23 Oct 04:23 (owner's list, item 5) |
| 18 | Learned keyword search (SPLADE) | not built | 23 Oct 04:23 (item 4) |
| 19 | Automatic clusters in the graph view | not built | 23 Oct 02:59, 04:23 (item 6) |
| 20 | OCR for scanned PDFs, Docling for difficult PDFs; PDF tables; legacy `.doc` / `.ppt` | not built | 23 Oct 04:23 (item 7); on hold with images |
| 21 | Scale test at archive size (~40k multi-MB PDFs), approximate index with exact re-scoring | not measured | 23 Oct 03:06 |
| 22 | Graph questions on a real Graphify / codebase-documenter wiki (multi-hop, blast radius) | not measured (synthetic wiki only) | 23 Oct 02:47, 13:09 |
| 23 | Larger summary model (Gemma 4 12B class) for a dedicated server | not run (disk in the cloud container) | 23 Oct 07:37 |

## 6. Housekeeping

- About 20 older `ruff` findings (unused `noqa`, import order, two B023/B008). Harmless; clean up in one go.
