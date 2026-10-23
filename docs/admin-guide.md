# Admin guide

Administrators see two extra tabs: **Settings** and **Users & access**.

## Settings

| Setting | What it does | Trade-off |
|---|---|---|
| **Embedding model** | Turns passages and queries into vectors for semantic search. Default `bge-base-en-v1.5` (~210 MB) — best measured accuracy/speed trade-off; `bge-small` (~70 MB) is ~3× faster to index but less accurate; larger models are available but untested here. Measurements: [Evaluation](evaluation.md). | Larger = better retrieval, slower indexing and queries on CPU. Changing it requires a reindex. |
| **Chunk size** | Target passage length in characters (200–4000). | Smaller = more precise passages; larger = more context per result. |
| **Overlap** | Repeats the last N characters of a passage at the start of the next, so a sentence split at a boundary is still found. Untick to disable. | More overlap = slightly larger index. |
| **Document summary in each passage** | Adds what the document is about (front-matter `description`, else its first paragraph, cut to N characters; 0 = off) to every passage before embedding, so a passage that never names its subject still matches. No AI model writes it. | On 623 Kubernetes pages, 300 put the right page first 98% of the time (100% with MiniLM) vs 95%; on the small test corpus slightly worse. Requires a reindex. |
| **Reranker** | A local cross-encoder re-reads the top candidates together with the query, re-orders them and (in the reranker gate modes) decides what counts as evidence. | Better precision/coverage; adds CPU time per query (see [Evaluation](evaluation.md)). |
| **Candidates to rerank** | How many top results the reranker reads. | More = better recall, slower. Models: MiniLM (fast), jina-turbo (fast, measured best on Kubernetes), bge-reranker-base (~3× slower), bge-reranker-v2-m3 (multilingual, slowest). |
| **Evidence thresholds** (advanced) | When a passage counts as evidence. | Higher = fewer false answers, more "no adequate evidence". Tuned values come from the evaluation; re-tune after changing the model. |

Changing the model, chunking or document summary marks already-indexed documents as **outdated**: the Settings page shows how
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
`site2md/README.md`). It saves a website as one Markdown file per page, plus the PDF and Word files the site
links to, ready to ingest. It needs no database or models and sends requests only to the site.
`knowledge-search crawl …` runs the same tool.

```bash
uv run knowledge-search crawl https://wiki.example.internal/ --out data/crawl/wiki
uv run knowledge-search ingest wiki data/crawl/wiki --wait
```

**Sites behind a sign-in** (Google Sites, SharePoint, Confluence with SSO): install the browser extra
(`uv pip install playwright`). Then sign in once in the browser window that opens. The session is kept in a
local browser profile (`~/.site2md/browser-profile`):

```bash
uv run knowledge-search crawl "https://sites.google.com/acme.com/intranet/home" --out data/crawl/intranet \
    --browser --login --channel msedge          # --channel chrome, or `playwright install chromium` once
```

Later runs drop `--login`. Browser mode also renders pages that build their content with JavaScript.

- **Links followed:** every link on every page (content, navigation bar, header, footer, sidebars, frames),
  plus the site's `sitemap.xml`. Crawling stops when no new page is found, or at `--max-pages` (default
  10,000).
- **Scope:** stays under the start URL's folder. `--prefix` sets other prefixes (repeatable) and
  `--allow-host` adds hosts.
- **Never followed:** logout, delete, edit, print and feed links. robots.txt is respected unless
  `--ignore-robots` is given.
- **Embedded documents:** Google Drive files, Docs and Slides that a page embeds or links to are
  downloaded with the same sign-in (Docs as Word, Slides as PDF). They are linked from the page under
  "Attached documents", so a page that is only a title and a PDF viewer is searchable through its PDF.
- **Duplicates:** a page reached under several URLs (`index.html`, tracking parameters) is saved once.
- **Links and titles:** links between crawled pages are rewritten to the local files, so the **graph works
  after ingest**. A site-wide title suffix (" | Company Wiki") is removed from titles.
- **Manifest:** `manifest.json` lists every URL → file, the failures (for example "sign-in page", which means
  run once with `--browser --login`) and the skips. `--resume` continues an interrupted crawl and retries the failures.
- **Limits:**
  - Without `--browser`, JavaScript-built pages come out empty (marked `empty` in the manifest).
  - Embedded Google Sheets, videos and Drive files whose owner disabled downloads are not saved.
  - Access rights are not copied: put pages with different audiences into different collections and grant
    them as usual.
- **Sharing the crawler:** `uv build --package site2md -o dist` builds a wheel. Others install it with
  `uv tool install "site2md[browser] @ ./site2md-0.1.0-py3-none-any.whl"`.

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
