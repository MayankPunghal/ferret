# Admin guide

Administrators see two extra tabs: **Settings** and **Users & access**.

## Settings

| Setting | What it does | Trade-off |
|---|---|---|
| **Embedding model** | Turns passages and queries into vectors for semantic search. Default `bge-base-en-v1.5` (~210 MB) — best measured accuracy/speed trade-off; `bge-small` (~70 MB) is ~3× faster to index but less accurate; larger models are available but untested here. Measurements: [Evaluation](evaluation.md). | Larger = better retrieval, slower indexing and queries on CPU. Changing it requires a reindex. |
| **Chunk size** | Target passage length in characters (200–4000). | Smaller = more precise passages; larger = more context per result. |
| **Overlap** | Repeats the last N characters of a passage at the start of the next, so a sentence split at a boundary is still found. Untick to disable. | More overlap = slightly larger index. |
| **Add a line about the whole document to every passage** | Every passage is embedded in full under a header (document title — section heading). This adds one more header line that describes the whole document, the same on all its passages, so a passage that never names its subject is still found. The line is the document's front-matter `description`, else the local model's summary (when installed; the document is re-indexed once after it is written), else the document's first paragraph — cut to N characters; 0 = off. | 623 Kubernetes pages, 300 characters, right page first (56 questions): pages with author descriptions 94.6% → 98.2% (100% with MiniLM); pages without: first paragraph no gain (96.4% → 96.4%), Gemma 4 E2B summary 98.2%. Small test corpus slightly worse. Requires a reindex. |
| **Summarise each document in the background** | A local language model (downloaded by setup, or by the app in the background on first start while this is on; ~3.2 GB, one-time) writes a two-sentence summary, up to six topics and, only for vague titles ("Overview", a file name), a clearer title. One document at a time, each as soon as it is indexed (the model runs at low priority, so indexing and search come first); nothing leaves the machine. Results and the passage view show them labelled as written by the model; they are never evidence. Progress on the Index status tab. | Gemma 4 E2B (default) on a 4-core CPU: ~17 s per document with 4 threads, 50 s with 1 (measured); the app uses half the cores (`KS_LLM_THREADS`), so plan ~30 s per document, i.e. ~1 h per 128 documents. Pick the model in the **Model** list: Gemma 4 E2B (recommended), Qwen3.5-2B (~1.3 GB, as accurate in the blind review) or Qwen3.5-0.8B (~0.5 GB, ~3x faster but more mistakes); a new one downloads in the background (refused with the sizes when the disk is too small) and rewrites every summary. `download-llm --model <name>` fetches one by hand. Runs at the lowest CPU priority (nice 19; idle class on Windows, not verified there): indexing speed measured unchanged. Untick to pause; summaries already written stay. |
| **Reranker** | A local cross-encoder re-reads the top candidates together with the query, re-orders them and (in the reranker gate modes) decides what counts as evidence. | Better precision/coverage; adds CPU time per query (see [Evaluation](evaluation.md)). |
| **Candidates to rerank** | How many top results the reranker reads. | More = better recall, slower. Models: jina-turbo (default since 2026-10-24: fast, equal or better than MiniLM on every corpus where both ran), MiniLM (fast; the default before), bge-reranker-base (~3× slower, not better on Kubernetes), bge-reranker-v2-m3 (multilingual, slowest). On Kubernetes no reranker scored as well as jina-turbo at half its time; it is kept for ordering on harder corpora (SciFact: +1.4 nDCG@10 for MiniLM). |
| **Evidence thresholds** (advanced) | When a passage counts as evidence. | Higher = fewer false answers, more "no adequate evidence". Tuned values come from the evaluation; re-tune after changing the model. |

Changing the model, chunking or the line about the whole document marks already-indexed documents as **outdated**: the Settings page shows how
many, and search reports *indexing incomplete* until they are reindexed. Click **Reindex outdated
documents** (or **Reindex everything**); progress is visible on the Documents and Index status tabs.
Under each model list, **Models on this computer** shows which models are downloaded and has a **Download**
button for the others: fetch a model before you switch to it (after a disk-space check; it is not loaded or
selected). A model chosen without downloading it first downloads in the background when you save. A
spinner shows a model downloading or loading. The embedding model, reranker, search index and summary
model all load when the app starts or when you switch, not on the first search or document.

All models run locally (ONNX Runtime on CPU); nothing is sent to external services.

## Users & access

- **Collections** — create one per team/topic. Grant a **group** read or read & write access. Collections
  without grants are visible to admins only.
- **Users** — create users and type their groups (comma-separated). Changing a user's groups, disabling or
  resetting a password signs that user out everywhere. Admins can read and write every collection.
- Groups are created implicitly the first time you use a name.

Permissions are enforced inside every database query: users never see restricted documents, snippets,
counts, or even whether a restricted document exists.

## AI assistants (MCP)

Every user can connect an AI assistant on the **AI assistants** page (setup: [User guide](user-guide.md)).
The endpoint is `/mcp` on the app's own address; there is nothing extra to run or open.

- An assistant gets its user's read access only (5 read-only tools); permissions, the evidence gate and the
  audit log (`token_created`, `token_revoked`, every search) apply as in the browser.
- Tokens are created only from a signed-in browser, last 30, 90 or 365 days and can be revoked by their
  owner. Disabling a user, resetting their password or changing their groups revokes all their tokens.
- Assistants on other computers need to reach the app: serve on the network (`serve --host 0.0.0.0`)
  behind HTTPS (a reverse proxy) so tokens never travel in clear text.
- Passages the assistant reads go to its provider. If that is against your data policy, tell users not to
  connect one; tokens are the only way in, so listing them (Logs: `token_created`) shows who did.

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

## Database

**Database** (admin only) shows the PostgreSQL tables behind the app. The top line names the server
version, whether the database is embedded or external, its size and its extensions.
- **Tables:** pick a table on the left to see:
  - **Rows:** 50 at a time. Sort by clicking a column; filter with "Where <column> contains <text>"; click a
    row to see all its values in full.
  - **Structure:** columns and types, indexes with their sizes, constraints, and the definition of a view.
  - The button left of the table name hides the table list, for wide tables.
- **SQL:** runs one statement of your own, for example a count per collection. At most 500 rows and 15
  seconds. Ctrl+Enter runs it. The example buttons above the box fill in a common query and run it.
  **Copy as CSV** copies the result for a spreadsheet.

It never changes data. Browsing runs read-only transactions. The SQL box connects as a separate database
role (`ks_reader`) that is not a superuser, can only read, and has no access to password or access-token
hashes. Writes, a second statement, reading server files and switching roles are all refused. Every SQL
query is recorded in the audit log (`db_query`).
- **Embedded database:** the app creates that role itself.
- **External database:** an administrator of that server creates a read-only role and sets
  `KS_READONLY_DATABASE_URL`. Without it the SQL box is off, and browsing still works.

Password and token hashes show as `•••`. Vectors show their first numbers and their length.

## Sidebar

The **Collapse** button at the bottom of the sidebar (or the `[` key) folds it to icons, which leaves more
room for wide tables. Hover an icon to see its name. The browser remembers the choice. Below 1000 px wide
the sidebar is already a bar at the top, and the button is hidden.

## Logs

**Logs** (admin only) has two views.

**Activity** works like a browser's Network tab. It lists the app's recent calls, newest first:
- searches and anything that changes something;
- API calls that fail or take over a second;
- calls from AI assistants (MCP), with the search each one made;
- the local model's calls;
- each document indexed.

Click a call to see:
- **Summary**: who made it, its status, how long it took, a timing bar for each step and, for the local
  model, the tokens read and written and the speed;
- **Request** and **Response**: the full payload, with a Copy button.

Filter by kind or text. **Live** (on by default) refreshes the list every 3 seconds. Routine page refreshes (fast,
successful reads) are not listed.

Unlike the logs, Activity shows search text, passages and prompts, so it is only for administrators. It
keeps the last 300 calls in memory only. They are gone on restart, never written to disk and never part of
**Copy for support**. Passwords, tokens and cookies are masked. Bodies over 30,000 characters are cut.
**Clear** empties it.

**Warnings and errors** shows recent warnings and errors with full technical details (tracebacks), newest
last; choose *everything* to include informational messages. **Copy for support** copies a diagnostics
header (app/Python/OS/library versions, model, reranker, database mode, document and job counts) plus the
log lines — paste it into a bug report. **Download .txt** saves the same text. Logs never contain document
text or search queries. The log is also written to `data/logs/knowledge-search.log` (rotated at 5 MB,
3 old files kept), which survives restarts.
