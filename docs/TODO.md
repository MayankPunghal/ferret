# Open work (audit of every owner request, 2026-10-24)

Built from all ~210 owner messages of the build session (including the ones sent while work was running),
checked against the code, the tests and the git history. Done items are not listed here: see
[HANDOFF.md](HANDOFF.md) §4 and [requirements-brief.md](requirements-brief.md). The date and time are the
owner's request (UTC).

Status words: **bug** · **running** · **plan ready** (written, not built) · **not built** · **not measured** ·
**needs owner**.

## 1. Fix first

| # | Item | Status | Source / where |
|---|---|---|---|
| 1 | `GET /api/documents` stops at 1,000 rows (`LIMIT 1000` in `api.py`): past 1,000 documents the Documents tab says "All 1000" and the Summaries tab's bar and list are wrong (and render thousands of rows) | **bug** | Found 2026-10-24 while planning the Summaries counts; fix is step 4 of [plan-summaries-status.md](plan-summaries-status.md) |

## 2. Measurements to finish

| # | Item | Status | Source / where |
|---|---|---|---|
| 2 | Vision: GLM-4.6V-Flash 9B against Gemma 4 E2B on 20 images (text in images, diagram relations) | **running in the cloud session** when it ended (GLM's last 9 images, then Gemma). GLM's first 11: all recall 1.00, 90–960 s per image on 4 cores (`eval/results/images-glm-4.6v-flash-part1.txt`). If `images-glm-4.6v-flash-part2.json` and `images-gemma-4-e2b.json` are not in `eval/results/`, re-run locally: `uv run --group eval --with playwright --with pillow python eval/image_set.py`, start `llama-server -m <model>.gguf --mmproj <mmproj>.gguf -c 16384 --temp 0 --jinja --port 8082`, then `uv run --group eval python eval/image_ocr.py --name <model> [--no-thinking for GLM]` | 23 Oct 11:51, 24 Oct 03:27, 11:16 ("do they have vision? that's the only thing worth having them") |
| 3 | Parallel embedding benchmark on an idle CPU: `uv run python eval/bench_parallel.py --passages 400 --layouts 1x0 2x0 2x2 4x1` (on your laptop: it is the machine that matters) | **not measured** (one contended run: 6.7 → 11.2 passages/s, identical vectors) | 24 Oct 14:52; [plan-large-documents.md](plan-large-documents.md) step 1 |

## 3. Plans ready, not built (owner asked for the plan first)

| # | Item | Source / plan |
|---|---|---|
| 4 | CPU/GPU switch in Settings for embedding, reranking, summaries and enrichment (owner's laptop has an Intel iGPU) | 24 Oct 05:08 · [plan-gpu.md](plan-gpu.md) |
| 5 | Installer for Ferret and Ferret Crawler (setup wizard, PyPI listing, like the owner's *holler* app) | 24 Oct 10:20, 10:27, 11:27 · [plan-installer.md](plan-installer.md), [plan-release.md](plan-release.md) |
| 6 | Docker image and compose file brought up to date with the current app | 24 Oct 10:20, 10:27 · [plan-docker.md](plan-docker.md) |
| 7 | Invite links and password reset (Google sign-in and e-mail/SMTP were discussed, not chosen) | 24 Oct 10:05–10:27 · [plan-invites.md](plan-invites.md) |
| 8 | Faster indexing of very large documents: several embedding sessions, small documents not stuck behind a large one, re-use vectors of unchanged passages | 24 Oct 14:52 · [plan-large-documents.md](plan-large-documents.md) |
| 9 | Summaries tab with count tiles and a per-collection table like Index status, paged list | 24 Oct 14:53 · [plan-summaries-status.md](plan-summaries-status.md) |
| 10 | Images: the crawler saves images, a local vision model writes their text, indexed through the same path as other files (crawler, folder sync and MCP all included) | 23 Oct 11:51, 12:21 (owner: hold), 24 Oct 11:55 · [plan-images-and-models.md](plan-images-and-models.md); waits for item 2 |

## 4. Needs the owner

| # | Item | Source |
|---|---|---|
| 11 | Windows check of the current version: `git pull`, `setup.bat --summaries`, summary model download on Windows (not verified here), Crawler tab sign-in with installed Chrome/Edge, a real intranet copy sent to a collection | 23 Oct 17:02, 24 Oct 12:38 |
| 12 | Your own documents (runbooks, the codebase-documenter `repo-kit` folder) with 50–100 labelled questions, including ones that must be refused. Every search setting so far was tuned on public docs | 23 Oct 04:01, 24 Oct 11:27 |

## 5. Search and graph ideas not built (each only with a measured gain)

| # | Item | Status | Source |
|---|---|---|---|
| 16 | Second brain: a local model answers from the found passages, with checked citations, and refuses when there is no evidence | not built (trialled in `eval/llm_answers.py`) | end goal; 23 Oct 07:37 |
| 17 | Shared-identifier (entity) links plus PageRank-style spreading (HippoRAG idea) | not built | 23 Oct 04:23 (owner's list, item 5) |
| 18 | Learned keyword search (SPLADE) | not built | 23 Oct 04:23 (item 4) |
| 19 | Automatic clusters in the graph view | not built | 23 Oct 02:59, 04:23 (item 6) |
| 20 | OCR for scanned PDFs, Docling for difficult PDFs; legacy `.doc` / `.ppt` | not built | 23 Oct 04:23 (item 7); on hold with images |
| 21 | Scale test at archive size (~40k multi-MB PDFs), approximate index with exact re-scoring | not measured | 23 Oct 03:06 |
| 22 | Graph questions on a real Graphify / codebase-documenter wiki (multi-hop, blast radius) | not measured (synthetic wiki only) | 23 Oct 02:47, 13:09 |
| 23 | Larger summary model (Gemma 4 12B class) for a dedicated server | not run (disk in the cloud container) | 23 Oct 07:37 |

## 6. Answered or decided (so they are not asked again without new data)

- **Dedicated summarisation models (BART, PEGASUS, T5):** not used. Instruction-tuned models write summaries
  rated on par with professional writers (Zhang et al., TACL 2024), while the dedicated ones were mostly
  trained on news, read about 1,000 tokens, and cannot return the document type, topics or a clearer title.
  Answered 23 Oct 13:28 and 24 Oct 03:39; recorded here because it was only in chat.
- **DeepSeek-R1-0528-Qwen3-8B:** not run. It has no vision, which was the owner's condition for the larger
  models (24 Oct 11:16).
- **The search tricks** (no overlap, passage size, summary in the keyword index, summary as a context line
  at three lengths, summary vectors, index-time question vectors, neighbour passages at rerank, convex
  fusion, RRF constant, HyDE): measured on 219 queries, none beat the shipped setup by more than noise
  (evaluation.md §8).
- **Relations written by the local model, and topic links:** measured, worse than the shipped "related"
  rows (HANDOFF §4j).
- **A separate vector or graph database:** not now. PostgreSQL holds vectors, links and text in one store
  with exact scoring; revisit with item 21.
- **Neighbour summaries instead of the 150-character overlap:** discussed and rejected by the owner
  (2026-10-24). Not measured; the closest measured variants (no overlap, neighbour passages at rerank,
  summary context) gave nothing.
- **Repository name and licence:** settled by the owner (2026-10-24).
- **Spreadsheets and videos** are skipped by the crawler (owner, 23 Oct 11:27).

## 7. Housekeeping

- About 20 older `ruff` findings (unused `noqa`, import order, two B023/B008). Harmless; clean up in one go.
- [requirements-brief.md](requirements-brief.md) is dated 23 Oct. This page is the current list of open work.
