# Admin guide

Administrators see two extra tabs: **Settings** and **Users & access**.

## Settings

| Setting | What it does | Trade-off |
|---|---|---|
| **Embedding model** | Turns passages and queries into vectors for semantic search. Default `bge-base-en-v1.5` (~210 MB) — best measured accuracy/speed trade-off; `bge-small` (~70 MB) is ~3× faster to index but less accurate; larger models are available but untested here. Measurements: [Evaluation](evaluation.md). | Larger = better retrieval, slower indexing and queries on CPU. Changing it requires a reindex. |
| **Chunk size** | Target passage length in characters (200–4000). | Smaller = more precise passages; larger = more context per result. |
| **Overlap** | Repeats the last N characters of a passage at the start of the next, so a sentence split at a boundary is still found. Untick to disable. | More overlap = slightly larger index. |
| **Add a line about the whole document to every passage** | Every passage is embedded in full under a header (document title — section heading). This adds one more header line that describes the whole document, the same on all its passages, so a passage that never names its subject is still found. The line is the document's front-matter `description`, else the local model's summary (when installed; the document is re-indexed once after it is written), else the document's first paragraph — cut to N characters; 0 = off. | 623 Kubernetes pages, 300 characters, right page first (56 questions): pages with author descriptions 94.6% → 98.2% (100% with MiniLM); pages without: first paragraph no gain (96.4% → 96.4%), Gemma 4 E2B summary 98.2%. Small test corpus slightly worse. Requires a reindex. |
| **Summarise each document in the background** | A local language model (downloaded by setup, or by the app in the background on first start while this is on; ~3.2 GB, one-time) writes a two-sentence summary, up to six topics and, only for vague titles ("Overview", a file name), a clearer title. One document at a time, each as soon as it is indexed (the model runs at low priority, so indexing and search come first); nothing leaves the machine. Results and the passage view show them labelled as written by the model; they are never evidence. Progress on the Index status tab. | Gemma 4 E2B (default) on a 4-core CPU: ~17 s per document with 4 threads, 50 s with 1 (measured); the app uses half the cores (`KS_LLM_THREADS`), so plan ~30 s per document, i.e. ~1 h per 128 documents. Qwen3.5-0.8B (`download-llm --model qwen3.5-0.8b`) is ~3x faster but makes more mistakes. Untick to pause; summaries already written stay. |
| **Reranker** | A local cross-encoder re-reads the top candidates together with the query, re-orders them and (in the reranker gate modes) decides what counts as evidence. | Better precision/coverage; adds CPU time per query (see [Evaluation](evaluation.md)). |
| **Candidates to rerank** | How many top results the reranker reads. | More = better recall, slower. Models: MiniLM (fast), jina-turbo (fast, measured best on Kubernetes), bge-reranker-base (~3× slower), bge-reranker-v2-m3 (multilingual, slowest). |
| **Evidence thresholds** (advanced) | When a passage counts as evidence. | Higher = fewer false answers, more "no adequate evidence". Tuned values come from the evaluation; re-tune after changing the model. |

Changing the model, chunking or the line about the whole document marks already-indexed documents as **outdated**: the Settings page shows how
many, and search reports *indexing incomplete* until they are reindexed. Click **Reindex outdated
documents** (or **Reindex everything**); progress is visible on the Documents and Index status tabs.
New models are downloaded once in the background (shown under the model selector).

All models run locally (ONNX Runtime on CPU); nothing is sent to external services.

## Users & access

- **Collections** — create one per team/topic. Grant a **group** read or read & write access. Collections
  without grants are visible to admins only.
- **Users** — create users and type their groups (comma-separated). Changing a user's groups, disabling or
  resetting a password signs that user out everywhere. Admins can read and write every collection.
- Groups are created implicitly the first time you use a name.

Permissions are enforced inside every database query: users never see restricted documents, snippets,
counts, or even whether a restricted document exists.

## Command line equivalents

```bash
uv run knowledge-search create-collection policies --description "Company policies"
uv run knowledge-search grant policies everyone              # read
uv run knowledge-search grant policies policy-editors --write
uv run knowledge-search create-user maria --password -        # prints a generated password
uv run knowledge-search add-to-group maria everyone
uv run knowledge-search ingest policies ./policies --wait     # bulk import a folder
```

## Crawling a website or intranet

The crawler is a separate, shareable package, **site2md** (`site2md/`, standard-library Python; see
`site2md/README.md`). It saves a website as Markdown, one file per page, next to the original PDF, Word and
PowerPoint files the site links to or embeds, ready to ingest. It needs no database or models and sends requests only to the site.
`knowledge-search crawl …` runs the same tool.

```bash
uv run knowledge-search crawl https://wiki.example.internal/ --out data/crawl/wiki
uv run knowledge-search ingest wiki data/crawl/wiki --wait
```

**Sites behind a sign-in** (Google Sites, SharePoint, Confluence with SSO): install the browser extra
(`uv pip install playwright`). Then sign in once in the browser window that opens. The session is kept in a
local browser profile, one per browser (`~/.site2md/profile-<channel>`):

```bash
uv run knowledge-search crawl "https://sites.google.com/acme.com/intranet/home" --out data/crawl/intranet \
    --browser --login --channel chrome   # or msedge; firefox/chromium: `playwright install <name>` once
```

Later runs drop `--login`. Browser mode also renders pages that build their content with JavaScript.

- **Links followed:** every link on every page (content, navigation bar, header, footer, sidebars, frames),
  plus the site's `sitemap.xml`. Crawling stops when no new page is found, or at `--max-pages` (default
  10,000).
- **Scope:** stays under the start URL's folder. `--prefix` sets other prefixes (repeatable) and
  `--allow-host` adds hosts.
- **Never followed:** logout, delete, edit, print and feed links. robots.txt is respected unless
  `--ignore-robots` is given.
- **Embedded documents:** Google Drive files, Docs and Slides that a page embeds or links to, and the
  files in an embedded Drive folder (and its subfolders), are downloaded with the same sign-in (Docs as Word,
  Slides as PDF). They are linked from the page under "Attached documents". They are saved as they are and
  read by ingest (PDF page by page, PowerPoint slide by slide with the speaker notes), so a search finds the
  page and its documents, connected in the graph. Only PDF, Word and PowerPoint files are kept, up to 500 MB:
  videos and spreadsheets are recognised by name and skipped without downloading them. For files over 100 MB
  the crawler answers Google's "Download anyway" prompt itself.
- **Duplicates:** a page reached under several URLs (`index.html`, tracking parameters) is saved once.
- **Links and titles:** links between crawled pages are rewritten to the local files, so the **graph works
  after ingest**. A site-wide title suffix (" | Company Wiki") is removed from titles.
- **Manifest:** `manifest.json` lists every URL → file, the failures (for example "sign-in page", which means
  run once with `--browser --login`) and the skips. `--resume` continues an interrupted crawl and retries the failures.
- **Limits:**
  - Without `--browser`, JavaScript-built pages come out empty (marked `empty` in the manifest).
  - Embedded Google Sheets and Calendars, forms, videos, pictures and Drive files whose owner disabled
    downloads are not saved. Embeds from other sites are listed in the manifest's skips ("embedded
    calendar.google.com/calendar not saved (on …)"), so a page with little text can be explained.
  - Access rights are not copied: put pages with different audiences into different collections and grant
    them as usual.
- **Sharing the crawler:** zip the `site2md/` folder. People who are not technical double-click
  `Crawl Website.bat` (Windows) or `Crawl Website.command` (macOS). It installs what it needs on first use,
  asks for the address, opens a browser for the sign-in, and saves the pages in `site2md/output/<name>/`.

## Index status and jobs

**Index status** shows per-collection counts (indexed, pending, failed) and the active configuration; admins
also see the job queue. Jobs that crash are retried automatically (3 attempts with backoff); parse errors
are permanent until the file is fixed and uploaded again.

## Logs

**Logs** (admin only) shows recent warnings and errors with full technical details (tracebacks), newest
last; choose *everything* to include informational messages. **Copy for support** copies a diagnostics
header (app/Python/OS/library versions, model, reranker, database mode, document and job counts) plus the
log lines — paste it into a bug report. **Download .txt** saves the same text. Logs never contain document
text or search queries. The log is also written to `data/logs/knowledge-search.log` (rotated at 5 MB,
3 old files kept), which survives restarts.
