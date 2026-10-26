# Handoff — Ferret

State at the end of the cloud session (2026-10-24, version 1.0). **Open work: `docs/TODO.md`**; prompt for the next
(local) session: `docs/NEXT_SESSION_PROMPT.md`. Read with `CLAUDE.md` (owner instructions and end
goal), `docs/requirements-brief.md` (owner asks up to 2026-10-23), `README.md`, and the docs listed there. Scope authority: `docs/brief/…master_prompt.md` §11.

## 1. Resume locally

```bash
git clone https://github.com/MayankPunghal/ferret && cd ferret
./setup.sh --demo --no-start          # Windows: setup.bat -Demo -NoStart
uv sync                               # adds dev tools
uv run ferret download-model --model BAAI/bge-small-en-v1.5 --reranker Xenova/ms-marco-MiniLM-L-6-v2   # the tests' models
uv run pytest -q                      # Linux: 132 passed; Windows: 130 passed, 2 skipped (embedded PostgreSQL; no Docker)
uv run --group eval python eval/run_eval.py --reranker Xenova/ms-marco-MiniLM-L-6-v2
./start.sh                            # or start.bat → http://127.0.0.1:8000
```

## 2. What the product is now

Offline knowledge search for technical teams: upload Markdown/text/PDF into permissioned collections;
hybrid retrieval (PostgreSQL full-text + local embeddings + exact identifiers, RRF, optional local
reranker); every search returns ranked passages with source links **and an explicit evidence outcome**
(`evidence_found`, `conflicting_evidence`, `no_adequate_evidence`, `index_coverage_incomplete`,
`no_searchable_scope`, `retrieval_degraded`, `retrieval_failure`). One-click setup with an embedded
PostgreSQL (no Docker). Admin UI for models, chunking, reranker, thresholds, users and access.
Each result also lists its related documents (links to / referenced by / mentions / related in meaning).
Optional: a small local language model (Gemma 4 E2B via llama.cpp; setup or the app on first start downloads it) writes a
summary, topics and, for vague titles, a clearer title per document in the background (§4f). No answer
generation yet (deliberately — see §7).

Code map: `src/knowledge_search/` — `api.py` (routes), `admin.py` (admin routes), `search.py`
(retrieval + gate), `ingest.py` (versions, worker, progress), `parsing.py`, `embedder.py` (model
registry + per-model thresholds), `runtime.py` (settings, model cache, reranker), `vectors.py`
(in-memory vector search), `auth.py`, `db.py` (embedded PG, migrations, Windows DLL fix), `cli.py`,
`static/` (UI), `graph.py` (link graph), `enrich.py` (background summaries by the local model),
`ask.py` (the Ask tab: search, then an answer from the local model with citations), `migrations/0001-0006`.
Tests in `tests/`, evaluation in `eval/`.

## 3. Verification status (what was actually run)

| Item | Status | Evidence |
|---|---|---|
| Fresh install with shipped defaults (bge-base + MiniLM reranker, weighted fusion) | **verified** on Linux: `setup.sh --demo` into an empty data dir, `start.sh`, full UI walkthrough, screenshots regenerated | `docs/screenshots/` |
| Test suite | **verified** | `uv run pytest -q` → 130 passed (2026-10-24) (summaries, types, topics and hours against a fake local model server, search, HTML/DOCX/PPTX formats, crawler, link graph + link-aware search, permissions, lifecycle, admin/settings/reindex/reranker, in-memory vs pgvector parity, DB-failure 503, embedded-DB helpers) |
| Linux setup | **verified** (`setup.ps1` logic run end-to-end under PowerShell 7 on Linux; CLI setup/demo/serve on Linux) | session logs |
| Windows setup | **verified by the owner** on Windows 11: install, model download, DB init and indexing; searches crashed the embedded DB (pgvector distance functions on Windows) → fixed with in-memory vector search (commit 7ec7a93, 2026-10-22 18:01); owner after that: "setup worked now, search returns results on Windows" (2026-10-23 00:15). **Regression
found by the owner 2026-10-23 22:36:** the upgrade to migration 0005 (document vectors, `avg(embedding)` in SQL)
crashed the embedded DB on start, and meaning-based related pages (`graph.similar`, `<=>` in SQL) would have
too → both now computed in Python with the embedded DB (parity + backfill tests); **rule: no pgvector function
or operator in SQL on the embedded-DB path**. Fix not yet re-run by the owner on Windows | owner messages |
| UI walkthrough | **verified** (Playwright/Chromium, `scripts/ui_check.py`): first-run admin, settings save + reindex, collections/grants/users, multi-file upload incl. rejection, clean state after sign-out/in, search outcomes, passage drawer, source open, mobile width | `docs/screenshots/` |
| Server profile (web + separate worker, external PG, concurrency) | verified natively earlier (0 errors at c=16, ~60–75 req/s on 4 vCPU, bge-small). Docker image build **not verified** (registry rate limits) | docs/evaluation.md |
| GPU | **not available here, not implemented**; plan for CPU/GPU switches in Settings (embeddings, reranker, local model; Intel GPU via DirectML / OpenVINO and llama.cpp Vulkan) with parity checks to run on the owner's laptop | docs/plan-gpu.md |
| Install and release | **next step (owner, 2026-10-24)**: CI on 3 OSes → PyPI package with a browser setup wizard → Docker image on GHCR → Windows installer like holler's; found: the Docker image uses bge-small, not the default bge-base | docs/plan-release.md |
| Activity view | Logs → Activity: recent calls (searches, writes, MCP, local model, indexing) with request, response, step timings and tokens; memory only, admin only, secrets masked; checked in `scripts/ui_check.py` | docs/admin-guide.md |
| Database tab | Admin → Database: tables with row counts and sizes, rows (sort, filter, full values of one row), structure (columns, indexes, constraints), read-only SQL as the non-superuser role `ks_reader` (no password/token hashes; writes, second statements, server files, role switches refused — tested); external DB needs `KS_READONLY_DATABASE_URL` | docs/admin-guide.md, tests/test_dbview.py |
| Plans written 2026-10-24 (not built) | invite/reset links without a mail server; Docker brought up to date (found: image uses bge-small, no local model, read-only models folder); Windows installer like holler's — **still not built**. The site2md screen was built 2026-10-24 (§4o) | docs/plan-invites.md, plan-docker.md, plan-installer.md, plan-site2md-ui.md |
| Backup/restore | **verified** (`scripts/backup_restore_check.sh`): data-folder copy and pg_dump/pg_restore + blobs both restore identical search results and all original files open. Found and fixed on the way: the embedded database kept running after the app stopped (the web server re-raises Ctrl+C after shutdown, so exit hooks never ran) — the app now releases it on shutdown | docs/deployment.md |

## 4. Measured results (tables and caveats in docs/evaluation.md)

- **Embedding models, BEIR SciFact subset (2,000 abstracts, 300 human-labelled queries), CPU:**
  bge-small nDCG@10 0.779 (8.3 docs/s, query 6.6 ms) · **bge-base 0.818** (3.3 docs/s, 23 ms) ·
  arctic-embed-m 0.814 (3.7 docs/s, 22 ms). nomic-v1.5 was OOM-killed (now bounded by batch/token caps;
  not re-measured). Qwen3-Embedding-0.6B not measured on SciFact; on Kubernetes worse than bge-base (§4l).
- **Fusion:** equal-weight RRF hurt natural-language retrieval (0.746 vs dense 0.815); keyword weight
  0.1 restores 0.794 and keeps identifiers perfect on fixtures → default.
- **Rerankers (top-10, real pipeline):** MiniLM 0.795 → 0.809 at ~0.4 s (default on); bge-reranker-base
  0.812 at ~1.9 s (selectable). Neither improves abstention on hard negatives.
- **Abstention at scale (SciFact, 122 hard-negative unanswerable / 150 answerable):** no gate reaches
  ≤5–10% false answers; best trade-offs ≈ 15% false answers at ~47–51% coverage. Similarity/rerankers
  measure relevance, not support → wording changed to "Relevant passages found"; real claim verification
  is part of the planned answer-generation stage.
- Fixtures (12 docs, 56 queries): hybrid ranking at ceiling; similarity gate test false-answer 1/12 at
  56–63% coverage; reranker gates 81% coverage with 2/12 false answers.

## 4a. Windows setup — latest report (2026-10-23)

Owner's fresh clone on Windows 11: dependencies and embedded DB installed; **model download failed** with
`WinError 10054/10038` (connection reset / "not a socket") from Hugging Face's parallel *xet* transfer, and
fastembed's 3 quick retries gave up (the file then finished in the background). Fix (commit after
16ed591…): `HF_HUB_DISABLE_XET=1` + longer timeouts set in `knowledge_search/__init__.py`, our own retry
with backoff for network-type errors (`download.py`, 5 attempts, tested), a plain-language error in setup,
and `setup.bat --demo/--no-start/...` flag support. Verified on Linux (fresh cache download in 20 s);
**not yet re-verified on Windows** — next step for the owner: `git pull` then `setup.bat --demo`.

**Update:** owner confirmed on Windows 11 — setup completes and search returns results (the
download and pgvector fixes work). Remaining console noise (`ConnectionResetError` in
`_call_connection_lost`, a harmless CPython/Windows quirk) is now filtered, and an admin **Logs** tab
(diagnostics + errors with tracebacks, "Copy for support", file `data/logs/knowledge-search.log`) makes
future reports easy.

## 4b. Graph navigation v1 (latest session)

Implemented and verified (tests + Playwright walkthrough, screenshot `docs/screenshots/11-connections.png`):
deterministic links from Markdown links, `[[wiki links]]` and front-matter relations → `doc_links`
(migration 0004); `GET /api/documents/{id}/links` and `/impact?depth=1..3`; Connections panel in the
passage drawer; permission filtering on every hop (tested); demo wiki `fixtures/corpus/platform-wiki/`.
Details and what is still open: `docs/research-graph.md`. **Not evaluated yet** for answer quality.

## 4c. Link-aware search (graph-assisted retrieval, Gate 3 item)

Search now follows the links of the page a question names (`search._linked`, setting `graph_expansion`,
on by default, badge "linked from …"). Measured on a labelled synthetic interlinked wiki
(`eval/make_wiki.py`, `eval/graph_eval.py`; docs/evaluation.md §6): multi-hop evidence recall
dependencies 0.24 → 0.60, workflows 0.36 → 0.57, impact 0.23 → 0.40; single-hop unchanged (0.96);
0 restricted passages; fixture eval identical; +15 ms. Verified: tests (incl. permission test and
pgvector/in-memory parity), Playwright walkthrough (screenshot `13-linked-search.png`). Not verified on a
real corpus. Also found: the evidence gate rejects most correct answers on terse wiki sections
(similarity 0.6–0.7 vs threshold 0.72) — re-tune thresholds on real data.

## 4d. Mentions, keyword gate, real-corpus stress test (2026-10-23, later)

- **Mentions** (`graph.mentions`, commit d122885): pages are linked when a passage names another page's
  title (stemmed phrase, titles ≥ 2 words, computed at query time). With every declared link stripped from
  the wiki, mentions alone match declared links (deps 0.25 → 0.64). Used by search expansion, the
  Connections list and the graph view (dotted edges). Not yet: aliases / identifiers / one-word titles.
- **Keyword gate** (26afcfd): owner reported "exact words, still no evidence" (Qwen3 + bge-reranker-base,
  untuned thresholds). Short queries have cosine 0.54–0.71 with every model. New rule: all query terms in the
  passage + similarity ≥ `gate_all_terms_min`; 40 keyword queries added to `eval/queries.jsonl`; Qwen3 tuned
  (0.78/0.64/0.20/0.45): fixtures test answered 0.86, false answers 1/20; bge-base 0.68 and 3/20. Switching
  the reranker now resets a stored reranker-score threshold. Recommend gate mode **similarity**.
- **Real corpus** (ed5c956, 82197ac, 9da5864): `eval/make_k8s_corpus.py` (623 Kubernetes pages, 8,524
  passages), `eval/k8s_queries.jsonl` (56 answerable, 16 must-refuse; labels = answer key for the test only,
  written by hand once, never needed by the product), `eval/corpus_eval.py` (per-query table). It exposed 4
  parser/gate bugs, fixed in parse-v4 (bare "2.0" as identifier, `{#id}` headings, camelCase keys, loose
  front matter). parse-v4, bge-base: **56/56 in top 5, 0/16 false answers, 89% answered, rank-1 0.946,
  0.24 s/query**. MiniLM / jina-turbo / bge-reranker-base: rank-1 0.946 / 0.964 / 0.964 for 1.2 / 1.3 /
  4.3 s — at most one query better, so **reranker off** is the recommendation. Remaining misses are
  abstentions on reworded questions ("run a job every night at 2am") and `OOMKilled` (sim 0.59).
- **Contextual passages** (`parsing.CONTEXT_CHARS`, research item 3): document summary in every passage's
  embedding. Kubernetes rank-1 0.946 → 0.982 (1.00 with MiniLM); fixtures 1.00 → 0.98 and fewer answered.
  **Off**: with a gate tuned for it on both label sets pooled it answers less (0.73 vs 0.77) and less
  precisely (0.91 vs 0.94). `eval/pooled_gates.py` (fixtures + Kubernetes, 84 val / 84 test) re-picks the
  shipped bge-base gate exactly; its 3/28 test false answers are fixture near-topic questions.
- **Not done, deliberately:** UpperCamel words (`OOMKilled`, `NodePort`) as exact identifiers — would fix
  `OOMKilled`, but brand words (`GitHub`, `JavaScript`) mentioned in passing would then count as exact
  evidence and a query with an identifier absent from the corpus is refused outright. If revisited: count
  them as evidence only when matched, never as a veto, and only with full query-term coverage.
- **Bake-off harness:** `eval/beir_screen.py` takes `cqadupstack/<forum>` (technical StackExchange Q&A),
  several `--rerank-candidates`, `--max-queries`, and ColBERT (late-interaction) models.

## 4e. Version 1.0: crawler, formats, speed, local-model trials (2026-10-23, last)

- **site2md 1.0.0** (`site2md/`, shareable zip, launchers `Crawl Website.bat` / `.command`, browser
  choice Chrome/Edge/Firefox/Chromium, sign-in kept per browser in `~/.site2md/profile-<browser>`):
  - **Pages:** saved as Markdown with `source_url` front matter. Text outside `<main>` is kept when
    `<main>` misses it (Google Sites); pages are scrolled through before reading.
  - **Linked and embedded files:** saved as originals, not converted. Google Drive files, Docs (as Word),
    Slides (as PDF) and the files in embedded Drive folders are saved too, and listed under "Attached
    documents" with links to the local files. Only PDF, Word and PowerPoint are kept, up to 500 MB.
  - **Skipped:** videos and spreadsheets, by name, before downloading. For files over 100 MB the crawler
    answers Drive's "Download anyway" prompt itself.
  - **Manifest:** lists other embeds (forms, sheets, calendars) as "not saved".
  - **Running a crawl:** Ctrl+C stops cleanly and `--resume` continues. `--save-html` keeps the HTML each
    page was converted from.
  - **Verified:** by the owner on their Google Sites intranet (79 pages, 49 files, 0 failures,
    `check_crawl.py` report), plus `tests/test_crawl.py` and `site2md/tests/test_browser.py`.
  - **Decisions:** keep our own crawler (Firecrawl: AGPL and heavy; Crawl4AI would still need the Drive
    and sign-in handling); save originals instead of converting PDF to Markdown (no text lost).
- **Formats:**
  - PDF text now comes from PDFium (`pypdfium2`, parse-v5). pypdf split Google-Docs PDFs into one word per
    line (93–100% one-word lines vs 4–52% with PDFium), measured on the owner's 38 PDFs.
  - **PowerPoint (.pptx)** is read as one section per slide, with tables and speaker notes. parse-v7: SmartArt
    text (was missing entirely) and hand-drawn org charts (boxes joined by glued connectors; the upper box is
    the parent) become nested lists where each item says what it is under. Unglued lines carry no hierarchy.
  - **PDF view**: `/view` serves a page that draws the PDF with PDF.js 4.10.38 (legacy build, vendored in
    `static/vendor/pdfjs` with cmaps and standard fonts, 4.2 MB, so it works offline), `static/pdfview.js`:
    pages drawn as they scroll into view, a text layer for every page (laid out in the background so Ctrl+F
    finds text anywhere), web and in-document links, zoom and fit, `#page=N`. Its CSP allows only our own
    scripts (eval off; `wasm-unsafe-eval` for JPEG 2000 images) and fetching from this server. Verified in
    Chromium on a 24-page PDF, including the narrow layout. The browser's own viewer is a link on the page.
    Not done: highlighting the result's passage on the page.
  - **Slide view** (`slides.py`): `/view` draws each slide as HTML/CSS/SVG (positions in % of the slide,
    lengths in `cqw`), with no scripts and no external loads (same CSP as the readable view). It covers theme
    colours and fonts, master/layout inheritance, preset shapes (clipped so gradients and pictures fill
    them), custom geometry, connectors, pictures with cropping, tables with their style from
    `tableStyles.xml`, SmartArt from the drawing PowerPoint stores (a nested list when none), and notes.
    Charts, OLE objects, EMF/WMF and effects (shadows, 3-D) are not drawn. It was compared slide by slide
    with LibreOffice renders of 16 public sample decks (Apache POI and LibreOffice test files) and is close
    on all of them. If drawing fails, the text view is shown. `?text=1` gives the text view.
  - The upload limit is 500 MB (was 25 MB, which rejected large manuals).
  - **Upload a folder** (Documents → choose a folder).
  - A crawled page's `source_url` becomes its original location, shown as a link.
- **Indexing speed:** a document's passages are embedded sorted by length (vectors identical), in batches
  of 8 for bge-base. A first micro-benchmark claimed 2.9× for batches of 32; it was run while an LLM trial
  loaded the CPU and was wrong. Re-measured on an idle 4-core VM: 7.4 passages/s sorted against 7.1
  unsorted at batch 8, and 6.3 at batch 32. The Kubernetes ingest at batch 32 took 1,698 s for 8,514
  passages (5.0/s) against 1,590 s before, so batch 32 was reverted.
- **Related documents with every result:** each search result lists what it links to, what refers to it
  (links and mentions) and what is related in meaning, up to 4 per group, each opening that document's
  passage (`relatedDocs` in `app.js`, from `/api/documents/{id}/links`). In the passage view the graph
  is behind "Show as graph". UI walkthrough verified (`docs/screenshots/15-related.png`).
- **Results cover 10 documents** (`FILL_DOCUMENTS` in `search.py`): after the top 10 passages, the best
  passage of each next document is added until 10 documents are listed (often 2–3 of the top passages are
  from one page). Nothing is removed or re-ordered; the added passages are not reranked (branch
  `more_documents`; with a reranker gate they count as weaker matches). New labelled set
  `eval/k8s_relational.jsonl` (24 questions needing 2–3 pages each) and `eval/relational_eval.py`:
  - all needed pages in the results 41.7% → **58.3%** (needed pages found 70% → 81%);
  - Kubernetes 72 questions, fixtures and the multi-hop wiki (`graph_eval.py`): identical numbers with
    and without it (top result, false answers, coverage, evidence recall).
  - Tried and rejected: *one* passage per document (multi-hop wiki: right passage in the top 10 fell
    100% → 58%); two per document (the reranker then promoted wrong pages: 94.6% → 91.1%).
  - Related documents ranked by the question (top 2 per result) reach 79–88% with ~14 documents on screen;
    a plain list of the 13 best documents reaches 92%. On Kubernetes the graph adds little beyond more
    distinct documents; it is kept for browsing and for true multi-hop links (fixture wiki: dependency
    evidence 0.24 → 0.60 with link expansion).
- **Fixture drift found:** the committed fixture results predate parse-v5 (PDFium). Re-run: document
  hit@1 1.0 → 0.982 (k024 "incident response targets" now ranks the post-mortem PDF first) and passage
  hit@1 0.982 → 0.893 (the PDF's passages compete more). Not caused by later changes (same with and
  without the document fill); worth a look when the gate is re-tuned.
- **Kubernetes without descriptions, MiniLM reranker:** top result right 94.6% (96.4% without the
  reranker); 0 false answers. The reranker helps only together with page descriptions (100%).
- **Local language models: trialled** (summaries later shipped as an option, §4f). Details and the resumable plan are in
  `docs/plan-images-and-models.md`. llama.cpp `llama-server`, eval scripts only:
  - **Gemma 4 E2B answers** (`eval/llm_answers.py`): it writes answers well, but is no better as an
    evidence check or ranker (its first cited passage was right 61% of the time vs 95% for search's top
    result).
  - **Gemma-written page summaries in the embeddings:** +1 of 56 questions at rank 1, for ~12 s per page.
    Not worth it.
  - **Vision trial** (`eval/image_set.py`, `eval/image_ocr.py`): stopped by the owner after 4 images. All
    4 were perfect with PaddleOCR-VL-1.6, at 60–70 s per image on 2 cores.
  - **Owner decision (earlier):** ingestion stays fast with one model; images and OCR deferred. **Later
    the same day:** background summaries by a local model wanted, never blocking (§4f).

## 4f. Document summaries by a local language model (owner decision 2026-10-23)

- **What:** `enrich.py`. For each indexed document, one at a time and as soon as it is indexed (low CPU priority; before 2026-10-24 only while no indexing job waited), the
  model gets the title, the headings and the first ~3,000 characters and returns JSON: a 2-sentence summary,
  up to 6 topics, and a title. The title is used only when the document's own is vague ("Overview", a file
  name, "Page 3"; `vague_title`) and the proposal is not (`clearer_title`). Stored in `documents.enrichment`
  per indexed version, model and prompt version; failures retried 3 times; claims expire after 15 min.
- **Where it runs:** where indexing runs: the app (laptop) or `ferret worker` (server profile).
  The app starts llama.cpp's `llama-server` on a free 127.0.0.1 port (half the cores, `-np 1`) and stops it
  on exit, even mid-request. `KS_LLM_URL` points it at another local OpenAI-compatible server instead.
- **Search models on first start (2026-10-25, owner: "from the UI, no cmd commands"):** if the embedding model
  or reranker is not on disk, the app opens at once and loads them in a background thread (`api.py`
  `warm_models`); until then search skips the vector and rerank steps without waiting (reason codes
  `search_model_downloading` / `reranker_downloading`), `/api/status` gives `search_model` and the Search tab
  shows a note. Offline it now starts instead of crashing; Settings → Download retries and re-queues documents
  that failed with `embedding_failed`. The reranker has its own lock, so its download no longer blocks
  embedder lookups. Checked: first start with an empty model folder answered in 2 s; offline start with no
  models showed the note (Playwright); test `test_first_start_while_the_search_model_downloads`.
- **Install:** automatic: with summaries on and no model, the app downloads the default in the background on first start (once per start on failure, never with `KS_OFFLINE`); setup asks (Y downloads now, N writes `KS_ENRICH_ENABLED=false` to `.env`); `ferret download-llm` by hand. llama.cpp release
  **b11836** CPU build (the build used for every measurement here; all 6 platform files checked to exist)
  + `unsloth/gemma-4-E2B-it-GGUF` Q4_K_M (~3.1 GB, Apache-2.0; `--model qwen3.5-0.8b` for Qwen3.5-0.8B, ~0.5 GB).
  Default switched from Qwen to Gemma after the blind review below; Gemma verified starting through
  `LocalServer` (9 s) and summarising a page (50 s with 1 thread).
  **Verified on Linux x64** (download 11 s, server start 3 s, summaries in the app). Windows and macOS
  downloads: file names checked, **not run**.
- **Shown:** results and the passage view, labelled "Summary (local model)", topics as chips that search for
  themselves, clearer title with the original in the tooltip. Never evidence; the gate ignores it.
- **In the embeddings:** only with "Add a line about the whole document to every passage" (`context_chars` > 0) and only for
  documents without their own description: the document is re-indexed once with the summary.
- **Owner's first intranet test (2026-10-23, 135 files, Windows):** found and fixed the same evening:
  start crashed on the 0005 upgrade (above); summary model needed a separate command (now downloaded by the
  app); upload list of one line per file (now one line); no summary stage per document (now in Documents
  and a **Summaries** tab); PDFs blocked by Chrome (`CSP: sandbox` on PDFs → removed; ERR_BLOCKED_BY_CLIENT)
  and "authentication required" on reload (session cookie SameSite Strict → Lax); Markdown shown raw and Word/
  PowerPoint downloaded (now `/api/documents/{id}/view`: rendered page, raw HTML escaped, CSP without
  scripts, links to other documents resolved, Download original); PDF titles like "Microsoft Word - X",
  "X.docx.docx", "PowerPoint Presentation" (parse-v6 `pdf_title`); crawled pages with only a title (site2md
  now skips them, listed under `skipped`); a file Type column. Not verified on Windows by me.
- **Model choice: blind review** (`eval/blind_review.py`, sheet, grades, reasons and key in `eval/results/blind-review*`):
  5 models summarised the same 100 Kubernetes pages (no author descriptions) with the app's exact prompt;
  9 random pages (fixed seed, none read before) graded with each page's summaries shuffled into slots A–E,
  key opened only after the grades were saved. Faithful / specific (0–2) and wrong claims:
  Qwen3.5-0.8B 1.22 / 1.56 / 3 (and 4 of 100 replies not parsable); Qwen3.5-2B 1.89 / 2.00 / 0;
  Gemma 4 E2B 2.00 / 1.67 / 0; Qwen3.5-4B 1.78 / 2.00 / 0; Gemma 4 E4B 2.00 / 1.67 / 0. Median seconds per
  page (4 threads, 2 requests at a time): 6.9 / 18.0 / 17.4 / 32.7 / 35.9. **Gemma 4 E2B chosen**: never wrong,
  and E4B, at twice the time, was no better. Limits: 9 pages, one grader (me), writing styles recognisable.
  The automatic score against author descriptions (`eval/summary_quality.py`) could not separate the models.
- **Live run (demo data, Qwen3.5-0.8B):** 20 of 20 documents summarised, ~5 s each (1 s reading, 4 s writing,
  2 threads while an evaluation used the other cores). Found and fixed: relative model-cache path broke the
  server start; a server that cannot start counted against documents; the model repeated file names as
  "clearer" titles (prompt v2 + `clearer_title`). On a one-line document it embellishes slightly — the
  reason for the label.
- **Topic links: measured, rejected** (`eval/topic_links_eval.py`, results in
  `eval/results/topic-links-kubernetes-nodesc.txt`): linking each top result to the documents sharing its
  model-written topics (IDF-weighted, 2 or 4 per result) finds all needed pages for 62.5% of the 24
  relationship questions (58.3% without), against 91.7% for the related rows already shown, with 16–21
  documents on screen. Same for Qwen and Gemma topics.
- **Not built (each needs a measured test first):** LLM-extracted relations between documents, answer
  writing.

## 4g. Claude skill (owner request 2026-10-23; external AI, opt-in)

`skills/ferret/`:
- `SKILL.md`: the workflow, which is search, read the outcome, search again, read the context or the whole
  document, follow links or impact, then answer only from what was read with citations, abstaining otherwise.
  It also covers management (upload, status, retry, delete with confirmation, reindex) and tells the user once
  that passages go to Anthropic.
- `ks.py`: stdlib client.
  - Sign-in: token or user/password; cache per user and server, 0600.
  - Upload checks write access first and skips other file types.
  - Output survives `| head` and Windows code pages.
- New endpoint `GET /api/documents/{id}/text`: the indexed passages in reading order, permission-checked and
  audited as opened; tested.

Verified: every command run against a fresh demo app on Linux, covering:
- alice and bob permissions;
- an expired token;
- a wrong password;
- an unreachable server;
- a recursive folder upload with an empty and an .exe file.

**Not verified:**
- installed as a skill in a Claude Code session;
- Windows;
- answer quality as a whole. A test set of questions answered through the skill would measure that, and
  is not built.

## 4h. UI pass (owner: "UI could be way better", 2026-10-23)

- Passages are rendered from their Markdown-like source as DOM nodes, never HTML (`prose`/`inline` in app.js):
  soft line breaks joined, so PDF lines become paragraphs; lists, code spans and blocks, tables and headings;
  links show their label. Plain-text files stay monospace as they are.
- The passage view is 780 px wide with a backdrop; Esc or a click outside closes it. The evidence comes first,
  then the model's summary, then connections, all in one scroll area. It has Open document and Download
  buttons. The `file:///` footer is gone; only http(s) originals are linked.
- Result cards: the title opens the document; the location drops the repeated title and shows the version only
  when there is more than one; outcome icons.
- Search start: the number of documents and collections the user can read, three kinds of query, and **/** to
  focus.
- Verified: the UI walkthrough, which now asserts rendered code spans, Esc, and the hint; no console errors.
  Screenshots refreshed. **Not** reviewed by the owner yet. Documents, Summaries and admin tabs unchanged in
  this pass.

- **Owner report 2026-10-23 (Windows):** "search failed": a query made only of stop words or punctuation
  ("how to do it") has no lexemes. The snippet query then passed `''` to `to_tsquery`, a syntax error, and
  the result was a 503. It now passes an empty string, which gives an empty query. Regression test added;
  it failed before the fix.
- Topic chips now list the documents with that topic (Summaries tab, case-insensitive exact match) instead of
  searching for the word. Searching "IT" (a stop word, never indexed as a keyword) could only match by
  meaning. The walkthrough checks it.

## 4l. Owner's second intranet test (2026-10-24): fixes

- **"View passage" 404:** a re-index (new parser, settings, a summary added to the passages) replaces a
  document's passages, so results on screen pointed at deleted rows. Opening one now re-runs the search and
  says why. Passage ids are not kept stable across re-indexes (deliberately simple).
- **"COOKabir":** PowerPoint (`a:br`) and Word (`w:br`) line breaks inside a paragraph were dropped. parse-v8
  re-indexes affected documents automatically. The deck view matches slide anchors by slide number and
  starts a linked slide below its sticky header (which the slides used to cover).
- **Ordering:** Documents sort by any column (name by default, remembered per browser); uploads are queued in
  folder and natural name order; re-index jobs and summaries follow the order documents were added.
- **Summaries during indexing** (owner request): no longer wait for an empty queue. The model server runs at
  nice 19 / idle priority class with `--poll 0`. Measured on 4 cores, embedding 151 passages: 17.5 s alone,
  37.1 s with the model busy at normal priority, 27.9 s with llama.cpp's `--prio -1`, **17.6 s at nice 19**;
  a summary alone 15.6 s vs 16.2 s. Live: summaries started while 8 of 19 documents still waited. Windows idle
  class **not verified**.
- **Summary model in Settings:** Gemma 4 E2B (default), Qwen3.5-2B (added: blind review 1.89/2.00/0 wrong,
  types not measured), Qwen3.5-0.8B; downloaded in the background after a disk-space check (found when a
  switch here started a 3.1 GB download onto a 2.6 GB disk).
- **Model defaults:** shipped defaults are bge-base + MiniLM (recommended in the lists now). The owner's
  install ran Qwen3-Embedding-0.6B + bge-reranker-base, chosen in their Settings earlier. **Measured on
  Kubernetes** (docs/evaluation.md §7): Qwen3 right page first 0.893 (0.929 with bge-reranker-base) vs
  bge-base 0.964, answered 0.875 vs 0.911, 1.0–2.6 s vs 0.27 s per search → recommend bge-base. Owner's
  own documents still unmeasured.

## 4n. Model downloads and loading (owner request 2026-10-24)

- Settings lists every embedding model, reranker and summary model with "downloaded / Download / in use"
  and a spinner while downloading or loading (`GET /api/admin/models`, `POST /api/admin/models/download`;
  fastembed `download_model` fetches files without loading; the presence check reads the cache on disk,
  because fastembed's own check logs an error per missing model). Tested (`test_model_list_and_download_requests`)
  and clicked through in Chromium (a 640 MB embedding model downloaded, spinner then "downloaded").
- Startup already loaded the embedding model and reranker before accepting traffic; it now also loads the
  in-memory vector index (it loaded on the first search: slow on a large archive), and `knowledge-search
  worker` loads its embedding model at start. The summary model server already started with the job loop.

## 4o. Folders, MCP that can change things, and the crawler's page (owner requests 2026-10-24)

- **Synced folders** (`folders.py`, migration 0008, Documents → Synced folders, admin): add a folder by
  its path on the server; it is walked recursively (skips names starting with `.` or `_`, keeps the chosen
  types: markdown, text, html, word, powerpoint, pdf), and **Sync** takes in only what changed and removes
  documents whose file is gone. Browser folder uploads now send each file's path, so documents are keyed by
  path in the folder (`repo-kit/docs/modules/index.md`); a re-upload adds new files, versions changed ones,
  skips unchanged ones, and never duplicates. Older documents keyed by bare file name are re-keyed on their
  first path upload. A browser re-upload cannot see deletions; a synced folder can.
- **Tokens:** access tokens are read-only unless created with *May also make changes* (migration 0009,
  existing tokens became read-only). A read-only token also cannot write through the REST API.
- **MCP can do the admin work** (`mcp.py`): status, list/add/retry/delete documents, add/list/sync folders,
  `crawl_website` / `list_crawls` / `send_crawl` (`crawls.py`, the same site2md App as the Crawler tab;
  into an existing collection or a `new_collection` created when the copy is done), create collections, read/change settings, download
  models, reindex. Tools are listed per caller (reader / writer / admin); non-read tools are marked not
  read-only so clients ask before calling. Tests: `tests/test_mcp_manage.py`, `tests/test_folders.py`.
- **site2md page** (`site2md/src/site2md/ui.py`, `static/`; plan `docs/plan-site2md-ui.md`): the launchers
  now open a page on 127.0.0.1 (one-time key, Host check, CSP without inline code): Your copies → Copy a
  website (instant address check) → sign in in a browser window (the page notices by itself; no console)
  → progress bars per site section, Pause/Stop/Continue → done page with what was not copied grouped by
  reason and *Try these again* → *Send to Ferret* (local admin: synced folder; otherwise uploads
  keyed by path). Update copies into `<name>.new` and swaps when finished. Three sign-in pages in a row stop
  the copy as "sign-in expired" instead of recording failures. Verified: Playwright walkthrough at 1280 and
  390 px (`$scratchpad/s2m/`, not committed), a real send into a running app (6 files queued as a synced
  folder, HTML and manifest left out), `tests/test_site2md_ui.py`. **Not verified:** a real sign-in on
  Windows/macOS with installed Chrome/Edge (owner to try).
- **Original HTML (owner asked 2026-10-24):** optional, saved to `<name>-html/` beside the copy, never
  indexed. Reason: the Markdown already keeps what retrieval uses (headings → section paths, lists, tables,
  code, links → graph), and indexing both would put every page in the results twice. If a page loses
  structure in Markdown (e.g. merged table cells), fix the converter (`site2md/html.py`), measured on that page.
- **Rule for future content types (owner, 2026-10-24):** there is one ingestion path — crawler → folder →
  `folders.sync` → `ingest.submit`, and MCP → REST with the caller's token. A new type (images, for vision)
  is added once in `ingest.MEDIA_TYPES` / `folders.TYPES` / `ks.py` / the upload type list, and site2md must
  save it (today it saves pages and PDF/Word/PowerPoint/text attachments, **not images**). Planned vision
  design: a local vision model writes text for each image (OCR + a description of diagrams and their
  relations) in a background job; that text is embedded with the normal text embedder and cited back to the
  image. Not a multimodal embedding model: those are weaker on text retrieval, which is most of the corpus.
  The GLM-4.6V-Flash vs Gemma 4 E2B image comparison (`eval/image_ocr.py`) decides the model.
- **Crawler tab in the app (owner request 2026-10-24):** admin tab framing site2md's own page at `/crawler/`,
  API `/api/admin/crawler/*` → `site2md.ui.App.handle` (one dispatcher shared with the stand-alone server), the
  admin's session instead of a token; *Add to a collection* (existing or New collection…) makes the copy a
  synced folder; an Update of a copy that is a synced folder is synced again when it finishes (`App.on_done`).
  Copies folder: `<data>/crawls`, changeable on the page (`<data>/crawler.json`; stand-alone:
  `~/.site2md/config.json`). The page's "Open Documents" posts a message to the app (note: `app.js` has its
  own `location()` function, so the listener uses `window.location`). Verified: Playwright copy → new
  collection → Documents on the demo; `tests/test_mcp_manage.py` (MCP crawl into existing and new collections,
  `send_crawl`, the tab's API with a session).
- **Following links (owner, 2026-10-24: "no links unopened"):** site2md `--follow linked` (default) follows
  links in the page text to other sections/hosts and copies the section (start folder) they lead to; links in
  nav/header/footer/aside, ARIA landmark roles, `<link>` and hreflang alternates stay on-site; social/video
  hosts are never followed; the site's own pages are fetched before linked sections. `everything` follows every
  link up to the page limit; `site` stays on the site. Disagreed with making "everything" the default: from a
  product page it walks all of aws.amazon.com through menus. Verified on aws.amazon.com/transform/ (the guide
  card → docs.aws.amazon.com/transform/latest/userguide/ copied: 43 + 27 pages at a 150-page cap).
- **Demo gotcha:** this container periodically resets `/tmp/claude-0` to mode 700, and the embedded Postgres
  (non-root) then loses its data folder ("Permission denied" PANIC). Keep long-running demo data outside the
  scratchpad (`/home/user/ks-demo`).
- **Installer:** still TODO for both apps (plans: `docs/plan-installer.md`, `plan-release.md`, `plan-docker.md`).

## 4m. MCP server for AI assistants (owner request 2026-10-24)

- **What:** `POST /mcp` inside the app (`mcp.py`, ~230 lines, no new service; runtime dependency
  `httpx` only). Streamable HTTP, stateless, JSON replies, protocols 2025-06-18 / 2025-03-26 / 2024-11-05.
  Five read-only tools: search, read_passage, read_document, document_links, list_collections. Each tool
  calls the app's own REST route in-process with the caller's token, so permissions, the evidence gate and
  the audit log apply unchanged; nothing is duplicated. Server instructions tell the assistant to cite,
  abstain on no_adequate_evidence and treat summaries as non-evidence.
- **Tokens:** an **AI assistants** page (every user) creates a named token (30/90/365 days, shown once),
  lists and revokes them, and prints ready-to-paste setup for Claude Code, Claude Desktop, Cursor, VS Code
  and others. Tokens are labelled sessions (migration 0007); only a browser session can create one (an
  assistant holding a token gets 403), so an assistant cannot mint itself more. Changing a user's groups,
  disabling them or resetting the password revokes their tokens too (existing sign-out-everywhere rule).
- **Claude Desktop** only starts local commands: `ferret mcp` relays stdin/stdout to the endpoint
  (KS_URL, KS_TOKEN). It needs Ferret installed on that computer.
- **Verified:** `tests/test_mcp.py` (handshake, tools scoped to the user incl. HR hidden from bob, token
  create/list/revoke, 403 for bearer-created tokens, the official `mcp` 2.3.0 SDK client connecting over
  Streamable HTTP, the stdio bridge against a live uvicorn); `scripts/ui_check.py` creates a token in the UI,
  checks every client's snippet, runs an MCP search with it and revokes it. **Not verified:** a real Claude
  Desktop / Cursor / VS Code session (none in the container).
- **MCP vs the skill vs raw API (owner asked):** agree MCP is the better default for assistants: tool list
  and schemas are discovered, auth is set once in the client config, and it works across clients. The
  skill stays for Claude Code users who want admin tasks (upload, retry, delete), which MCP deliberately
  does not expose. Cost of MCP: its tool definitions sit in the assistant's context every turn (~1.2k
  characters here, kept short).

## 4k. Labels by the local model: document type, consistent topics, overnight hours (owner request 2026-10-23)

- Prompt v3 (`enrich.py`) adds a **document type** from a described list of 16 (runbook, how-to,
  troubleshooting, reference, concept, design, decision record, policy, process, release notes, incident
  report, meeting notes, org chart, onboarding, presentation, other). It is shown with results (labelled
  as the model's) and as a Type column and filter on Documents and Summaries. It is **not** used in
  ranking: no measurement says it helps.
- **Consistent topics:** the model sees its collection's 40 most used topics and is asked to reuse
  their names; spelling variants (case, hyphens, a plural) take the known spelling (`topic_key`), and the
  topic filter matches the same way.
- **Upgrade cost:** when only the prompt changed, the stored summary is kept, so documents whose passages
  carry it (context_chars) are not re-indexed. Existing installs relabel every document once in the
  background.
- **Hours:** the setting `enrich_hours` ("22-6", local time; empty = any time). Outside it the job waits
  and stops the model server to free its memory. Tested (window wrap, validation, the job waiting).
- **Measured** (`eval/llm_labels.py`, gold `eval/k8s_doc_types.jsonl`: 451 Kubernetes pages labelled by
  where kubernetes/website files them; the same 42-page stratified sample for each model):

  | Model | Exact, all 42 | Without the 10 glossary entries |
  |---|---|---|
  | Gemma 4 E2B (default) | 64.3% | **78.1%** (25/32) |
  | Qwen3.5-0.8B | 57.1% | 56.2% (18/32); never says "concept" |

  **Gemma 4 E4B** (2026-10-24, every 3rd page of the sample: 14 pages, `--every 3`): exact 9/14 vs E2B
  8/14 on the same pages; compatible 10 vs 8 (E4B better on 3 pages, worse on 1: too few to call); median
  40.7 s vs 17.4 s per page, ~8.7 GB resident. Offered in Settings as the server choice; E2B stays default.

  **More candidates (2026-10-24, owner: "other small summarizers", thinking mode).** The same 14 pages
  (`eval/results/llm-labels-kubernetes-*-every3.json`), with exact and close types counted together:
  E4B 10 · Ministral 3 3B 10 (all 4 concept pages called how-to) · Qwen3.5-4B 9 · SmolLM3 3B 9 (6 exact) ·
  E2B with thinking 9 (4–7x slower) · **E2B 8** · Phi-4-mini 8 · Qwen3.5-2B 7 · Granite 4.0 micro 7.
  Fourteen pages cannot separate these, so the finalists ran on the **100 described pages** with the
  app's current prompt (`eval/summary_quality.py`, `eval/results/summary-quality-app3.json`):

  | Model | Agrees with author description | Picks out its own page (of 228) | Median s/page | Type, 15 non-glossary pages |
  |---|---|---|---|---|
  | **Gemma 4 E2B** | **0.800** | 75% | **18** | 10 |
  | E2B with thinking | 0.796 | 76% | 78 | 11 |
  | Ministral 3 3B | 0.771 | 65% | 31 | 4 |

  **Decision:** E2B stays the default. Thinking mode is not offered: the summaries are the same and it is
  4.3x slower. Ministral is not added: its summaries are worse and it is slower. Its type score on these
  pages (28 of 52 glossary entries called "reference") reflects the gold filing, not better
  understanding. The 100 pages are mostly glossary entries (52 of the 67 with a gold type), so document
  types get a balanced 105-page sample (30 tasks, 30 concepts, 30 reference, 15 tutorials), E2B with and
  without thinking. **E2B: 72.4% exact, 76.2% close; without the 25 glossary entries 90% exact, 95%
  close (76 of 80)** (`llm-labels-kubernetes-gemma-4-e2b-ps30.json`). With E2B already at 95% on ordinary
  pages, thinking could add only a few pages at 4x the time, so that run was stopped and thinking is not
  offered.
  Not yet run (planned, blocked by disk in the cloud session): GLM-4.6V-Flash 9B (Z.ai),
  DeepSeek-R1-0528-Qwen3-8B, Gemma 4 12B.

  **Vision (Gemma 4 E2B + its mmproj, 20–56 s per image on CPU, `$scratchpad/vision`, not committed):**
  - The text inside images is read accurately: every label of a flow diagram, including `ERR-PAY-4012`
    and `POST /orders`, and nearly all the text of a Settings screenshot.
  - Arrows are not: about half the relations in the diagram were wrong (it treated an edge label as a
    box), and most relations it listed for the graph screenshot were wrong or invented.
  - So for future image indexing, use it for the text and a one-line description only, never for
    "A → B" relations.

  Glossary entries (one-paragraph definitions filed under reference) are called "concept" by Gemma 8
  times out of 10, which is defensible; the gold mapping is the weak point there. Unmeasured: business
  types (policy, process, incident report) on a real company corpus; on the 16 demo documents Qwen
  called all three policies "reference" (before the type descriptions were added).
- **Not pursued: a model-written context line per passage** (Anthropic-style contextual retrieval). The
  document-level version (the summary in every passage, `context_chars`) is already measured and off by
  default (§4d). A per-passage call costs about a document call (Gemma 4 E2B: median 39 s on CPU), so an
  archive with millions of passages would take years of CPU time. Revisit only with a GPU and only if the
  owner's labelled questions show misses that the passage text alone cannot explain.

## 4j. Relations between documents written by the local model: measured, not shipped (owner question 2026-10-23)

Owner: "just based on content can we setup relation between docs? Using LLM." Tried with Gemma 4 E2B
(`eval/llm_relations.py`). For the top 3 results of each of the 24 relational questions, the model read the
document and 15 candidates (its links, mentions and nearest documents by meaning: title and opening line),
and chose relations with a type and an evidence phrase. A relation whose evidence is not verbatim in the
document was dropped. It also named topics the document relies on but does not explain; each was looked up
with search.
- All needed pages shown: **66.7%** with 14 documents on screen (75.0% at 18 with the looked-up topics).
  Today's related rows: 91.7% (13 best documents, or all 33); ranked top 2 per group: 87.5% at 15.
- 154 of 289 proposed relations had no verbatim evidence (53%); 39 s per document (CPU), which would be
  ~18 days for a 40k-document archive.
- A sample of 25 kept relations: about 18 restate a Markdown link the link graph already has, and roughly
  10 have a wrong or vague type ("configure-persistent-volume-storage *part of* pods", evidence "create a
  /mnt/data directory").
- **Decision: not shipped.** It is worse than ranking the links, mentions and similarity the app already
  has, and it costs far more. Kept: the script and results (`eval/results/llm-relations-*.json`,
  `relations-*.json` with raw replies). Worth retrying only with a much stronger local model, or with typed
  links written by authors (front matter `depends_on`, already used).

## 4i. Redesign (owner: "use the impeccable and taste skill extensively", 2026-10-23)

Designed from the taste skill's app-screen rules (dealt style **framed sheet**, `draw.mjs --app --kind devtools`)
and impeccable's operate-mode and craft-floor rules; both repos were read as guidance, nothing from them runs.
- Shell: a blue-grey frame holds the sidebar (product mark and name, labelled nav with 16 px outline icons,
  an Administration group, the account at the bottom) and a 48 px top bar with the search box (on every page,
  **/** focuses it). Pages sit on a white sheet with a 1 px edge and 16 px corners, no shadows at rest. On
  narrow windows the sidebar folds behind a menu button.
- Type: IBM Plex Sans, with Plex Mono for file names, keys and config values. Both are vendored under
  `static/vendor/fonts` (Latin subset, OFL), so the app stays offline. The viewer pages use the same fonts
  and colours. Dark theme by `prefers-color-scheme`.
- Colour sits on data only: highlighted words, status pills, one green evidence mark per supported result.
  The primary button is near-black; the teal accent is the logo, focus ring and graph.
- Search: a collection menu in place of chips; results are ruled rows with the rank in a mono gutter; the
  outcome is a line with a status dot (tinted only for conflicts, incomplete coverage and failures).
- Documents: Upload files / Upload a folder buttons, or drop anywhere on the page (folders read through the
  entries API). State tabs with counts (All, Indexing, Failed, Outdated settings) and a name filter. One row
  menu (Open, Download original, Retry, Delete) in place of a red Delete on every row; relative times. Rows
  become two-line items on a phone.
- Index status: a KPI strip (documents, indexed, waiting, failed, outdated), a per-collection table and the
  configuration. Settings: a label column beside a tinted panel per section, with a sticky save bar. Users &
  access: grants as pills with a remove button.
- Verified: `scripts/ui_check.py` passes end to end with no console errors; there were screenshot reviews
  at 1440 px, 390 px and in dark; the phone width has no horizontal scroll. 99 tests pass. Screenshots in
  `docs/screenshots/` are regenerated. **Not reviewed by the owner yet.**

## 4p. Local session on the owner's Windows laptop (2026-10-25)

- **Baseline on Windows 11:** the tests had never been run on Windows. 9 of 132 failed: two pgvector-in-SQL
  parity tests crashed the embedded PostgreSQL (the known Windows pgvector bug; three later tests then failed
  on the database in recovery), three summary tests wrote a fake `llama-server` without `.exe`, and `.js`
  files were served as `application/javascript` (Windows takes the type from the registry). Fixed: the two
  SQL parity tests are skipped on Windows with the embedded database, the fake server gets `.exe`, and
  `.js` is registered as `text/javascript` in `api.py`. Now **130 passed, 2 skipped** on Windows.
- The tests need `bge-small` and the MiniLM reranker; plain `ferret download-model` fetches the shipped
  defaults (bge-base, jina turbo), so the instructions now name the tests' models.
- **UI walkthrough (TODO 25) fixed:** `scripts/ui_check.py` now creates a group under Groups, gives it access
  under Collections (group menu, read & write, **Give access**) and ticks it in the new user's group picker.
  Run end to end on Windows against a fresh demo server (Edge via `CHROMIUM_PATH`, summaries off): every
  step passed, no horizontal overflow on mobile. Console errors now print the failing URL.
- **Summaries counts and the 1,000-row cap (plan-summaries-status, TODO 1 and 9): built.** The Documents tab
  is filtered, sorted, counted and paged by the server (`/api/documents` with `status`, `q`, `doc_type`,
  `sort`, `desc`, `limit`, `offset` and an `X-Total-Count` header; `/api/documents/counts`); API clients that
  pass nothing still get the newest 1,000. The Summaries tab has the Index status tiles (indexed, summarised,
  waiting, writing, could not), a per-collection table from `enrich.by_collection` (the worker's own
  `PENDING` rule, shared as `enrich.STAGE`), the time left at the median pace marked as an estimate, "paused
  until" outside the configured hours, and Writing / Could not / Summarised sub-tabs served 100 at a time by
  `/api/summaries` (name, topic text or exact topic). Tests: 1,050 documents page and count correctly and
  stay invisible to a user without access; the Summaries counts equal `enrich.status()`; no pace before the
  first summary. Checked in the browser: the walkthrough with a stand-in local model (2 s per summary), and
  150 extra rows: 100 shown, "Show more" shows all 168, Failed + "bulk 14" shows 11. Not measured: response
  time on a 40k-document archive.
- **Tables kept as tables (plan-tables steps 1–4, TODO 13): built.** One grid writer
  (`site2md.html.table_markdown`) for web pages, Word and PowerPoint: GFM with a header row, merged cells
  (`rowspan`/`colspan`, `w:vMerge`/`w:gridSpan`, `rowSpan`/`gridSpan`) repeated into every row and column they
  cover, `|` escaped, paragraphs in a cell joined with `<br>`, the caption bold above. Tables with one row or
  one column, a nested table, or headings/lists/code in a cell stay text. Passages: a table that fits stays in
  one passage; a longer one is split between rows with the caption and header repeated; no overlap is carried
  into or out of a table. Reading view and passage drawer show the header row and the `<br>` line breaks (no
  other HTML). Parser `parse-v9`, chunker `chunk-v3`: existing documents show as outdated until Reindex.
  Crawler 1.1.0 (re-crawl, or Update for copies that kept their HTML). Tests: `tests/test_tables.py` (AWS
  shape, header rules, layout tables, Word and PowerPoint merges, a 200-row table in passages, the reading
  view); the walkthrough uploads an AWS-shaped page and checks the passage shows a header row and the merged
  cell twice. **Step 5 (index rows as "column: value") not measured** (TODO 26).
- **Large documents (plan-large-documents steps 2–3, TODO 8): built.** Two in-process indexing workers by
  default (`KS_INPROCESS_WORKERS=2`), sharing the model session; a re-index embeds only passages whose
  embedding text or model changed (`chunks.embed_hash`, migration 0010), so an edit, a folder or website
  update, or a reindex that leaves passage text alone is nearly free. Test: one edited section re-embeds one
  of four passages and the kept vectors equal a full re-embed (cosine above 0.999; batch padding changes the last digits, most on macOS). Measured on the owner's laptop:
  12th Gen Intel Core i5-12500H laptop (12 cores, 16 threads; Windows 11), bge-base, a generated 400-passage Markdown file, two-worker default: a 1-passage file uploaded right after it was indexed after **1.2 s** (one worker: **71.8 s**, behind the large one); the large file took 72.3 s (one worker: 71.8 s). Uploading the 400-passage file again with one section edited: **1.0 s** (first upload 58.4 s), because 399 passages kept their vectors. A changed document summary (context line) still re-embeds every passage, as it should.
- **GPU probe (plan-gpu step 1) on the laptop:** Intel Iris Xe (driver 31.0.101.4575), i5-12500H, Windows 11; bge-base, 400 passages cut by the app (`data/app-passages.json`, median 740 characters), sorted by length as the app batches them; same passages in both runs. CPU: the app's own environment (onnxruntime 1.31). GPU: `onnxruntime-directml` 1.24.4 in its own environment (`scripts/gpu_probe.py`). Passages/s at batch 8 / 32 / 64 / 128: **CPU 5.2 / 4.5 / 4.0 / 3.6, GPU (DirectML) 1.0 / 2.2 / 2.7 / out of GPU memory** (DirectML "Not enough memory resources" at batch 128). Larger batches help the GPU, but its best (2.7) is about half the CPU's best (5.2, at the app's batch 8). Parity, from an earlier run on paragraph input: cosine between CPU and GPU vectors min 0.999987, mean 0.999992, which passes the bar. So the embedding switch is not worth building for this laptop. (An earlier version of this note said CPU 3.4 vs GPU 1.2; that run used unsorted paragraphs from the docs folder, so padding slowed both sides.) Not measured: OpenVINO and the reranker.
- **Step 1 (several embedding sessions) measured on the laptop:** best 4 × 4 threads 6.9 against 6.5
  passages/s for one session (evaluation.md §10): no worthwhile gain, not built.
- **Published names decided by the owner (2026-10-25):** PyPI `ferret-search`, image
  `ghcr.io/mayankpunghal/ferret-search` (TODO 24 closed).
- **CI (plan-release step 1):** `.github/workflows/ci.yml` (tests on three systems, UI walkthrough on Linux).
  First runs: Linux and the walkthrough passed; Windows failed at setup because the embedded PostgreSQL's
  `initdb` cannot create its folder under the runner's Temp ("File exists"), so pytest now uses
  `--basetemp` under `runner.temp`; macOS failed the new embed-reuse test on a 1e-4 tolerance (batch
  padding), now a cosine check.
- **Settings added (owner asks, 2026-10-25):** **Summary length** (`summary_length`: short = 2 sentences,
  medium = 3–4, long = a paragraph; the prompt asks for it, the enrichment stores `length`, and a change
  makes every summary pending again, in the worker and in the Summaries counts); **Take that line from**
  (`context_source`: description = the old order, summary only, first paragraph only; part of the index
  signature, so a change re-indexes); **Run it on CPU / GPU** for the local language model (`llm_device`,
  see plan-gpu status and evaluation.md §11). Smoke test on the laptop: the Vulkan build downloaded,
  `--list-devices` shows the Iris Xe, the server started with `-ngl 99` and answered. Tests: length rewrite,
  the three line sources, GPU-to-CPU fallback.

- **Parallel embedding (TODO 3) closed:** measured twice with no worthwhile gain (4 cores in the cloud
  session; 6.5 vs 6.9 passages/s with 4 sessions on the laptop, evaluation.md §10). Two in-process workers
  (built) already keep small files from waiting behind a large one. Re-open only with a 16–32-core server.
- **Hardware defaults (owner ask, 2026-10-25): built.** `system.py` checks once, on the first start: total
  memory (or the container's limit) and graphics cards (Windows: `Win32_VideoController`; Linux: the vendor
  under `/sys/class/drm` when `/dev/dri` exists). Under 8 GB, summaries default to off; with 15 GB or more
  and a GPU llama.cpp's Vulkan build can use, the summary model defaults to the GPU. Settings already saved
  or set by a `KS_` variable are left alone; Settings shows what was found. The thresholds are estimates
  from model sizes, not measured on 8 GB machines. Laptop: 15.6 GB, Iris Xe, chose GPU.
- **PyPI and Docker (plan-release step 2 partly, steps 3 and plan-docker 1–3, 6, 7): built.** Distributions
  renamed `ferret-search` and `ferret-crawler` (import names unchanged; `site2md` is taken on PyPI). An
  installed package keeps data in `~/Ferret` (a clone keeps `./data`); `ferret` alone starts and opens the
  browser. Checked: both wheels install in a clean venv outside the repository and the app starts. Docker:
  bge-base (was bge-small), `libgomp1`, `/models` writable, no `KS_OFFLINE` by default, one-container
  mode documented; CI builds the image, checks `ldd llama-server`, starts one container and searches.
  `release.yml` publishes both (see "How to release" below).
- **Held for a later phase (owner):** macOS app, invites, Ask, images.
- **v1.0.0 released (2026-10-25):** `ferret-search` 1.0.0 and `ferret-crawler` 1.1.0 on PyPI (trusted
  publishing; the crawler has its own environment `pypi-crawler` because PyPI allows one pending publisher
  per workflow and environment), the image `ghcr.io/mayankpunghal/ferret-search` public (anonymous pull
  checked).
- **Windows installer (plan-installer, owner ask 2026-10-25): built.** Option A of the plan, not PyInstaller:
  `packaging/build_windows.ps1` copies uv's standalone Python 3.13 into `dist\Ferret\python` and installs the
  locked dependencies and both wheels into it (313 MB); `packaging/installer.iss` (Inno Setup, per-user, no
  admin prompt, holler's shape) makes `Ferret-Setup-<version>.exe` (66 MB). Shortcuts start
  `pythonw -m knowledge_search.tray`: the server runs in the tray process, the browser opens when it answers,
  menu Open Ferret · Start when I sign in (the same HKCU Run value the installer's task writes) · Quit; a
  second start only opens the browser; port 8000 taken is shown in the tray. Data in `%USERPROFILE%\Ferret`,
  asked about on uninstall. Upgrades and uninstall stop every process started from the install folder.
  `windows-app.yml` builds, installs silently, starts the app, checks `/healthz`, uninstalls, and attaches the
  installer to the release (called by `release.yml`). Not built: code signing (SmartScreen warns; README says "More info
  → Run anyway").
- **1.0.2 (2026-10-25):** port fallback 8000–8019 (the chosen port is written to `%USERPROFILE%\Ferret\port`);
  "Pause indexing and summaries" (tray item + Settings checkbox, `indexing_paused` in runtime settings; the
  worker and the enricher skip their claim while set); updates (`updates.py`): Settings → Updates shows this
  version and the newest GitHub release, "Install update" (Windows app only) downloads the installer and runs it
  with `/SILENT /UPDATE=1`; the tray checks daily and its "Updates…" item opens `/#updates`. pip/Docker
  installs see the upgrade commands instead. Gotcha: a process Inno Setup starts inherits a "redirection
  guard" that cannot traverse the HF cache's symlinks (WinError 448), so `[Run]` starts Ferret through
  `explorer.exe` with the Start-menu shortcut.

- **How to release (2026-10-25):** the version in `pyproject.toml` decides. Either GitHub → Actions →
  Release → Run workflow, typing the new version (plus a crawler version if `site2md/` changed), or push a
  commit to main that changes the version. `scripts/release_check.py` refuses a version that is not newer, a
  wrong `ferret-crawler==` pin, or a changed crawler with an unchanged version; then the whole CI must pass on
  that commit; only then the tag `v<version>`, the GitHub release, Docker (GHCR), both PyPI packages and the
  Windows installer. Pushing a tag by hand no longer releases anything.

## 4q. Ask: a chat answered by the local model from search's passages (owner request 2026-10-26)

Owner: "Add a chat window that runs a small local model for llm based replies after using rag search … allow
for a few strong local models to be downloadable maybe the same ones we have listed for the summarization —
we already started some work in a separate branch but lets take reference from that and add in MAIN only."
The WIP branch was `feature/ask` (a single-shot Q&A, no history, no model choice); this build takes its
shape (search first, citations, no evidence means no model call) and adds the window, models and history.
Nothing from that branch was merged.

- **`ask.py`** (new): `POST /api/ask` (whole answer, for assistants and tests), `POST /api/ask/stream`
  (server-sent events) and `GET /api/ask/models`, plus the conversation routes below.
  Search runs with the caller's permissions and the runtime gate; **only `supported` passages** (max 6, 1,500
  chars each) are numbered and sent, so the search — not the model — decides whether there is evidence
  (plan-answers.md, measured: letting the model veto cost 28 points answered). No supported passage means
  **no model call at all** and the search's own message. The model writes plain text (Markdown allowed) and
  replies `NOT_IN_DOCUMENTS` when the passages do not answer, held back from the stream until the sentinel is
  settled and then shown as "No answer from your documents".
- **Conversations are saved** (migration `0011_ask_conversations.sql`, `ask_conversations` + `ask_messages`):
  `GET/POST /api/ask/conversations`, `GET/PATCH/DELETE /api/ask/conversations/{id}`, all filtered by the
  caller's `user_id` (another user's id answers 404). The first question names the conversation until it is
  renamed; `?q=` searches titles and what was said. Each question reloads the last 6 turns from the store, so a
  reload, another device or a follow-up all see the thread; those turns are marked in the prompt as context
  that is **never evidence**.
- **Streaming, thinking, stop:** `/api/ask/stream` sends `search` (outcome, citations, passage count), then
  `text` / `thinking` pieces as llama-server writes them, then `done` (model, seconds, answered) — or `error`.
  "Think first" passes `chat_template_kwargs.enable_thinking` and streams `reasoning_content` into a collapsed
  card. Stop aborts the fetch; the server's `finally` keeps what was written, so a stopped answer is not lost.
  One answer at a time (`-np 1`, one lock); the answer is saved before `done`, so a reload shows it.
- **Models:** the same `enrich.LLMS` list as the summaries, chosen per browser (localStorage); "" = whatever
  the summaries run, so nothing extra is loaded. A different model starts `ask.py`'s own llama-server (one at
  a time, stopped when another is chosen); `KS_LLM_URL` is used when set. `enrich.LLMS` gained
  **`qwen3.5-4b`** (`unsloth/Qwen3.5-4B-GGUF`, `Qwen3.5-4B-Q4_K_M.gguf`, 2.74 GB, file name checked on Hugging
  Face) as the strong option; its answer quality is **not measured** yet. Downloading is unchanged
  (Settings → Models, or `ferret download-llm`).
- **UI:** Ask tab (`#tab-ask`) — conversation list (new, search, open, rename, delete) beside the chat;
  answers render as Markdown (the passage view's `prose()`); each answer carries a collapsible "Searched '…' ·
  N passages" card, a "Thinking" card when asked, the answer with `[n]` buttons that open that passage, a
  sources row, and outcome · model · seconds · Copy · Retry. Composer: Enter sends, Shift+Enter newline,
  model picker, "Think first", collection scope, Ask/Stop. Phone width stacks the list above the chat.
- **MCP:** a new read-only `ask` tool (question, collections, think) calls the app's own `/api/ask`, so
  permissions and the gate apply; it returns the answer, its citations and the outcome.
- **Verified:** `tests/test_ask.py` (9: no evidence → no model call; citations and the prompt the model got;
  the sentinel is a refusal; thinking is kept; history caps; a saved conversation's turns reach the next
  question; another user's conversation is 404; the SSE event sequence and the saved answer; model list + MCP
  tool). Full suite **157 passed, 2 skipped**. **The prompt against the real Gemma 4 E2B** (installed copy,
  CPU): ERR-PAY-4012 → "…page the payments on-call engineer and check the processor status page [1]", and an
  unanswerable question → `answered: false`. **The window in Chromium** against a live instance and a streaming
  fake model: streamed answer with [1], step card and sources; a thinking card; "No answer from your
  documents."; the conversation listed after a reload with its turns, renamed and deleted; no JS errors; no
  phone overflow. The whole `scripts/ui_check.py` walkthrough (now with the Ask assertions) passes end to end
  with no console errors. **Not measured:** answer quality on the labelled sets, E4B/Qwen3.5-4B for answers, latency
  on a laptop (E2B: ~15 s per answer in earlier runs).
- **Not built:** **pictures** (per-message image upload, OCR/description by a vision model: needs the model's
  mmproj, 20–56 s per image on CPU, and the measured arrows/relations are unreliable — TODO item 2 / item 10),
  and editing an earlier message (Retry exists). Everything else in plan-answers.md step 3 shipped.



## 5. Decisions made this session (details: docs/architecture.md)

Embedded PostgreSQL + vendored Windows DLL; in-memory vector search for laptops; runtime-editable
settings with index signatures (outdated documents are reported, never silently mixed); per-model tuned
thresholds; reranker optional and off unless it measurably helps; first-run admin in the browser;
auto-migrations on start; GraphRAG deferred in favour of a deterministic link-graph research track.

**Answered or decided with the owner (moved from TODO.md, so they are not asked again without new data):**

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

## 6. Explored vs unexplored

| Explored (outcome) | Unexplored / open |
|---|---|
| Existing products (Onyx/RAGFlow/R2R) — rejected for v1 | Connectors (Confluence, SharePoint, Git, Jira) |
| Docker-based laptop install — replaced (failed on the owner's Windows) | OIDC / company SSO |
| Embedded PG on Windows — two platform bugs found and fixed (DLL, pgvector distance) | Verify the Windows search fix; Windows service install (NSSM) |
| 6 embedding models screened (3 measured on SciFact); bge-base vs Qwen3-0.6B on 623 Kubernetes pages | bge-large, mxbai-large, arctic-l; multilingual corpora |
| 7 rerankers: CQADupStack-unix (all lowered nDCG), Kubernetes (+0–1 rank-1 hit, 1–4 s) → off by default | bge-reranker-v2-m3 (OOM at 15 GB; try int8), rerankers on the owner's documents |
| Gate families: similarity, IDF-weighted, reranker, reranker+similarity, all-query-terms rule; contextual passages | Gate tuned on pooled labels (fixtures + Kubernetes); contextual passages with that gate; SPLADE |
| Concurrency (single/multi process) | Large corpora (10⁵+ chunks), HNSW |
| HTML (main-content extraction, links → graph), DOCX, PPTX, PDF via PDFium — implemented | XLSX, legacy .doc/.ppt, OCR (plan: docs/plan-images-and-models.md), Docling for complex PDFs |
| Graph navigation v1 (links, impact, Connections UI) + link-aware search, evaluated on a synthetic wiki | Graph: real-corpus multi-hop evaluation, shared-identifier/entity edges, Graphify import, MCP tools |
| Local LLMs: answers and images trialled, not shipped; **document summaries, types, topics shipped (optional, §4f, §4k)**; LLM-written relations measured, not shipped (§4j) | **Local LLM answers with citations ("second brain")**; topic links; document types on a company corpus |

## 7. Next steps (highest value first)

Owner direction (2026-10-23): **accuracy before speed**; plan for an organisation-scale archive (~40k
multi-MB PDFs). Ranked search-improvement plan with sources: docs/research-search.md (reranker and
embedding bake-offs → contextual passages → learned sparse → entity edges + PageRank → scale test).

1. **Owner test on the intranet files (Windows):** `git pull`, `setup.bat --summaries`, upload the crawl
   folder; check summaries appear (Index status) and that `download-llm` worked on Windows (unverified here).
2. **Real-corpus evaluation:** done for public docs (Kubernetes, §4d). Still needed: the owner's own
   documents (runbooks, codebase wiki) with 50–100 labelled questions incl. unanswerable ones; then tune the
   gate on fixtures + Kubernetes + owner labels pooled (`eval/pooled_gates.py`, add the owner's rows file).
   Store per-model thresholds in `embedder.py`.
3. **Graph navigation, next increments** (docs/research-graph.md): run `eval/graph_eval.py`-style questions on
   a real Graphify/codebase-documenter wiki; shared-identifier (entity) edges; REST/MCP tools (`search`,
   `get_passage`, `get_links`, `impact`) for agent navigation.
4. **Second brain** (plan: `docs/plan-answers.md`, the Ask chat window with saved conversations, pictures and thinking): local LLM (Ollama/llama.cpp via an OpenAI-compatible endpoint, opt-in) answering only
   from retrieved passages with checked citations; abstains on `no_adequate_evidence`; surfaces conflicts.
   Expose `search`/`get_passage`/`get_links` as REST/MCP tools for agent navigation.
5. XLSX/Docling parsing, OCR for scanned PDFs and images (plan ready), OIDC, Docker build verification.

### Open owner requests (audit of the owner's recent messages, 2026-10-24)

**The full, current list of open work is [TODO.md](TODO.md)** (audit of every owner message, 2026-10-24). Plans not yet built, with sizes and order: [PLANS.md](PLANS.md); the latest is [plan-tables.md](plan-tables.md) (tables kept as tables).

**Latest (2026-10-24, last):** renamed to **Ferret** (crawler: **Ferret Crawler**, spider icon; logos in
`docs/brand/`). The command is `ferret` (`knowledge-search` still works), the crawler also installs as
`ferret-crawler`, MCP server name `ferret`, skill folder `skills/ferret`. Python packages, database, data
folders and the log file name are unchanged on purpose (no migration). The repository was
renamed to `ferret` on GitHub by the owner (GitHub redirects the old `knowledge-search` URL, so old clones keep working). README
rewritten (logo, badges, quick start, light/dark screenshot). App text decluttered: short labels, detail
behind ⓘ buttons (`info()` in app.js, `.info` in app.css). Graph: readable at 60 documents (only the lines
that explain the layout; point at a document for all its links; labels along the radius; fitted to the
panel with a Full size toggle), ~15× faster neighbours (document vectors cached in `graph.py`), and a Back
button in the passage panel. Plans added: `docs/plan-large-documents.md` (parallel embedding sessions:
first contended reading 1.7×, identical vectors; vector reuse for unchanged passages) and
`docs/plan-summaries-status.md` (count tiles + per-collection table; also fixes the 1,000-row cap that makes
Documents say "All 1000").

Done this session and verified: Crawler tab; MCP crawl into an existing or new collection (`send_crawl`);
following links in page text (the AWS guide case); clickable tiles, Pages table and Log; Synced folders tab
with progress and Stop; Documents page simplified; token access editable; groups created on their own tab and
picked from menus; the 7 search tricks measured (docs/evaluation.md §8: no gain).
Still open, in the order to do them:
1. **Vision comparison** GLM-4.6V-Flash vs Gemma 4 E2B (`eval/image_ocr.py`, 20 pictures): GLM measured on 19
   (docs/evaluation.md §9): words read almost perfectly, **every arrow right** in the org chart and both
   flowcharts, median 126 s per picture (52–961 s) on 4 cores. Still to run: Gemma 4 E2B on the same 20, GLM on
   the scanned page (two container restarts stopped them). Then: site2md saves images, and image text is
   indexed through the one ingestion path (§4o rule).
2. **CPU/GPU switch in Settings** for embedding, reranking, summaries and enrichment (owner has an Intel iGPU):
   plan in `docs/plan-gpu.md`, not built.
3. **Installer** for both apps (`docs/plan-installer.md`, `plan-release.md`), **Docker** brought up to date
   (`plan-docker.md`), **invite and password-reset links** (`plan-invites.md`): plans only.
4. **Owner checks on Windows:** crawler sign-in with installed Chrome/Edge (the Crawler tab opens that browser
   on the computer running Ferret); a real intranet copy through the Crawler tab.
5. **Owner's own documents** with 50–100 labelled questions (the tricks were measured on public docs only).
6. "Neighbour summaries instead of overlap": not measured; discussed and rejected by the owner (2026-10-24).
7. **Large documents** (`docs/plan-large-documents.md`; several embedding sessions measured: no gain on 4 cores,
   evaluation §10) and **Summaries counts** (`docs/plan-summaries-status.md`): plans.
8. Lint debt: ~20 older `ruff` findings (unused noqa, import order, two B023/B008) — harmless, clean up once.

## 8. Gotchas

- `KS_DATABASE_URL` empty = embedded PG in `data/pg`; set it for external servers (vector search then runs
  in pgvector unless `KS_VECTOR_SEARCH=memory`).
- Changing the model or chunking marks documents outdated until reindexed (Settings → Reindex).
- Thresholds are per model (`ModelSpec.gate_high/gate_min`); untuned models show a warning in Settings.
- Never `pkill -f "ferret serve"` from a shell command line that contains that text.
- The cloud environment rewrote the literal `NUL` in shell commands; Windows scripts were written with the
  editor tool and `.gitattributes` forces CRLF for `.bat/.ps1`.
- The embedded PG refuses to run as root on Linux; the package creates a `pgserver` user automatically. In the
  cloud container that user lost access to a data dir under the session scratchpad (`/tmp/claude-0/…`)
  mid-run (PANIC on `pg_control`); use a data dir under the repo's `data/` instead.
