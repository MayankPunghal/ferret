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
| 3 | Optional: the parallel-embedding benchmark on a 16–32-core server (`eval/bench_parallel.py --file` with a large document); on 4 cores it gave no gain | **not measured** | 24 Oct 14:52 |
| 26 | Tables, step 5 of [plan-tables.md](plan-tables.md): are table rows found better when indexed as "column: value" lines (shown as a table)? Needs about 20 labelled questions on table pages (AWS quotas pages, Kubernetes reference tables, a Word file with merged cells) and `eval/tricks.py`; keep only if clearly better | **not measured** | 24 Oct; tables built 2026-10-25 |

## 3. Plans ready, not built (owner asked for the plan first)

| # | Item | Source / plan |
|---|---|---|
| 4 | CPU/GPU switch in Settings for embedding, reranking, summaries and enrichment (owner's laptop has an Intel iGPU) | 24 Oct 05:08 · [plan-gpu.md](plan-gpu.md) |
| 5 | Installer for Ferret and Ferret Crawler (setup wizard, PyPI listing, like the owner's *holler* app) | 24 Oct 10:20, 10:27, 11:27 · [plan-installer.md](plan-installer.md), [plan-release.md](plan-release.md) |
| 6 | Docker image and compose file brought up to date with the current app | 24 Oct 10:20, 10:27 · [plan-docker.md](plan-docker.md) |
| 7 | Invite links and password reset (Google sign-in and e-mail/SMTP were discussed, not chosen) | 24 Oct 10:05–10:27 · [plan-invites.md](plan-invites.md) |
| 8 | Faster indexing of very large documents: small documents not stuck behind a large one (two workers), re-use vectors of unchanged passages | 24 Oct 14:52 · [plan-large-documents.md](plan-large-documents.md) |
| 10 | Images: the crawler saves images, a local vision model writes their text, indexed through the same path as other files (crawler, folder sync and MCP all included) | 23 Oct 11:51, 12:21 (owner: hold), 24 Oct 11:55 · [plan-images-and-models.md](plan-images-and-models.md); waits for item 2 |
| 16 | **Ask**: a chat window like Claude Code / OpenCode / the DeepSeek harness. Saved conversations, follow-ups, step cards, pictures, Think first; a local model (Gemma 4 E2B or E4B) answers from what search finds, with checked citations; search's evidence check decides whether to answer; optional MCP `ask` tool. Six phases | 23 Oct 07:37, 24 Oct (owner: separate tab, then a full chat window) · [plan-answers.md](plan-answers.md) |

## 4. Needs the owner

| # | Item | Source |
|---|---|---|
| 11 | Windows check of the current version: `git pull`, `setup.bat --summaries`, summary model download on Windows (not verified here), Crawler tab sign-in with installed Chrome/Edge, a real intranet copy sent to a collection | 23 Oct 17:02, 24 Oct 12:38 |
| 12 | Your own documents (runbooks, the codebase-documenter `repo-kit` folder) with 50–100 labelled questions, including ones that must be refused. Every search setting so far was tuned on public docs | 23 Oct 04:01, 24 Oct 11:27 |
| 24 | Published package and image names: `ferret` is taken on PyPI (`ferret-search` was free on 24 Oct); older plans still say `knowledge-search`. Decide before step 2 of [plan-release.md](plan-release.md) | 24 Oct (repo renamed to ferret) |

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
