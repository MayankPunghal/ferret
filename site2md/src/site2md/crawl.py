"""Website → Markdown crawler: point it at a URL, get one Markdown file per page, plus the original PDF / Word
files the site links to or embeds. Standard library only; ``--browser`` (optional ``playwright`` package)
drives a real browser for sites behind a login or that build pages with JavaScript.

    site2md https://wiki.example.internal/ --out wiki-docs
    site2md https://sites.google.com/acme.com/intranet/home --out intranet --browser --login   # sign in once

Traversal: breadth-first over every link on every page — content, navigation bar, header, footer, sidebars,
image maps, frames, <link rel=next> — plus the site's sitemap.xml, until no new page is found (or
``--max-pages``). Scope: URLs under the start URL's folder (https://host/docs/intro.html → https://host/docs/;
``--prefix`` sets other prefixes, ``--allow-host`` adds whole hosts). Robots.txt is respected unless ``--ignore-robots`` (your own intranet). Links that change state or
end a session (logout, delete, edit, …) are never followed. Pages reached under several URLs are saved once
(same content).

Output: ``<name>.md`` per page with front matter (title, source_url, crawled_at); the page body is converted
like an uploaded HTML file (main content only, menus and footers dropped). After the crawl every link between
crawled pages is rewritten to the local file name, so they become graph edges when ingested; other links stay
absolute, and a title suffix shared by most pages (" | Company Wiki") is removed. ``manifest.json`` lists url → file, failures and the remaining queue; ``--resume`` continues an
interrupted crawl. A page with no text of its own (only a title or headings, e.g. one that only embeds files
that could not be saved, or one that needs JavaScript without ``--browser``) is not written; the manifest lists
it under ``skipped``.
"""

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urldefrag,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

from .html import _own_text, html_to_markdown

USER_AGENT = "site2md/1 (documentation crawler)"
# never follow: ends the session or changes data (crawlers that click "logout" lose the login; "delete"
# links on wikis are GET in old systems), plus endless calendars / print / feed variants
NO_FOLLOW = re.compile(
    r"log-?out|sign-?out|log-?off|/delete|[?&](action|do)=(edit|delete|remove|logout|diff|history|revision)"
    r"|/edit\b|/print\b|[?&]print=|/feed\b|/rss\b|\.rss$|/calendar/|[?&]replytocom=|[?&]share=", re.IGNORECASE)
FILE_TYPES = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".md": "md", ".markdown": "md", ".txt": "txt"}
KEEP = (".pdf", ".docx", ".pptx")  # embedded Drive files that are kept
SKIP_TYPES = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|mjs|json|xml|zip|gz|tgz|tar|exe|msi|dmg|iso|mp4|"
                        r"mp3|wav|avi|mov|woff2?|ttf|eot|pptx?|xlsx?|csv)$", re.IGNORECASE)
TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|_ga|ref|refid|sessionid|jsessionid|phpsessid|sid|authuser)$",
                      re.IGNORECASE)  # authuser: Google's account selector, same page
MAX_BYTES = 500_000_000  # the same limit as knowledge-search uploads
DOCUMENT = re.compile(r"pdf|wordprocessingml|octet-stream|html", re.IGNORECASE)  # worth downloading (html: an
# answer such as "download disabled", recorded as such)
# Google Drive / Docs / Slides files embedded in a page (Google Sites viewers, iframes, links): downloaded
# with the same sign-in, saved next to the page and linked from it
EMBED = re.compile(r"https://(?:drive|docs)\.google\.com/(?:a/[\w.-]+/)?(file|document|presentation)/d/([\w-]{20,})")
EXPORT = {"file": "https://drive.google.com/uc?export=download&id={}",
          "document": "https://docs.google.com/document/d/{}/export?format=docx",
          "presentation": "https://docs.google.com/presentation/d/{}/export/pdf"}
# an embedded Drive folder: the files in it (and in its subfolders) are downloaded like embedded files
FOLDER = re.compile(r"https://drive\.google\.com/(?:a/[\w.-]+/)?(?:embeddedfolderview\?(?:[^\"'\s<>]*?&(?:amp;)?)?id="
                    r"|drive/(?:u/\d+/)?folders/)([\w-]{20,})")
FOLDER_VIEW = "https://drive.google.com/embeddedfolderview?id={}"
IFRAME = re.compile(r"<iframe\b[^>]*?\ssrc=[\"']([^\"']+)", re.IGNORECASE)
LOGIN = re.compile(r"^(accounts\.google\.com|login\.microsoftonline\.com)|/(login|signin|sign-in|sso)\b", re.IGNORECASE)


SETTLE = """async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 40 && scrollY + innerHeight < document.documentElement.scrollHeight; i++) {
    scrollBy(0, innerHeight); await sleep(150);
  }
  let last = -1;
  for (let i = 0; i < 20; i++) {
    const n = document.body ? document.body.innerText.length : 0;
    if (n === last) break;
    last = n; await sleep(250);
  }
}"""


class _Inline:
    """Executor that runs each task immediately on the calling thread (Playwright objects are bound to it)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args) -> Future:
        f = Future()
        try:
            f.set_result(fn(*args))
        except Exception as e:  # noqa: BLE001 - surfaced by result(), like a thread pool
            f.set_exception(e)
        return f


class BrowserFetcher:
    """Fetch through a real browser (Playwright: installed Edge/Chrome, or its own Chromium) with a persistent
    profile, so a sign-in done once (``login``) is reused by every crawl, and pages that build their content
    with JavaScript are captured after rendering. Not thread-safe: the crawler uses one worker."""

    def __init__(self, profile: Path, channel: str = "chromium", headless: bool = True, executable: str = ""):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise SystemExit("--browser needs Playwright: pip install playwright (then, unless you use "
                             "--channel msedge/chrome, run: playwright install chromium)") from e
        self._pw = sync_playwright().start()
        engine = self._pw.firefox if channel == "firefox" else self._pw.chromium
        opts = {"headless": headless, **({"channel": channel} if channel not in ("", "chromium", "firefox") else {}),
                **({"executable_path": executable} if executable else {})}
        self.ctx = engine.launch_persistent_context(str(profile), **opts)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        if headless:  # crawling: images, video and fonts are not needed for the text, and slow every page down
            self.ctx.route("**/*", lambda route: route.abort() if route.request.resource_type in
                           ("image", "media", "font") else route.continue_())

    def login(self, url: str, done=None) -> None:
        """Open the site to sign in. Without `done`, the person presses Enter in the console; with it, wait
        until done(current address) is true (the browser is back on the site, or the page said continue)."""
        self.page.goto(url)
        if done is None:
            input("Sign in in the browser window until you see the site, then press Enter here... ")
            return
        while True:
            try:
                where = self.page.url
                if self.ctx.pages and self.page.is_closed():
                    self.page = self.ctx.pages[0]
                self.page.wait_for_timeout(1000)  # keeps the browser responsive while we wait
            except Exception:  # noqa: BLE001 - the window was closed: take that as finished
                return
            if done(where):
                return

    def get(self, url: str, render: bool) -> tuple[bytes, str, str, str]:
        if render:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            ctype = (resp.headers.get("content-type", "") if resp else "") or "text/html"
            if "html" in ctype:
                try:  # let scripts fill the page; embedded viewers (Drive, video) keep the network busy forever
                    self.page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:  # noqa: BLE001, S110 - still busy: take the page as it is
                    pass
                try:  # scroll through the page (content drawn when it comes into view), then wait for the text
                    self.page.evaluate(SETTLE)  # to stop changing
                except Exception:  # noqa: BLE001, S110 - navigated away meanwhile: take the page as it is
                    pass
                return self.page.content().encode("utf-8"), "text/html; charset=utf-8", self.page.url, ""
        r = self.ctx.request.get(url, timeout=120_000)  # files and non-HTML: same cookies, no rendering
        if r.status >= 400:
            raise urllib.error.HTTPError(url, r.status, r.status_text, None, None)
        if len(r.body()) > MAX_BYTES:
            raise ValueError(f"larger than {MAX_BYTES // 1_000_000} MB")
        return r.body(), r.headers.get("content-type", ""), r.url, _filename(r.headers.get("content-disposition", ""))

    def head(self, url: str) -> tuple[str, int, str]:
        """(content type, size, file name) without downloading the body; empty when the server does not say."""
        r = self.ctx.request.head(url, timeout=60_000)
        if not r.ok:
            return "", 0, ""
        h = r.headers
        return h.get("content-type", ""), int(h.get("content-length") or 0), _filename(h.get("content-disposition", ""))

    def close(self) -> None:
        self.ctx.close()
        self._pw.stop()


def _filename(content_disposition: str) -> str:
    if not content_disposition:
        return ""
    m = Message()
    m["content-disposition"] = content_disposition
    return m.get_filename() or ""


def embedded_documents(html: str) -> list[str]:
    """Download URLs of the Google Drive files, Docs and Slides a page embeds or links to."""
    return list(dict.fromkeys(EXPORT[kind].format(doc_id) for kind, doc_id in EMBED.findall(html.replace("\\/", "/"))))


class _Form(HTMLParser):
    """Drive's virus-scan warning: the "Download anyway" form and the file name shown above it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.action, self.fields, self.name, self._in_name = "", {}, "", 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "download-form":
            self.action = a.get("action") or ""
        elif tag == "input" and a.get("type") == "hidden" and a.get("name") and self.action:
            self.fields[a["name"]] = a.get("value") or ""
        elif tag == "span" and "uc-name-size" in (a.get("class") or ""):
            self._in_name = 1
        elif tag == "a" and self._in_name == 1:
            self._in_name = 2

    def handle_data(self, data):
        if self._in_name == 2:
            self.name += data

    def handle_endtag(self, tag):
        if tag == "a" and self._in_name == 2:
            self._in_name = 3


def virus_scan_confirm(html: str) -> tuple[str, str] | None:
    """(download URL behind "Download anyway", file name) on Drive's "can't scan for viruses" page."""
    if "download-form" not in html:
        return None
    f = _Form()
    f.feed(html)
    if not f.action.startswith(("https://drive.google.com/", "https://drive.usercontent.google.com/")):
        return None
    return f.action + "?" + urlencode(f.fields), f.name.strip()


def embedded_folders(html: str) -> list[str]:
    return list(dict.fromkeys(FOLDER.findall(html.replace("\\/", "/"))))


def _front(title: str, source_url: str) -> str:
    return (f"---\ntitle: {json.dumps(title)}\nsource_url: {json.dumps(source_url)}\n"
            f"crawled_at: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n---\n\n")


def normalize(url: str) -> str:
    """Canonical form used to recognise the same page: no fragment, lower-case scheme/host, default port and
    tracking/session parameters removed, remaining query parameters sorted."""
    url, _ = urldefrag(url.strip())
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port and not ((p.scheme == "http" and p.port == 80) or (p.scheme == "https" and p.port == 443)) else ""
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)))
    path = re.sub(r";jsessionid=[^/?]*", "", p.path, flags=re.IGNORECASE) or "/"
    return urlunsplit((p.scheme.lower(), host + port, path, query, ""))


class NavLink(str):
    """A link in a page's menus, header, footer or sidebar (not its content): followed inside the copied site,
    but never the reason to go to another site."""


# social and video sites: a link to them is never the start of a section worth copying
NOT_DOCS = re.compile(r"(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com|twitter\.com|x\.com|facebook\.com|"
                      r"linkedin\.com|instagram\.com|tiktok\.com|reddit\.com|vimeo\.com|twitch\.tv)$")
CHROME = {"nav", "header", "footer", "aside"}
CHROME_ROLES = {"navigation", "banner", "contentinfo", "complementary", "menu", "menubar"}
TRACKED = CHROME | {"div", "ul", "ol", "section"}


class _Links(HTMLParser):
    """Every followable URL on a page, wherever it appears (content, nav, header, footer, frames); links in
    menus, header, footer and sidebars are marked as NavLink."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base, self.links = "", []
        self._chrome: list[tuple[str, int]] = []  # open menu/header/footer elements: (tag, nesting level)
        self._depth = dict.fromkeys(TRACKED, 0)

    def handle_endtag(self, tag):
        if tag in TRACKED:
            if self._chrome and self._chrome[-1] == (tag, self._depth[tag]):
                self._chrome.pop()
            self._depth[tag] = max(0, self._depth[tag] - 1)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in TRACKED:
            self._depth[tag] += 1
            if tag in CHROME or (a.get("role") or "").lower() in CHROME_ROLES:
                self._chrome.append((tag, self._depth[tag]))
        n = len(self.links)
        self._link(tag, a)
        # menus, header, footer, and head links / language versions (rel=alternate, hreflang): same site only
        if len(self.links) > n and (self._chrome or tag == "link" or a.get("hreflang")):
            self.links[-1] = NavLink(self.links[-1])

    def _link(self, tag, a):
        if tag == "base" and a.get("href"):
            self.base = a["href"]
        elif tag in ("a", "area") and a.get("href"):
            self.links.append(a["href"])
        elif tag in ("frame", "iframe") and a.get("src"):
            self.links.append(a["src"])
        elif tag == "link" and a.get("href") and (a.get("rel") or "").lower() in ("next", "prev", "alternate", "canonical"):
            if not (a.get("type") or "").startswith(("application/rss", "application/atom")):
                self.links.append(a["href"])
        elif tag == "meta" and (a.get("http-equiv") or "").lower() == "refresh":
            m = re.search(r"url\s*=\s*['\"]?([^'\";]+)", a.get("content") or "", re.IGNORECASE)
            if m:
                self.links.append(m.group(1))


def page_links(html: str, url: str) -> list[str]:
    p = _Links()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001, S110 - broken markup: keep what was found
        pass
    base = urljoin(url, p.base) if p.base else url
    out = []
    for href in p.links:
        h = href.strip()
        if h and not h.lower().startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
            u = normalize(urljoin(base, h))
            out.append(NavLink(u) if isinstance(href, NavLink) else u)
    return out


def _decode(data: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', data[:4096], re.IGNORECASE)
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def start_folder(url: str) -> str:
    """The folder a crawl stays in by default: https://host/docs/intro.html -> https://host/docs/."""
    url = normalize(url)
    return (url if url.endswith("/") else url.rsplit("/", 1)[0] + "/").split("?")[0]


def slug_for(url: str, used: set[str], root: str = "") -> str:
    """Readable unique file stem from the URL path below ``root`` (the crawl folder):
    /docs/Ops/Runbook.html -> docs-ops-runbook, or ops-runbook below /docs/."""
    p = urlsplit(url)
    path = p.path[len(root):] if root and p.path.startswith(root) else p.path
    path = re.sub(r"\.(html?|php|aspx?|jsp)$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"/index$", "", path)
    stem = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")[:100] or "index"
    if p.query:
        stem += "-" + hashlib.sha1(p.query.encode()).hexdigest()[:6]
    name, n = stem, 2
    while name in used:
        name, n = f"{stem}-{n}", n + 1
    used.add(name)
    return name


WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def file_name_for(filename: str, used: set[str]) -> str:
    """A document's own name, kept readable ("Leave Policy (1)"): only characters Windows, macOS or a Markdown
    link cannot carry are replaced; unique ignoring case (Windows and macOS file systems)."""
    stem = re.sub(r'[<>:"/\\|?*#%\x00-\x1f\[\]]', "-", filename).strip(" .")[:120].strip(" .")
    if stem.lower() in WINDOWS_RESERVED:
        stem += "_"
    name, n = stem, 2
    while name.lower() in used:
        name, n = f"{stem} ({n})", n + 1
    used.add(name.lower())
    return name


class _Queue:
    """Pages to visit: the copied site's own pages first, then sections its links lead to, so a page limit
    never starves the site itself."""

    def __init__(self, first):
        self.first, self.now, self.later = first, deque(), deque()

    def append(self, u: str) -> None:
        (self.now if self.first(u) else self.later).append(u)

    def extendleft(self, urls) -> None:
        self.now.extendleft(urls)

    def popleft(self) -> str:
        return self.now.popleft() if self.now else self.later.popleft()

    def __len__(self) -> int:
        return len(self.now) + len(self.later)

    def __iter__(self):
        return iter([*self.now, *self.later])


class Crawler:
    def __init__(self, start: str, out: Path, *, prefixes: list[str] | None = None, allow_hosts: list[str] | None = None,
                 max_pages: int = 10_000, workers: int = 4, delay: float = 0.2, headers: dict | None = None,
                 robots: bool = True, files: bool = True, timeout: float = 30, log=print, browser=None,
                 save_html: Path | None = None, on_page=None, follow: str = "linked"):
        self.start, self.out = normalize(start), Path(out)
        host = urlsplit(self.start).netloc
        self.prefixes = [normalize(p) for p in prefixes] if prefixes else [start_folder(self.start)]
        self.root = urlsplit(self.prefixes[0]).path.rstrip("/")  # file names are relative to it
        self.allow_hosts = set(allow_hosts or [])
        self.hosts = {host, *self.allow_hosts, *(urlsplit(x).netloc for x in self.prefixes)}
        # how far to follow links that leave the copied folders: "site" not at all; "linked" (default) a link in
        # a page's content (not its menus) brings in the folder it points to, one step out from the start
        # folders; "everything" every link anywhere, up to max_pages
        self.follow = follow
        self.base_prefixes = list(self.prefixes)
        self.max_pages, self.workers, self.delay, self.timeout = max_pages, workers, delay, timeout
        self.headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self.robots, self.files, self.log = robots, files, log
        self.save_html = Path(save_html) if save_html else None  # the HTML each page was converted from
        self.browser = browser  # BrowserFetcher: pages are rendered in a signed-in browser (one at a time)
        if browser:
            self.workers = 1
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.lock = threading.Lock()
        # outside: links leading out of the copy (url -> the page linking to it), so a person can add them
        self.manifest = {"start": self.start, "pages": {}, "files": {}, "failed": {}, "skipped": {}, "queue": [],
                         "outside": {}, "linked": []}
        self.seen: set[str] = set()
        self.hashes: dict[str, str] = {}
        self.used: set[str] = set()
        # for a program driving the crawl (the site2md page): a call per finished URL, pause and stop
        self.on_page = on_page  # on_page(url, outcome text)
        self.paused, self.stopped = threading.Event(), threading.Event()
        self.progress = {"done": 0, "queued": 0, "running": 0}
        self._again: list[str] = []  # failed URLs to try again (after signing in again)

    def try_again(self, urls: list[str]) -> None:
        with self.lock:
            for u in urls:
                self.manifest["failed"].pop(u, None)
            self._again += urls

    # -- scope and politeness -------------------------------------------------------------------------------
    def in_scope(self, url: str) -> bool:
        p = urlsplit(url)
        if self.follow == "everything" and p.scheme in ("http", "https") and not NOT_DOCS.search(p.hostname or ""):
            return not NO_FOLLOW.search(url) and not SKIP_TYPES.search(p.path)
        if p.scheme not in ("http", "https") or p.netloc not in self.hosts:
            return False
        if p.netloc not in self.allow_hosts and not any(url.startswith(x) for x in self.prefixes):
            return False
        return not NO_FOLLOW.search(url) and not SKIP_TYPES.search(p.path)

    def follow_out(self, url: str, page: str) -> bool:
        """A content link from a start-folder page to somewhere else: bring in the folder it points to (only
        the page itself when that folder is a site's front page). Returns True when the URL is now in scope."""
        p = urlsplit(url)
        if (self.follow != "linked" or isinstance(url, NavLink) or p.scheme not in ("http", "https")
                or NOT_DOCS.search(p.hostname or "") or NO_FOLLOW.search(url) or SKIP_TYPES.search(p.path)
                or not any(page.startswith(x) for x in self.base_prefixes)):
            return False
        folder = start_folder(url)
        prefix = folder if urlsplit(folder).path != "/" else url
        with self.lock:
            self.prefixes.append(prefix)
            self.hosts.add(p.netloc)
            self.manifest.setdefault("linked", []).append(prefix)
        self.log(f"following a link out of the site: {prefix} (from {page})")
        return True

    def allowed(self, url: str) -> bool:
        if not self.robots:
            return True
        root = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.parse(self._get(root + "/robots.txt")[0].decode("utf-8", "replace").splitlines())
            except Exception:  # noqa: BLE001 - no robots.txt: everything allowed
                rp = None
            self._robots[root] = rp
        rp = self._robots[root]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def _get(self, url: str, page: bool = False) -> tuple[bytes, str, str, str]:
        """(body, content type, final URL after redirects, file name from Content-Disposition)."""
        if self.browser:
            return self.browser.get(url, render=page)
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # http(s) only (in_scope)
            data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError(f"larger than {MAX_BYTES // 1_000_000} MB")
            return data, r.headers.get("Content-Type", ""), r.geturl(), r.headers.get_filename() or ""

    def _head(self, url: str) -> tuple[str, int, str]:
        try:
            if self.browser:
                return self.browser.head(url)
            req = urllib.request.Request(url, headers=self.headers, method="HEAD")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return (r.headers.get("Content-Type", ""), int(r.headers.get("Content-Length") or 0),
                        r.headers.get_filename() or "")
        except Exception:  # noqa: BLE001 - HEAD not supported: decide after downloading
            return "", 0, ""

    def sitemap_urls(self) -> list[str]:
        root = "{0.scheme}://{0.netloc}".format(urlsplit(self.start))
        todo, found, done = [root + "/sitemap.xml"], [], set()
        rp = self._robots.get(root)
        if rp is not None and rp.site_maps():
            todo += rp.site_maps()
        while todo and len(done) < 50:
            sm = todo.pop()
            if sm in done:
                continue
            done.add(sm)
            try:
                xml = self._get(sm)[0].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001, S112 - no sitemap
                continue
            for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
                (todo if loc.endswith(".xml") else found).append(loc if loc.endswith(".xml") else normalize(loc))
        return found

    # -- one URL ---------------------------------------------------------------------------------------------
    def fetch(self, url: str) -> list[str]:
        if not self.allowed(url):
            with self.lock:
                self.manifest["skipped"][url] = "robots.txt"
            return []
        try:
            data, ctype, final, _ = self._get(url, page=not FILE_TYPES.get(Path(urlsplit(url).path).suffix.lower()))
        except urllib.error.HTTPError as e:
            with self.lock:
                self.manifest["failed"][url] = f"HTTP {e.code}"
            return []
        except Exception as e:  # noqa: BLE001 - network errors are recorded, the crawl goes on
            with self.lock:
                self.manifest["failed"][url] = str(e)[:200]
            return []
        finally:
            time.sleep(self.delay)
        final = normalize(final)
        if final != url and LOGIN.search(urlsplit(final).netloc + urlsplit(final).path):  # redirected to a sign-in
            with self.lock:
                self.manifest["failed"][url] = "sign-in page: run once with --browser --login"
            return []
        if final != url and not self.in_scope(final):
            with self.lock:
                self.manifest["skipped"][url] = f"redirects out of scope: {final}"
            return []
        ext = Path(urlsplit(final).path).suffix.lower()
        kind = FILE_TYPES.get(ext) or ("pdf" if "pdf" in ctype else "docx" if "wordprocessingml" in ctype else
                                       "pptx" if "presentationml" in ctype else None)
        if kind and "html" not in ctype:
            if self.files:
                self._save_file(final, data, kind)
            return []
        if "html" not in ctype and data.lstrip()[:1] != b"<":
            with self.lock:
                self.manifest["skipped"][url] = f"not a page ({ctype or 'unknown type'})"
            return []
        html = _decode(data, ctype)
        links = page_links(html, final)
        attached = []
        if self.files:
            docs = embedded_documents(html) + self._folder_documents(embedded_folders(html))
            attached = [self._embedded(u) for u in dict.fromkeys(docs)]
            self._unsaved_frames(html, final)
        self._save_page(final, html, [a for a in attached if a])
        return links

    def _folder_documents(self, folders: list[str], depth: int = 0) -> list[str]:
        """Download URLs of the files in embedded Drive folders (Drive's folder view lists them as links),
        with subfolders up to three levels down."""
        docs = []
        for folder_id in folders:
            url = FOLDER_VIEW.format(folder_id)
            with self.lock:
                if url in self.seen:
                    continue
                self.seen.add(url)
            try:
                listing = _decode(*self._get(url)[:2])
            except Exception as e:  # noqa: BLE001 - e.g. folder not shared with this account
                with self.lock:
                    self.manifest["failed"][url] = f"embedded Drive folder: {str(e)[:150]}"
                continue
            docs += embedded_documents(listing)
            if depth < 3:
                docs += self._folder_documents(embedded_folders(listing), depth + 1)
        return docs

    def _unsaved_frames(self, html: str, page_url: str) -> None:
        """Record what else the page embeds from other sites (calendars, forms, videos, published sheets): it is
        not saved, so the manifest says what the page shows beyond its text."""
        for src in dict.fromkeys(IFRAME.findall(html)):
            src = urljoin(page_url, src.replace("&amp;", "&"))
            p = urlsplit(src)
            if p.scheme not in ("http", "https") or p.netloc in self.hosts or EMBED.search(src) or FOLDER.search(src):
                continue
            with self.lock:
                self.manifest["skipped"].setdefault(
                    src, f"embedded {p.netloc}/{p.path.strip('/').split('/')[0]} not saved (on {page_url})")

    def _embedded(self, url: str) -> tuple | None:
        """Download a Drive/Docs file the page embeds (another host, so outside the crawl scope; robots.txt does
        not apply to a file the page shows): (url, data, kind, file name); data is None if already saved."""
        url = normalize(url)
        with self.lock:
            if url in self.manifest["files"]:
                return url, None, None, self.manifest["files"][url]
            if url in self.seen:
                return None
            self.seen.add(url)
        # decided before downloading: only PDF, Word and PowerPoint files are kept (videos and spreadsheets
        # uploaded to Drive are skipped; Google Docs and Slides are exported as Word and PDF), up to 500 MB
        ctype, size, filename = self._head(url)
        ext = (Path(filename).suffix.lower().split() or [""])[0]  # "Deck.pptx UPDATED" -> .pptx
        reason = (f"embedded {ext[1:].upper() or 'file'} is not a PDF, Word or PowerPoint file: {filename}"
                  if filename and ext not in KEEP else
                  f"embedded file of type {ctype}" if ctype and not DOCUMENT.search(ctype) else
                  f"embedded file larger than {MAX_BYTES // 1_000_000} MB ({size // 1_000_000} MB)"
                  if size > MAX_BYTES else "")
        if reason:
            with self.lock:
                self.manifest["skipped"][url] = reason
            return None
        try:
            data, ctype, _, filename = self._get(url)
            if "html" in ctype and (confirm := virus_scan_confirm(data.decode("utf-8", "replace"))):
                # Drive's "can't scan this file for viruses" page (files over 100 MB): "Download anyway" for the
                # files that are kept
                confirm_url, filename = confirm
                ext = (Path(filename).suffix.lower().split() or [""])[0]
                if filename and ext not in KEEP:  # no name shown: downloaded, then decided by content
                    with self.lock:
                        self.manifest["skipped"][url] = (f"embedded {ext[1:].upper() or 'file'} is not a PDF, Word "
                                                         f"or PowerPoint file: {filename}")
                    return None
                data, ctype, _, name = self._get(confirm_url)
                filename = name or filename
        except Exception as e:  # noqa: BLE001 - e.g. download disabled by the owner
            with self.lock:
                self.manifest["failed"][url] = f"embedded file: {str(e)[:150]}"
            return None
        ext = (Path(filename).suffix.lower().split() or [""])[0]
        kind = ("pdf" if "pdf" in ctype or data[:5] == b"%PDF-" else "docx" if "wordprocessingml" in ctype else
                "pptx" if "presentationml" in ctype else
                ext[1:] if ext in KEEP and data[:2] == b"PK" else None)  # Drive: octet-stream, named .docx/.pptx
        if not kind:
            with self.lock:
                self.manifest["skipped"][url] = (
                    "embedded file not downloadable (sign-in, or download disabled)" if "html" in ctype
                    else f"embedded {ext[1:].upper() or 'file'} is not a PDF, Word or PowerPoint file: "
                         f"{filename or ctype or 'unknown type'}")
            return None
        return url, data, kind, filename

    def _save_file(self, url: str, data: bytes, kind: str, filename: str = "") -> str:
        """Save a linked or embedded document as it is (the indexer reads PDF, Word and PowerPoint itself)."""
        digest = hashlib.sha256(data).hexdigest()
        stem = re.sub(r"(?i)(\.(pdf|docx?|pptx?|xlsx?))+$", "", filename).strip()
        with self.lock:
            if digest in self.hashes:
                self.manifest["files"].setdefault(url, self.hashes[digest])
                return self.hashes[digest]
            name = (file_name_for(stem, self.used) if stem else slug_for(url, self.used, self.root)) + "." + kind
            self.hashes[digest] = name
            self.manifest["files"][url] = name
        (self.out / name).write_bytes(data)
        return name

    def _save_page(self, url: str, html: str, attached: list[tuple] = ()) -> None:
        title, md = html_to_markdown(html)
        docs = []  # (label, url, local file)
        for doc_url, data, kind, filename in attached:
            if data is None:  # saved from an earlier page: filename is the local file
                docs.append((filename.rsplit(".", 1)[0], doc_url, filename))
            else:
                name = self._save_file(doc_url, data, kind, filename)
                docs.append((name.rsplit(".", 1)[0], doc_url, name))
        if not docs and not _own_text(re.sub(r"(?m)^#{1,6} .*$", "", md)):
            with self.lock:  # only a title or headings (e.g. a page that only embedded files that were not saved)
                self.manifest["skipped"][url] = ("no text of its own (only a title or headings; if a browser shows "
                                                  "text there, crawl with --browser)")
            return
        if docs:  # embedded documents: linked (to the local file), so they are connected to this page after ingest
            md += "\n\n## Attached documents\n\n" + "\n".join(f"- [{label}]({quote(name)})" for label, _, name in docs)
        digest = hashlib.sha256(md.encode()).hexdigest()
        with self.lock:
            if digest in self.hashes:  # same page under another URL
                self.manifest["skipped"][url] = f"same content as {self.hashes[digest]}"
                return
            name = slug_for(url, self.used, self.root) + ".md"
            self.hashes[digest] = name
            self.manifest["pages"][url] = {"file": name, "title": title, **({"empty": True} if len(md) < 40 else {}),
                                           **({"attached": [name for _, _, name in docs]} if docs else {})}
        title = title or (re.search(r"(?m)^#\s+(.+)$", md) or [None, urlsplit(url).path])[1]
        (self.out / name).write_text(_front(title, url) + md + "\n", encoding="utf-8")
        if self.save_html:
            self.save_html.mkdir(parents=True, exist_ok=True)
            (self.save_html / name.replace(".md", ".html")).write_text(html, encoding="utf-8")

    # -- crawl -----------------------------------------------------------------------------------------------
    def run(self, resume: bool = False) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        mpath = self.out / "manifest.json"
        queue = _Queue(lambda u: any(u.startswith(x) for x in self.base_prefixes))
        self.queue = queue  # read by the site2md page (pages waiting, per section)

        def add(urls, page=""):  # each URL is queued once (every page repeats the navigation links)
            outside = self.manifest.setdefault("outside", {})
            for u in urls:
                if u in self.seen:
                    continue
                twin = re.sub(r"\.(md|markdown|txt)$", ".html", u, flags=re.IGNORECASE)
                if twin != u and twin in self.seen:  # docs sites offer each page as Markdown too: the same page
                    self.seen.add(u)
                    self.manifest["skipped"][u] = f"same page as {twin}"
                    continue
                if self.in_scope(u) or (page and self.follow_out(u, page)):
                    self.seen.add(u)
                    queue.append(u)
                    outside.pop(u, None)
                elif page and len(outside) < 5000 and urlsplit(u).scheme in ("http", "https") \
                        and not NO_FOLLOW.search(u) and not SKIP_TYPES.search(urlsplit(u).path):
                    outside.setdefault(u, page)

        if resume and mpath.exists():
            self.manifest = json.loads(mpath.read_text())
            for x in self.manifest.get("linked", []):  # sections brought in by links, in an earlier run
                self.prefixes.append(x)
                self.hosts.add(urlsplit(x).netloc)
            for url, v in self.manifest["pages"].items():
                self.used.add(v["file"].removesuffix(".md"))
                self.hashes[hashlib.sha256((self.out / v["file"]).read_bytes()).hexdigest()] = v["file"]
            for name in self.manifest["files"].values():
                self.used.add(name.rsplit(".", 1)[0].lower())
            retry, self.manifest["failed"] = list(self.manifest["failed"]), {}  # failures are tried again
            self.seen = set(self.manifest["pages"]) | set(self.manifest["files"]) | set(self.manifest["skipped"])
            add(retry + self.manifest["queue"])
        if not queue:
            add([self.start])
        self.allowed(self.start)  # loads robots.txt (and its sitemap list)
        add(self.sitemap_urls())
        t0, last_save, done, url = time.time(), time.time(), 0, ""
        with _Inline() if self.browser else ThreadPoolExecutor(self.workers) as pool:  # browser: its own thread only
            running = {}
            try:
                while (queue or running or self._again or self.paused.is_set()) and not self.stopped.is_set():
                    with self.lock:
                        queue.extendleft(self._again)
                        self._again = []
                    while (queue and not self.paused.is_set() and len(running) < self.workers
                           and self._count() < self.max_pages):
                        url, started = queue.popleft(), time.time()  # (browser mode runs fetch inside submit)
                        running[pool.submit(self.fetch, url)] = url, started
                    self.progress = {"done": done, "queued": len(queue), "running": len(running)}
                    if not running:
                        if self.paused.is_set():
                            time.sleep(0.2)
                            continue
                        break
                    finished, _ = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
                    for fut in finished:
                        url, started = running.pop(fut)
                        add(fut.result(), url)
                        done += 1
                        outcome = self._outcome(url)
                        self.log(f"[{done}/{done + len(queue) + len(running)}] {time.time() - started:4.1f} s  "
                                 f"{url}  {outcome}")
                        if self.on_page:
                            self.on_page(url, outcome)
                    if time.time() - last_save > 30:
                        self._save_manifest(mpath, queue)
                        last_save = time.time()
                if self.stopped.is_set():
                    raise KeyboardInterrupt
            except KeyboardInterrupt:  # stopped by the person: keep what was saved, continue later with --resume
                m = self.manifest
                unfinished = [u for u, _ in running.values()] + ([url] if url else [])
                queue.extendleft(u for u in unfinished if not any(u in m[k] for k in ("pages", "files", "skipped")))
                self.log("stopped - saving what was crawled; run again with --resume to continue")
            self.progress = {"done": done, "queued": len(queue), "running": 0}
        self.rewrite_links()
        self._save_manifest(mpath, queue)
        m = self.manifest
        self.log(f"done in {time.time() - t0:.0f} s: {len(m['pages'])} pages, {len(m['files'])} files, {len(m['failed'])} failed, "
                 f"{len(m['skipped'])} skipped, {len(queue)} left in queue -> {self.out}")
        return m

    def _outcome(self, url: str) -> str:
        m = self.manifest
        with self.lock:
            if url in m["pages"]:
                page = m["pages"][url]
                extra = f" + {len(page['attached'])} attached document(s)" if page.get("attached") else ""
                return "-> " + page["file"] + (" (no text)" if page.get("empty") and not extra else "") + extra
            for key, label in (("files", "-> "), ("failed", "FAILED: "), ("skipped", "skipped: ")):
                if url in m[key]:
                    return label + m[key][url]
        return "-> (redirected)"

    def _count(self) -> int:
        return len(self.manifest["pages"]) + len(self.manifest["files"])

    def _save_manifest(self, path: Path, queue) -> None:
        with self.lock:
            self.manifest["queue"] = list(dict.fromkeys(queue))
            path.write_text(json.dumps(self.manifest, indent=1))

    def site_suffix(self) -> str:
        """A title suffix most pages share (" — Python 3.15 documentation", " | Company Wiki"): site chrome,
        not part of the page's name."""
        counts: dict[str, int] = {}
        titles = [v["title"] for v in self.manifest["pages"].values() if v["title"]]
        for title in titles:
            for m in re.finditer(r"\s+[|\u2013\u2014-]\s+", title):
                counts[title[m.start():]] = counts.get(title[m.start():], 0) + 1
        best = max(counts.items(), key=lambda x: (x[1], len(x[0])), default=("", 0))
        return best[0] if len(titles) >= 3 and best[1] >= 0.5 * len(titles) else ""

    def rewrite_links(self) -> None:
        """Links between crawled pages → local file names (graph edges after ingest); others → absolute URLs."""
        local = {u: v["file"] for u, v in self.manifest["pages"].items()} | self.manifest["files"]

        def fix(page_url: str, href: str) -> str:
            if re.match(r"^(mailto|tel|data|javascript):", href, re.IGNORECASE):
                return href
            if not re.match(r"^[a-z][a-z0-9+.-]*:|/", href, re.IGNORECASE) and \
                    (self.out / unquote(href.split("#")[0])).is_file():  # already a local file (attachments)
                return href
            absolute = urljoin(page_url, href)
            frag = urldefrag(absolute)[1]
            name = local.get(normalize(absolute))
            return f"{name}#{frag}" if name and frag else name or absolute

        suffix = self.site_suffix()
        for url, v in self.manifest["pages"].items():
            path = self.out / v["file"]
            text = path.read_text(encoding="utf-8")
            new = re.sub(r"\]\(([^)\s]+)\)", lambda m, u=url: f"]({fix(u, m.group(1))})", text)
            if suffix and v["title"].endswith(suffix) and len(v["title"]) > len(suffix):
                v["title"] = v["title"].removesuffix(suffix)
                new = new.replace(f"title: {json.dumps(v['title'] + suffix)}", f"title: {json.dumps(v['title'])}", 1)
            if new != text:
                path.write_text(new, encoding="utf-8")
