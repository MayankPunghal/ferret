# Plan: a friendly screen for site2md (handoff, 2026-10-24)

Owner request: "a proper user-friendly UI/UX" for site2md, the tool that copies a website or intranet into
Markdown files for indexing.

Method: the *impeccable* design skill (github.com/pbakaus/impeccable, Apache-2.0), read as guidance only.
Its launcher downloads a binary, which this session does not run.
- Its `shape` step: write a design brief before code.
- Its `onboard` guidance: fastest path to a first success, empty states that teach, skippable steps.
- Its *Operate* mode: a working tool uses familiar standard controls; the look only adds type, palette,
  density and **one** signature move.

**Built 2026-10-24** (`site2md/src/site2md/ui.py`, `static/`; tests `tests/test_site2md_ui.py`). Open questions
were settled with the defaults: Update is manual (no schedule), and after sending, "Search it" is the
prominent action. Assumptions are marked; the open questions are at the end.

## Today

- Double-click `Crawl Website.bat` / `.command`. A black console window asks for the address, a name and a
  browser (1–4).
- To sign in, the user switches to the browser and back, then **presses Enter in the console**.
- Progress is console lines like `[12/340] 1.2 s https://… saved`.
- At the end the output folder opens.
- Works, but:
  - It is intimidating for non-technical people.
  - It shows no overall progress.
  - Failures are only in `manifest.json`.
  - Re-running needs the same name typed again.
  - Nothing connects it to Knowledge Search.

## Brief

1. **Job and audience.** An analyst, project manager or support lead, not a developer, wants their team's
   wiki, Google Site or Confluence space searchable. They have a browser sign-in to it and little patience
   for a terminal. (Assumption: most first runs are intranet sites that need a sign-in.)
2. **Outcome and proof.** Success is **"my site is copied, and I can see it is complete"**: how many pages
   were saved, which were not and why, then one click to search it. The proof is the coverage view (below)
   and a short, plain list of what was skipped.
3. **Direction (Operate mode).**
   - Standard controls only: text field, buttons, a progress bar, tables.
   - The same look as the Knowledge Search app (its colour and type tokens from `app.css`), so the two read
     as one product.
   - **Signature move: the site's own structure filling in.** The site's sections (from its URL folders) are
     shown as a tree, each with a bar of pages saved / found that fills as the crawl runs. It shows progress
     and coverage at once, and it is the thing a generic progress screen cannot show. Failed and skipped
     pages show as counts on their section, so gaps are visible where they are.
4. **Scope.**
   - **In:** a local page that site2md opens in the user's browser; the five screens below; the launchers
     open it instead of asking questions in the console.
   - **Out:** scheduling, multiple crawls at once, editing pages.
   - **Untouched:** the crawler's behaviour and output format, and the command line for technical users.
5. **States and ranges.**
   - From 5 pages to 10,000 pages (the cap), runs from seconds to hours.
   - The sign-in may expire mid-run. Sites can block robots, and links may be dead.
   - Attachments can be up to 500 MB.
   - First run: no browser downloaded yet, and no previous copies.
6. **Constraints.**
   - No new dependencies: Python's built-in web server, plain HTML/CSS/JS.
   - Runs on `127.0.0.1` only, with a random port and a one-time key in the address, so other web pages or
     users cannot drive it.
   - Works offline after setup; keyboard and screen-reader friendly; light and dark.

## Screens

1. **Your copies** (home)
   - Previous copies: name, site, pages, last updated, and a status ("complete", "stopped at 1,240 of about
     3,000", "3 pages need sign-in").
   - Each has **Update**, **Open folder** and **Delete**.
   - Empty state: one sentence on what site2md does, and a single "Copy a website" button. It teaches the
     tool instead of saying "nothing here".
2. **Copy a website**
   - **Address** field. The name fills in from the address and can be changed.
   - **Sign-in** is detected by a quick check: if the address redirects to a sign-in page (Google, Microsoft,
     Okta), the screen says "This site needs you to sign in. A browser window will open."
   - **Browser:** the installed Chrome or Edge is picked automatically. Firefox or Chromium are offered with
     their download size only when neither is installed.
   - **More options** (closed by default): page limit, extra folders or hosts to include, attachments on/off,
     keep HTML, ignore robots.txt (with a warning that this is only for your own sites).
   - One primary button: **Start copying**.
3. **Sign in** (only when needed)
   - "A browser window opened. Sign in until you see the site, then come back." The page **notices by
     itself** when the browser reaches the site and continues; a **Continue** button remains as fallback. No
     Enter in a console.
4. **Copying**
   - Counters: pages saved, attachments saved, still to visit, skipped, failed; time so far and pages per
     minute. No ETA: the number of pages is unknown until the end, so an ETA would mislead.
   - The section tree with its bars.
   - The last few pages saved, as links that open the Markdown.
   - **Pause** and **Stop** (Stop saves everything, like Ctrl+C today, and can be continued later).
   - **If the sign-in expires mid-run** (pages start coming back as sign-in pages): pause and ask to sign in
     again, instead of recording hundreds of failures.
5. **Done**
   - One plain sentence: "Copied 1,184 pages and 96 attachments from the Engineering wiki in 42 minutes."
   - **What was not copied**, grouped by reason in plain words: "needed sign-in", "no text on the page",
     "video or spreadsheet (not kept)", "page not found". Each group has an example and a **Try these
     again** button.
   - **Next step:**
     - If Knowledge Search runs on this computer: **Search it in Knowledge Search** creates or picks a
       collection, uploads the folder and opens the Documents tab.
     - Otherwise: **Open folder**, plus **Send to Knowledge Search** with the server address and an access
       token from its AI assistants page. The token is shortened on screen, as in the app.

## Build notes

- **Engine:** `Crawler.run` already has one place where each page finishes (the `self.log(...)` line).
  Replace the console line with a progress callback (url, outcome, counts). The command line keeps printing
  it; the page gets it through a small events stream.
- **Sign-in:** replace `input()` in `BrowserFetcher.login` with a wait the page can end. It finishes when the
  browser's address is back under the start URL, or when the page sends "continue".
- **Launchers:** `Crawl Website.bat` / `.command` install uv (as now), then run `site2md` with no arguments,
  which opens the page.
- **Later:** the same screens as "Import a website" inside Knowledge Search for laptop installs. Not first:
  when Knowledge Search runs on a server, a sign-in-protected intranet must be crawled from the user's own
  computer and browser, which the standalone page handles.

## Checks

- A Playwright walkthrough in site2md's tests: start a copy of a local test site, see the bars fill, stop,
  continue, finish, see the skipped list. Take screenshots at 1280 px and 390 px.
- A design review against impeccable's critique and audit lists: contrast, focus order, every control's
  states, and no layout jump while the counters update.

## Open questions for the owner

1. Is "Search it in Knowledge Search" the most common next step, or do people also use the Markdown
   elsewhere (LLM tools, offline copies)? That decides how prominent "Open folder" is.
2. Should copies refresh on a schedule (for example nightly), or is a manual **Update** enough for now?
   (Recommended: manual first.)
3. Which intranets matter most to test the sign-in flow: Google Sites, SharePoint or Confluence?
