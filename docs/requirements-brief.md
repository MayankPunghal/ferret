# Requirements brief (owner's asks, whole build session) and audit

Compiled on 2026-10-23 from every owner message in the build session, including messages sent while work
was running. Scope authority remains the brief's §11 gates; this page records what the owner asked on top,
and whether the product does it. Status: **done** (verified by a test, an evaluation or the owner),
**partial**, **not built**, **decided against** (with the reason and the measurement).

## 1. Product principles

| # | Ask | Status | Evidence / note |
|---|---|---|---|
| P1 | Offline knowledge search, no dependence on an external AI provider; external AI calls opt-in only | done | Embedding model and reranker run locally (ONNX); `tests/test_search.py::test_search_makes_no_outbound_network_calls` |
| P2 | For technical / IT people (PMs, BAs, devs, DBAs, DevOps): codebase docs, runbooks, business workflows; later a "second brain" that answers questions | partial | Search with cited passages is done; answer writing is trialled (`eval/llm_answers.py`), not shipped (§6) |
| P3 | Do not over-engineer: the simpler way is best when quality is the same | ongoing | One PostgreSQL (no separate vector or graph DB); every addition measured; rejected ideas listed in `docs/HANDOFF.md` |
| P4 | Accuracy before speed; 1–6 s per search with a visible loader is fine; indexing may be slow (background) | done | Spinner and "still working" note on search; indexing in a background worker with progress |
| P5 | Production-grade quality, look and functionality; keep auditing against the goal | ongoing | UI walkthrough (`scripts/ui_check.py`), 62 tests; this audit |
| P6 | Scale to an organisation archive (~40k multi-MB PDFs) on a server; choose infrastructure by measured scale tests, keep exact scoring | partial | `eval/scale_eval.py` and the server profile exist; 40k-PDF scale **not measured**; approximate index (HNSW) not built (exact search today) |
| P7 | Abstain instead of guessing; no false positives | done (measured) | Kubernetes: 0 false answers of 16 must-refuse questions; fixtures: 3 of 20 at the shipped gate (near-topic questions) — see `docs/evaluation.md` |

## 2. Setup, docs, delivery

| # | Ask | Status | Evidence / note |
|---|---|---|---|
| S1 | One-click setup on a new machine for a non-technical user; the batch file installs every prerequisite | done | `setup.bat` / `setup.sh` install uv, Python, dependencies, embedded PostgreSQL, models; owner ran it on Windows 11 ("setup worked now, search returns results on Windows", 2026-10-23 00:15) |
| S2 | No Docker requirement; clear errors and links when something is missing | done | Embedded PostgreSQL; Docker only for the optional server profile |
| S3 | Industry-standard README, split into sub-docs; quick start uses the batch file with its flags | done | `README.md` → `docs/getting-started.md`, `user-guide.md`, `admin-guide.md`, `configuration.md`, `deployment.md`, `evaluation.md`, `development.md` |
| S4 | A guide, sample/test documents and how to do a test run | done | `knowledge-search demo` (fictional corpus), `docs/getting-started.md` |
| S5 | Minimum prerequisites; fast on any machine | done | CPU only; ~0.5 GB of models; indexing ~5–7 passages/s on a 4-core VM |
| S6 | Commit in logical commits, push, keep the handoff current | done | `docs/HANDOFF.md`, git history |
| S7 | Acknowledge questions while background jobs run | done (process) | — |

## 3. User interface

| # | Ask | Status | Evidence / note |
|---|---|---|---|
| U1 | Clean UI to upload documents and change settings (chunk size, overlap, …) | done | Documents tab (files and whole folders), Settings tab (model, chunking, overlap, document summary, reranker, link following, thresholds) |
| U2 | Loading indicator for indexing and for the cold start | done | Per-document progress (parsing / embedding n of m), model-loading note |
| U3 | Log viewer with copyable, detailed errors | done | Logs tab, "Copy for support" |
| U4 | Each result shows the passage, the document and opens the real source | done | Result card → passage drawer → "Open source" (PDF at the page); crawled pages link to the original web address |
| U5 | Related / referenced / connected documents shown **with** each result, instead of a graph picture | done | "Links to / Referenced by / Mentions / Related in meaning" rows on every result (`docs/screenshots/15-related.png`); graph behind "Show as graph" |
| U6 | CPU/GPU toggle in settings | not built | No GPU to test on; CPU only (brief: GPU optional) |
| U7 | Is the document summary + reranker combination configurable in Settings | done | Both are Settings fields |

## 4. Retrieval quality

| # | Ask | Status | Evidence / note |
|---|---|---|---|
| R1 | Larger, more accurate embedding model even if slower | done (measured) | Bake-off: bge-base chosen (SciFact nDCG 0.818 vs 0.779 small); Qwen3-Embedding-0.6B answers more fixture questions (coverage 0.86 with 1/20 false answers, against 0.68 with 3/20 for bge-base) but indexing is much slower (estimated >1 h for 2,000 abstracts on this CPU; not measured); selectable in Settings |
| R2 | An offline reranker that does a decent job | done (measured) | MiniLM-L6 (90 MB); jina-turbo and bge-reranker-base measured (+1 of 56 on Kubernetes at best) |
| R3 | Chunk overlap, chunk linking, summary per chunk/document | done | Overlap on; neighbouring passages in the passage view; document summary in passage embeddings (setting) |
| R4 | Research and use more search techniques (owner's list of 7) | partial | 1 stronger reranker/more candidates: measured, worse. 2 larger embeddings: measured (R1). 3 contextual passages: built (setting), helps only with real page descriptions. 4 SPLADE: not built. 5 shared-identifier links + PageRank: not built (title mentions built). 6 automatic clusters: not built. 7 Docling/OCR: on hold (images plan) |
| R5 | Show a strong combination: summary + MiniLM gave 100% top results, 0 false answers | done (measured) | Kubernetes with page descriptions + MiniLM: top result 100%, 0 false answers; without descriptions 94.6–96.4% |
| R6 | Results should bring the related pages too | done (measured) | Results now cover 10 documents: all needed pages found for 58% of relationship questions instead of 42%; with the related rows ~88–92% |

## 5. Links and graph ("web of documents")

| # | Ask | Status | Evidence / note |
|---|---|---|---|
| G1 | Graph-style navigation of interlinked technical docs: forward/backward links, blast radius, from→to | done | Links table from Markdown/HTML links and `[[wiki]]` links; incoming/outgoing; impact (2 hops); graph view |
| G2 | Link documents by meaning, not just explicit links or keywords (e.g. a work-orders page mentioning the assets service links to the assets page) | done | Title mentions (no model) and "related in meaning" (mutual nearest documents by document vector); measured on Kubernetes: 61% of meaning pairs are pages the authors linked |
| G3 | Visualise the graph; clusters picked automatically | partial | Graph view done; automatic clusters not built |
| G4 | Dedicated vector DB / graph DB? Scale | decided against (for now) | PostgreSQL holds vectors, links and text in one transactional store; exact scoring; revisit with a measured scale test (P6) |
| G5 | GraphRAG / LightRAG for interconnected product/service docs | partial | LightRAG's idea taken without its per-passage LLM cost: links, mentions, meaning links, link-following search, related rows. LLM-extracted entities/relations per document: **not built yet** (§6). Multi-hop wiki: link following raises dependency evidence 0.24 → 0.60 |
| G6 | Learn from the codebase-documenter skill (fast agent answers from linked .md) | partial | Same principles adopted: per-page summaries (§6), stable links, related rows. Agent tools (MCP / llms.txt-style index) not built |

## 6. Local language models

| # | Ask | Status | Evidence / note |
|---|---|---|---|
| L1 | A local LLM summarises, tags and labels every document **in a background job** that never blocks search | **not built** | Measured: Gemma 4 E2B summaries ≈ authors' descriptions (+1 of 56); ~11 s per document on 4 cores. Owner wants it → next |
| L2 | The LLM gives a page a proper title when the page's own title is vague | not built | Part of L1 (the summary prompt already returns a title) |
| L3 | Small, fast models (phone-class: Gemma, Qwen, GLM, DeepSeek) for summaries, ranking and answers | done (trialled) | Gemma 4 E2B/E4B, Qwen3.5 0.8B/2B/4B tried; ranking and evidence checks by LLM were worse than the current gate |
| L4 | Answer the question from the found passages (second brain) | not built | Trialled; useful for writing, not for deciding; after L1 |
| L5 | Images: read text in images with a small vision model | decided: on hold | Owner: "hold off"; plan and measurements in `docs/plan-images-and-models.md` |

## 7. Formats and crawler (site2md)

| # | Ask | Status | Evidence / note |
|---|---|---|---|
| F1 | .md, .txt, .pdf, .html, .docx, .pptx | done | `tests/test_formats.py`; PDF via PDFium |
| F2 | Separate, shareable crawler package; double-click batch file for non-technical people | done | `site2md/` 1.0.0, `Crawl Website.bat` / `.command` |
| F3 | Follow every link (content, nav, header, footer, frames, sitemap); one Markdown file per page | done | `tests/test_crawl.py`; owner's intranet: 79 pages |
| F4 | Sign-in sites (Google Sites), selectable browser | done | `--browser --login --channel chrome|msedge|firefox|chromium` |
| F5 | Embedded/linked PDFs and Drive files saved as the original files (no lossy conversion), linked from the page | done | Owner's run 0.4.3: 49 files, 0 failures |
| F6 | Skip spreadsheets and videos, keep PowerPoint; answer Drive's "Download anyway"; files up to 500 MB | done | 1.0.0; tested |
| F7 | Original file names (not hyphenated) | done | 0.4.1 |
| F8 | Test it on the real intranet | done | Owner ran 0.4.x on the SourceFuse intranet (content never left the owner's machine) |

## Gaps to close, in order

1. L1/L2 background enrichment job (summary, tags, title) — optional, local, set up by the batch file.
2. ~~Gate 2: rehearse backup → restore~~ — done (`scripts/backup_restore_check.sh`; found and fixed the database outliving the app).
3. Final recommended settings and a local test procedure for the owner's intranet files; site2md 1.0.0 zip.
4. Later, each only with a measured test: LLM relations (G5), shared-identifier links (R4.5), clusters (G3),
   agent tools (G6), answer writing (L4), scale test at archive size (P6).
