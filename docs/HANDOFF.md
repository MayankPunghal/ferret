# Handoff — Knowledge Search

State at the end of the cloud session (2026-10-23, version 1.0). Read with `CLAUDE.md` (owner instructions and end
goal), `docs/requirements-brief.md` (every owner ask and its status), `README.md`, and the docs listed there. Scope authority: `docs/brief/…master_prompt.md` §11.

## 1. Resume locally

```bash
git clone https://github.com/MayankPunghal/knowledge-search && cd knowledge-search
./setup.sh --demo --no-start          # Windows: setup.bat -Demo -NoStart
uv sync                               # adds dev tools
uv run pytest -q                      # expect 76 passed (embedded PostgreSQL; no Docker)
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
`migrations/0001-0006`. Tests in `tests/`, evaluation in `eval/`.

## 3. Verification status (what was actually run)

| Item | Status | Evidence |
|---|---|---|
| Fresh install with shipped defaults (bge-base + MiniLM reranker, weighted fusion) | **verified** on Linux: `setup.sh --demo` into an empty data dir, `start.sh`, full UI walkthrough, screenshots regenerated | `docs/screenshots/` |
| Test suite | **verified** | `uv run pytest -q` → 93 passed (summaries against a fake local model server, search, HTML/DOCX/PPTX formats, crawler, link graph + link-aware search, permissions, lifecycle, admin/settings/reindex/reranker, in-memory vs pgvector parity, DB-failure 503, embedded-DB helpers) |
| Linux setup | **verified** (`setup.ps1` logic run end-to-end under PowerShell 7 on Linux; CLI setup/demo/serve on Linux) | session logs |
| Windows setup | **verified by the owner** on Windows 11: install, model download, DB init and indexing; searches crashed the embedded DB (pgvector distance functions on Windows) → fixed with in-memory vector search (commit 7ec7a93, 2026-10-22 18:01); owner after that: "setup worked now, search returns results on Windows" (2026-10-23 00:15). **Regression
found by the owner 2026-10-23 22:36:** the upgrade to migration 0005 (document vectors, `avg(embedding)` in SQL)
crashed the embedded DB on start, and meaning-based related pages (`graph.similar`, `<=>` in SQL) would have
too → both now computed in Python with the embedded DB (parity + backfill tests); **rule: no pgvector function
or operator in SQL on the embedded-DB path**. Fix not yet re-run by the owner on Windows | owner messages |
| UI walkthrough | **verified** (Playwright/Chromium, `scripts/ui_check.py`): first-run admin, settings save + reindex, collections/grants/users, multi-file upload incl. rejection, clean state after sign-out/in, search outcomes, passage drawer, source open, mobile width | `docs/screenshots/` |
| Server profile (web + separate worker, external PG, concurrency) | verified natively earlier (0 errors at c=16, ~60–75 req/s on 4 vCPU, bge-small). Docker image build **not verified** (registry rate limits) | docs/evaluation.md |
| GPU | **not available, not implemented** | — |
| Backup/restore | **verified** (`scripts/backup_restore_check.sh`): data-folder copy and pg_dump/pg_restore + blobs both restore identical search results and all original files open. Found and fixed on the way: the embedded database kept running after the app stopped (the web server re-raises Ctrl+C after shutdown, so exit hooks never ran) — the app now releases it on shutdown | docs/deployment.md |

## 4. Measured results (tables and caveats in docs/evaluation.md)

- **Embedding models, BEIR SciFact subset (2,000 abstracts, 300 human-labelled queries), CPU:**
  bge-small nDCG@10 0.779 (8.3 docs/s, query 6.6 ms) · **bge-base 0.818** (3.3 docs/s, 23 ms) ·
  arctic-embed-m 0.814 (3.7 docs/s, 22 ms). nomic-v1.5 was OOM-killed (now bounded by batch/token caps;
  not re-measured). Qwen3-Embedding-0.6B **not measured** (too slow for the session on this CPU).
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
  - **PowerPoint (.pptx)** is read as one section per slide, with tables and speaker notes.
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

- **What:** `enrich.py`. For each indexed document, one at a time and only while no indexing job waits, the
  model gets the title, the headings and the first ~3,000 characters and returns JSON: a 2-sentence summary,
  up to 6 topics, and a title. The title is used only when the document's own is vague ("Overview", a file
  name, "Page 3"; `vague_title`) and the proposal is not (`clearer_title`). Stored in `documents.enrichment`
  per indexed version, model and prompt version; failures retried 3 times; claims expire after 15 min.
- **Where it runs:** where indexing runs: the app (laptop) or `knowledge-search worker` (server profile).
  The app starts llama.cpp's `llama-server` on a free 127.0.0.1 port (half the cores, `-np 1`) and stops it
  on exit, even mid-request. `KS_LLM_URL` points it at another local OpenAI-compatible server instead.
- **Install:** automatic: with summaries on and no model, the app downloads the default in the background on first start (once per start on failure, never with `KS_OFFLINE`); setup asks (Y downloads now, N writes `KS_ENRICH_ENABLED=false` to `.env`); `knowledge-search download-llm` by hand. llama.cpp release
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

`skills/knowledge-search/`:
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

## 5. Decisions made this session (details: docs/architecture.md)

Embedded PostgreSQL + vendored Windows DLL; in-memory vector search for laptops; runtime-editable
settings with index signatures (outdated documents are reported, never silently mixed); per-model tuned
thresholds; reranker optional and off unless it measurably helps; first-run admin in the browser;
auto-migrations on start; GraphRAG deferred in favour of a deterministic link-graph research track.

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
| Local LLMs: answers and images trialled, not shipped; **document summaries shipped (optional, §4f)** | **Local LLM answers with citations ("second brain")**; LLM-extracted relations; topic links |

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
4. **Second brain:** local LLM (Ollama/llama.cpp via an OpenAI-compatible endpoint, opt-in) answering only
   from retrieved passages with checked citations; abstains on `no_adequate_evidence`; surfaces conflicts.
   Expose `search`/`get_passage`/`get_links` as REST/MCP tools for agent navigation.
5. XLSX/Docling parsing, OCR for scanned PDFs and images (plan ready), OIDC, Docker build verification.

## 8. Gotchas

- `KS_DATABASE_URL` empty = embedded PG in `data/pg`; set it for external servers (vector search then runs
  in pgvector unless `KS_VECTOR_SEARCH=memory`).
- Changing the model or chunking marks documents outdated until reindexed (Settings → Reindex).
- Thresholds are per model (`ModelSpec.gate_high/gate_min`); untuned models show a warning in Settings.
- Never `pkill -f "knowledge-search serve"` from a shell command line that contains that text.
- The cloud environment rewrote the literal `NUL` in shell commands; Windows scripts were written with the
  editor tool and `.gitattributes` forces CRLF for `.bat/.ps1`.
- The embedded PG refuses to run as root on Linux; the package creates a `pgserver` user automatically. In the
  cloud container that user lost access to a data dir under the session scratchpad (`/tmp/claude-0/…`)
  mid-run (PANIC on `pg_control`); use a data dir under the repo's `data/` instead.
