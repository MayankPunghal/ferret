<p align="center"><img src="src/site2md/static/favicon.svg" width="88" alt=""></p>

<h1 align="center">Ferret Crawler</h1>

<p align="center">Part of <a href="../README.md">Ferret</a>. Command-line name: <code>site2md</code> (or <code>ferret-crawler</code>).</p>


Save a website or intranet as Markdown files, one per page, for a search index (such as Ferret),
an LLM tool or an offline copy. Standard-library Python; a real browser is optional for sites behind a login.

## The easy way (no setup)

1. Unzip this folder anywhere.
2. Double-click **`Crawl Website.bat`** (Windows) or **`Crawl Website.command`** (macOS; on Linux run
   `bash "Crawl Website.command"`). A page opens in your browser; keep the small black window open while you
   copy (closing it stops the crawler).
3. **Copy a website** → paste the address. The page checks it at once ("Found “Engineering Wiki”", or "this
   address asks you to sign in"). Give the copy a name and press **Start copying**. For a site behind a
   sign-in, a browser window opens: sign in there; the page notices and starts copying by itself.

While it copies you see each section of the site filling in, with Pause and Stop. When it is done, the page
lists what was not copied, grouped by reason ("page not found · 12"), with **Try these again**, and can
**send the copy to Ferret**.

The copies are in `output/<name>/`, one Markdown file per page, next to the original PDF, Word and
PowerPoint files the pages link to or embed. The first run installs what it needs (uv, Python, about 1–2
minutes; the internet is needed for that). **Your copies** lists them, with **Update** (copy the site again;
the old copy is kept until the new one is finished), **Continue** for a stopped copy, Open folder and Delete.

Sign-ins use Google Chrome or Microsoft Edge already on the computer (or a Chromium downloaded with
`uv run --extra browser playwright install chromium`). The sign-in is kept on your computer only, in
`~/.site2md/profile-<browser>`. Delete that folder to sign out. The crawler never sees your password.

**Original HTML (optional):** *More options → Also keep each page's original HTML* saves each page's HTML in
`output/<name>-html/`, next to the copy rather than in it, to check what a page held. It is not sent to
Ferret: the Markdown already keeps the structure that search uses (headings, lists, tables, code,
links), and indexing the same page twice would crowd results with duplicates.

The page listens on 127.0.0.1 only, at an address with a one-time key, so other computers and other web
pages cannot use it. `site2md ui --copies <folder>` opens it from a command line; `site2md <url> --out
<folder>` still copies without the page.

## Install as a command (technical users)

Needs Python 3.11+ (or [uv](https://docs.astral.sh/uv/), which installs Python for you).

```bash
uv tool install "site2md[browser] @ ./site2md"       # this folder; or: pipx install "./site2md[browser]"
```

## Use

```bash
site2md https://docs.example.com/ --out example-docs
```

Sites behind a company login (Google Sites, SharePoint, Confluence with SSO) — sign in once in a browser
window, then crawl with that session (kept in `~/.site2md/profile-<channel>`, on your machine only):

```bash
site2md "https://sites.google.com/acme.com/intranet/home" --out intranet --browser --login --channel chrome
site2md "https://sites.google.com/acme.com/intranet/home" --out intranet --browser --channel chrome   # later runs
```

`--channel chrome` or `--channel msedge` uses the browser already installed. `--channel firefox` or
`--channel chromium` (the default) uses Playwright's own copy: run `playwright install firefox` (or
`chromium`) once. Other Chromium-based browsers (Brave, Vivaldi): `--browser-path` to the executable.

## What it does

- Follows **every link on every page** — content, navigation bar, header, footer, sidebars, frames,
  `<link rel=next>` — plus `sitemap.xml`, until no new page is found (`--max-pages`, default 10,000).
- Stays under the start URL's folder (`https://host/docs/intro.html` → `https://host/docs/`); `--prefix` sets
  other prefixes (repeatable), `--allow-host` adds whole hosts.
- Never follows logout, delete, edit, print or feed links; respects robots.txt unless `--ignore-robots`.
- Writes `<page>.md` for every page, with front matter (`title`, `source_url`, `crawled_at`) and the page's
  text (menus and footers dropped; when the page's `<main>` element misses part of its text, as on Google
  Sites, the whole page is used). Scrolls through each page first, so text drawn when it comes into view is
  included.
- Saves linked PDF, Word and PowerPoint files **as they are**, under their own names ("Leave Policy (1).pdf") — nothing
  is converted, so nothing can be lost; a search index reads them itself (Ferret does, page by page).
- Downloads the Google Drive files, Docs (as Word) and Slides (as PDF) a page embeds or links to — the PDF
  viewers on Google Sites pages — and the files in embedded Drive folders, with the same sign-in, and links
  them from the page ("Attached documents"). Only PDF, Word and PowerPoint files are kept, up to 500 MB:
  videos and spreadsheets uploaded to Drive are recognised by name before downloading and skipped (listed in
  the manifest). For files over 100 MB it answers Google's "Download anyway" prompt itself.
- `--save-html` also keeps the HTML each page was converted from (in `<out>-html/`), to check what a page
  held.
- Saves a page reached under several URLs once; rewrites links between crawled pages to the local files;
  removes a title suffix shared by most pages (" | Company Wiki").
- `manifest.json`: every URL → file, failures (e.g. "sign-in page"), skips and the remaining queue;
  `--resume` continues an interrupted crawl. Ctrl+C stops a crawl cleanly (links fixed, manifest saved).

Pages with no text of their own (only a title or headings) are not written; the manifest lists them under
`skipped`. Without `--browser`, pages that build their content with JavaScript are among them. `--resume` also retries the URLs that failed. Embedded Google Sheets and Calendars, forms,
videos and files whose owner disabled downloads are not saved; the manifest lists them. The copy has no access control: keep it where only the intended readers can open it.
