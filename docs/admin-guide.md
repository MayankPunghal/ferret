# Admin guide

Administrators see two extra tabs: **Settings** and **Users & access**.

## Settings

| Setting | What it does | Trade-off |
|---|---|---|
| **Embedding model** | Turns passages and queries into vectors for semantic search. Default `bge-base-en-v1.5` (~210 MB) — best measured accuracy/speed trade-off; `bge-small` (~70 MB) is ~3× faster to index but less accurate; larger models are available but untested here. Measurements: [Evaluation](evaluation.md). | Larger = better retrieval, slower indexing and queries on CPU. Changing it requires a reindex. |
| **Chunk size** | Target passage length in characters (200–4000). | Smaller = more precise passages; larger = more context per result. |
| **Overlap** | Repeats the last N characters of a passage at the start of the next, so a sentence split at a boundary is still found. Untick to disable. | More overlap = slightly larger index. |
| **Add a line about the whole document to every passage** | Every passage is embedded in full under a header (document title — section heading). This adds one more header line that describes the whole document, the same on all its passages, so a passage that never names its subject is still found. The line is the document's front-matter `description`, else the local model's summary (when installed; the document is re-indexed once after it is written), else the document's first paragraph — cut to N characters; 0 = off. | 623 Kubernetes pages, 300 characters, right page first (56 questions): pages with author descriptions 94.6% → 98.2% (100% with MiniLM); pages without: first paragraph no gain (96.4% → 96.4%), Gemma 4 E2B summary 98.2%. Small test corpus slightly worse. Requires a reindex. |
| **Summarise each document in the background** | A local language model (downloaded by setup, or by the app in the background on first start while this is on; ~3.2 GB, one-time) writes a summary (**Summary length**: 2 sentences by default, up to a paragraph; a new length rewrites every summary, and longer ones take longer), a document type, up to six topics and, only for vague titles ("Overview", a file name), a clearer title. One document at a time, each as soon as it is indexed (the model runs at low priority, so indexing and search come first); nothing leaves the machine. Results and the passage view show them labelled as written by the model; they are never evidence. Progress on the Index status tab. | Gemma 4 E2B (default) on a 4-core CPU: ~17 s per document with 4 threads, 50 s with 1 (measured); the app uses half the cores (`KS_LLM_THREADS`), so plan ~30 s per document, i.e. ~1 h per 128 documents. Pick the model in the **Model** list: Gemma 4 E2B (recommended), Qwen3.5-2B (~1.3 GB, as accurate in the blind review) or Qwen3.5-0.8B (~0.5 GB, ~3x faster but more mistakes); a new one downloads in the background (refused with the sizes when the disk is too small) and rewrites every summary. `download-llm --model <name>` fetches one by hand. Runs at the lowest CPU priority (nice 19; idle class on Windows, not verified there): indexing speed measured unchanged. Untick to pause; summaries already written stay. **Run it on** CPU or GPU: the GPU uses llama.cpp's Vulkan build (Windows and Linux x64, ~50 MB, fetched on first use); measured on an Intel Iris Xe laptop 10–20% faster with the same quality, and the processor stays free. If the GPU build fails to download or start, the CPU build takes over and Settings says why. |
| **Reranker** | A local cross-encoder re-reads the top candidates together with the query, re-orders them and (in the reranker gate modes) decides what counts as evidence. | Better precision/coverage; adds CPU time per query (see [Evaluation](evaluation.md)). |
| **Candidates to rerank** | How many top results the reranker reads. | More = better recall, slower. Models: jina-turbo (default since 2026-10-24: fast, equal or better than MiniLM on every corpus where both ran), MiniLM (fast; the default before), bge-reranker-base (~3× slower, not better on Kubernetes), bge-reranker-v2-m3 (multilingual, slowest). On Kubernetes no reranker scored as well as jina-turbo at half its time; it is kept for ordering on harder corpora (SciFact: +1.4 nDCG@10 for MiniLM). |
| **Evidence thresholds** (advanced) | When a passage counts as evidence. | Higher = fewer false answers, more "no adequate evidence". Tuned values come from the evaluation; re-tune after changing the model. |

### Evidence thresholds: what is best for each model

The gate decides whether a passage counts as evidence. Its **cosine values belong to the embedding model** (it
defines what "similar" means) and the **reranker floor belongs to the reranker** (its scores are logits or
probabilities, not percentages of anything). Settings shows the shipped values for the model in use, next to a
button that fills them in.

| Embedding model | Similarity, min – strong | Keyword coverage | Every word matched | Tuned on |
|---|---|---|---|---|
| `BAAI/bge-small-en-v1.5` | 0.76 – 0.88 | 0.34 | 0.60 | fixtures |
| `BAAI/bge-base-en-v1.5` (default) | 0.72 – 0.78 | 0.20 | 0.60 | fixtures + Kubernetes |
| `Qwen/Qwen3-Embedding-0.6B-Q` | 0.64 – 0.78 | 0.20 | 0.45 | Kubernetes |
| every other model | 0.76 – 0.88 (the global default) | 0.34 | 0.60 | not tuned: measure on your own documents |

When the reranker is on, its floor is on its own scale:

| Reranker | Its score at a true match | Suggested floor |
|---|---|---|
| `Xenova/ms-marco-MiniLM-L-6-v2` | ~0.0 to 5 | 0.0 (the shipped default) |
| `jinaai/jina-reranker-v1-turbo-en` | −0.7 to −1.0 | −1.2 |
| `BAAI/bge-reranker-base` | −0.6 to −5 | −1.0, and it is noisy: near-miss pages can outscore the right one |

**Measured on a mixed business corpus** (slides, policies, wiki pages; 1,100 documents; 2026-10-26): with
**gate mode `similarity`, similarity 0.58 – 0.70, keyword coverage 0.20 and the reranker off**, three real
questions ("who is X", "who reports to X", "how much leave of each type") each came back with the right page
first and the junk question ("who won the football tournament") stayed refused, at 0.4 – 1 s per search. The
same questions with `bge-reranker-base` gating put a conference page above the right biography, and the
Kubernetes evaluation agrees (first result right: 96.4% without the reranker, 94.6% with it). Turning the
reranker off is therefore a good default for document sets like this; keep it on, with its own floor, when your
evaluation says it helps.


Changing the model, chunking or the line about the whole document marks already-indexed documents as **outdated**: the Settings page shows how
many, and search reports *indexing incomplete* until they are reindexed. Click **Reindex outdated
documents** (or **Reindex everything**); progress is visible on the Documents and Index status tabs.
Under each model list, **Models on this computer** shows which models are downloaded and has a **Download**
button for the others: fetch a model before you switch to it (after a disk-space check; it is not loaded or
selected). A model chosen without downloading it first downloads in the background when you save. A
spinner shows a model downloading or loading. The embedding model, reranker, search index and summary
model all load when the app starts or when you switch, not on the first search or document.

**No command line is needed for models.** If the search model is not on this computer when Ferret starts
(setup skipped or interrupted, or `data/models` deleted), the app still opens at once and downloads the
embedding model and reranker in the background. The Search tab says so until they are ready, and documents
added meanwhile are indexed afterwards. If the download fails (no internet, a proxy), the Search tab says
that too: press **Download** next to the embedding model in Settings once the connection is back. Documents
that failed for want of the model are queued again automatically.

All models run locally (ONNX Runtime on CPU); nothing is sent to external services.

## Users & access

**Users & access** has three tabs. Access goes through groups (one per team or role):
- **Groups** — create and delete groups; the table shows each group's members and the collections it opens.
  Deleting a group removes the access it gave (its members are signed out so it applies at once).
- **Collections** — create one per team/topic, and give a group (picked from a menu) read or read & write
  access. Collections without grants are visible to admins only.
- **Users** — create users and tick their groups (only existing groups can be chosen; the API refuses unknown
  names too). Changing a user's groups, disabling or resetting a password signs that user out everywhere.
  Admins can read and write every collection.
- The command line (`ferret add-to-group`, `grant`) still creates a group by naming it, for setting
  up a server from a script.

Permissions are enforced inside every database query: users never see restricted documents, snippets,
counts, or even whether a restricted document exists.

## AI assistants (MCP)

Every user can connect an AI assistant on the **AI assistants** page (setup: [User guide](user-guide.md)).
The endpoint is `/mcp` on the app's own address; there is nothing extra to run or open.

- **Read-only tokens** (the default) get 7 read-only tools: `search`, `read_passage`, `read_document`,
  `document_links`, `list_collections`, `index_status`, `list_documents`.
- **Tokens created with "May also make changes"** add tools within the user's rights. They are marked as not
  read-only, so assistants ask before running them.
  - Writers: `add_document`, `retry_document`, `delete_document`.
  - Administrators: `add_folder` / `list_folders` / `sync_folder` (Synced folders), `crawl_website` /
    `list_crawls` / `send_crawl`, `create_collection`, `get_settings` / `change_settings`, `download_model`, `reindex`.
- Tokens made before 2026-10-24 stay read-only.
- A read-only token is refused by the REST API as well (no uploads, no administration), so it cannot be used
  around the MCP tool list.
- Permissions, the evidence gate and the audit log (`token_created` with `allow_changes`, `token_revoked`,
  every search and change) apply as in the browser.
- Tokens are created only from a signed-in browser, last 30, 90 or 365 days and can be revoked by their
  owner. Disabling a user, resetting their password or changing their groups revokes all their tokens.
- Assistants on other computers need to reach the app: serve on the network (`serve --host 0.0.0.0`)
  behind HTTPS (a reverse proxy) so tokens never travel in clear text.
- Passages the assistant reads go to its provider. If that is against your data policy, tell users not to
  connect one; tokens are the only way in, so listing them (Logs: `token_created`) shows who did.

## Command line equivalents

```bash
uv run ferret create-collection policies --description "Company policies"
uv run ferret grant policies everyone              # read
uv run ferret grant policies policy-editors --write
uv run ferret create-user maria --password -        # prints a generated password
uv run ferret add-to-group maria everyone
uv run ferret ingest policies ./policies --wait     # bulk import a folder
```

`ingest` follows the same folder rules as uploads: subfolders included, names starting with `.` or `_` left
out, each file keyed by its path under the folder's name.

## Synced folders

**Synced folders** tab (administrators) → **Add folder**: give the full path of a folder **on the computer
running Ferret**, for example `C:\Users\me\Docs\repo-kit`, the collection and the file types to take.
It is indexed and remembered.
- **Sync now** takes in only the difference: new and changed files are indexed; documents whose file was
  deleted (or is now of an unticked type) are removed from the index. The files themselves are never changed.
- While it syncs the row shows how far it got (*Syncing… 40 of 103*) and **Stop**. A stopped sync keeps what it
  indexed and removes nothing; the next sync finishes it.
- Folders and files starting with `.` or `_` are left out, with everything inside them.
- Each row shows the last sync (files, new or changed, removed); **Details** shows the file types, what was
  left out and why, and files that could not be read.
- **Remove** forgets the folder and asks whether to remove its documents from the index as well.
- Website copies made in the **Crawler** tab are synced folders too.
- It is for administrators only because the app reads its own disk. On a team server the path is on the
  server, not on your computer.

## Copying a website from the app (Crawler tab, crawl_website)

Administrators have a **Crawler** tab: the crawler's own page (site2md, the same one its stand-alone launcher
opens), run by the app with your session, so no access token is needed.
- **Copy a website**, watch it run (pages copied, failed and skipped, each tile opens the list with the
  reasons; a Log tab), then **Add to a collection**: an existing one or a **New collection…**. The copy becomes
  a synced folder of that collection and is indexed in the background.
- **Links to other sites:** by default it follows links in the pages' text to other sections and sites (for
  example a product page's "User guide" card that leads to the documentation) and copies the section they lead
  to; links in menus, headers and footers are followed only within the site. "Stay on this site" and "Follow
  every link" (up to the page limit) are the other choices.
- **Update** copies the site again and replaces the copy when it finishes; the collection then takes in only
  what changed, and pages gone from the site are removed.
- Copies are kept in `<data>/crawls` unless you choose another folder on the tab (**Change**, kept in
  `<data>/crawler.json`).
- **Sites behind a sign-in:** choose the browser to sign in with on the tab; a browser window opens on the
  computer running Ferret and you sign in there once. The sign-in is kept in that browser's profile
  (`~/.site2md`) for later copies; the app never sees or stores the password.

Assistants (MCP, administrator tokens) can do the same:
- `crawl_website` copies a site and, when it is done, adds it to a collection: `collection` (existing) or
  `new_collection` (created only then, so a failed copy leaves no empty collection). `follow`, `max_pages`
  and `browser` as above.
- `list_crawls` shows the copies, progress and counts; `send_crawl` adds a finished copy to an existing or a
  new collection. Copies made by assistants show in the Crawler tab, and the other way round.

## Crawling a website or intranet

The crawler is a separate, shareable package, **site2md** (`site2md/`, standard-library Python; see
`site2md/README.md`). It saves a website as Markdown, one file per page, next to the original PDF, Word and
PowerPoint files the site links to or embeds, ready to ingest. It needs no database or models and sends requests only to the site.
`ferret crawl …` runs the same tool.

```bash
uv run ferret crawl https://wiki.example.internal/ --out data/crawl/wiki
uv run ferret ingest wiki data/crawl/wiki --wait
```

**Sites behind a sign-in** (Google Sites, SharePoint, Confluence with SSO): install the browser extra
(`uv pip install playwright`). Then sign in once in the browser window that opens. The session is kept in a
local browser profile, one per browser (`~/.site2md/profile-<channel>`):

```bash
uv run ferret crawl "https://sites.google.com/acme.com/intranet/home" --out data/crawl/intranet \
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
  `Crawl Website.bat` (Windows) or `Crawl Website.command` (macOS). It installs what it needs on first use
  and opens a page in their browser (127.0.0.1 only, one-time key in the address): copy a site, sign in in a
  browser window (no console steps), watch progress per section, pause, stop, continue, update, retry what
  failed, and send the copy to Ferret with an access token that may make changes. On the computer
  running Ferret, as administrator, the copy becomes a synced folder; otherwise each file is
  uploaded with its path, so sending an update again adds only what changed. Pages are saved in
  `site2md/output/<name>/`; the optional original HTML goes to `<name>-html/` beside it and is never sent.

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
