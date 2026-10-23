"""Website → Markdown crawler: point it at a URL, get one Markdown file per page, and per PDF / Word file the
site links to or embeds. Standard library plus pypdf; ``--browser`` (optional ``playwright`` package) drives
a real browser for sites behind a login or that build pages with JavaScript.

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
interrupted crawl. Pages that need JavaScript to show their content come out empty (listed as ``empty`` in the
manifest) unless ``--browser`` is used.
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
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlsplit, urlunsplit

from .docs import document_to_markdown
from .html import html_to_markdown

USER_AGENT = "site2md/1 (documentation crawler)"
# never follow: ends the session or changes data (crawlers that click "logout" lose the login; "delete"
# links on wikis are GET in old systems), plus endless calendars / print / feed variants
NO_FOLLOW = re.compile(
    r"log-?out|sign-?out|log-?off|/delete|[?&](action|do)=(edit|delete|remove|logout|diff|history|revision)"
    r"|/edit\b|/print\b|[?&]print=|/feed\b|/rss\b|\.rss$|/calendar/|[?&]replytocom=|[?&]share=", re.IGNORECASE)
FILE_TYPES = {".pdf": "pdf", ".docx": "docx", ".md": "md", ".markdown": "md", ".txt": "txt"}
SKIP_TYPES = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|mjs|json|xml|zip|gz|tgz|tar|exe|msi|dmg|iso|mp4|"
                        r"mp3|wav|avi|mov|woff2?|ttf|eot|pptx?|xlsx?|csv)$", re.IGNORECASE)
TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|_ga|ref|sessionid|jsessionid|phpsessid|sid|authuser)$",
                      re.IGNORECASE)  # authuser: Google's account selector, same page
MAX_BYTES = 50_000_000
THIN_PAGE = 200  # characters of text besides headings: below this, a page with embedded documents is not saved
# Google Drive / Docs / Slides files embedded in a page (Google Sites viewers, iframes, links): downloaded
# with the same sign-in, saved next to the page and linked from it
EMBED = re.compile(r"https://(?:drive|docs)\.google\.com/(?:a/[\w.-]+/)?(file|document|presentation)/d/([\w-]{20,})")
EXPORT = {"file": "https://drive.google.com/uc?export=download&id={}",
          "document": "https://docs.google.com/document/d/{}/export?format=docx",
          "presentation": "https://docs.google.com/presentation/d/{}/export/pdf"}
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

    def login(self, url: str) -> None:
        self.page.goto(url)
        input("Sign in in the browser window until you see the site, then press Enter here... ")

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
        return r.body(), r.headers.get("content-type", ""), r.url, _filename(r.headers.get("content-disposition", ""))

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


def _front(title: str, source_url: str, document_url: str = "") -> str:
    doc = f"document_url: {json.dumps(document_url)}\n" if document_url else ""
    return (f"---\ntitle: {json.dumps(title)}\nsource_url: {json.dumps(source_url)}\n{doc}"
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


class _Links(HTMLParser):
    """Every followable URL on a page, wherever it appears (content, nav, header, footer, frames)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base, self.links = "", []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
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
        href = href.strip()
        if href and not href.lower().startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
            out.append(normalize(urljoin(base, href)))
    return out


def _decode(data: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', data[:4096], re.IGNORECASE)
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


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


class Crawler:
    def __init__(self, start: str, out: Path, *, prefixes: list[str] | None = None, allow_hosts: list[str] | None = None,
                 max_pages: int = 10_000, workers: int = 4, delay: float = 0.2, headers: dict | None = None,
                 robots: bool = True, files: bool = True, timeout: float = 30, log=print, browser=None):
        self.start, self.out = normalize(start), Path(out)
        host = urlsplit(self.start).netloc
        folder = self.start if self.start.endswith("/") else self.start.rsplit("/", 1)[0] + "/"
        self.prefixes = [normalize(p) for p in prefixes] if prefixes else [folder.split("?")[0]]
        self.root = urlsplit(self.prefixes[0]).path.rstrip("/")  # file names are relative to it
        self.allow_hosts = set(allow_hosts or [])
        self.hosts = {host, *self.allow_hosts}
        self.max_pages, self.workers, self.delay, self.timeout = max_pages, workers, delay, timeout
        self.headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self.robots, self.files, self.log = robots, files, log
        self.browser = browser  # BrowserFetcher: pages are rendered in a signed-in browser (one at a time)
        if browser:
            self.workers = 1
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.lock = threading.Lock()
        self.manifest = {"start": self.start, "pages": {}, "files": {}, "failed": {}, "skipped": {}, "queue": []}
        self.seen: set[str] = set()
        self.hashes: dict[str, str] = {}
        self.used: set[str] = set()

    # -- scope and politeness -------------------------------------------------------------------------------
    def in_scope(self, url: str) -> bool:
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or p.netloc not in self.hosts:
            return False
        if p.netloc not in self.allow_hosts and not any(url.startswith(x) for x in self.prefixes):
            return False
        return not NO_FOLLOW.search(url) and not SKIP_TYPES.search(p.path)

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
                raise ValueError("larger than 50 MB")
            return data, r.headers.get("Content-Type", ""), r.geturl(), r.headers.get_filename() or ""

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
        kind = FILE_TYPES.get(ext) or ("pdf" if "pdf" in ctype else "docx" if "wordprocessingml" in ctype else None)
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
        attached = [self._embedded(u) for u in embedded_documents(html)] if self.files else []
        self._save_page(final, html, [a for a in attached if a])
        return links

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
        try:
            data, ctype, _, filename = self._get(url)
        except Exception as e:  # noqa: BLE001 - e.g. download disabled by the owner
            with self.lock:
                self.manifest["failed"][url] = f"embedded file: {str(e)[:150]}"
            return None
        kind = "pdf" if "pdf" in ctype or data[:5] == b"%PDF-" else "docx" if "wordprocessingml" in ctype else None
        if not kind:
            with self.lock:
                self.manifest["skipped"][url] = ("embedded file not downloadable (sign-in, or download disabled)"
                                                 if "html" in ctype else f"embedded file of type {ctype or 'unknown'}")
            return None
        return url, data, kind, filename

    def _save_file(self, url: str, data: bytes, kind: str, filename: str = "", title: str = "",
                   page_url: str = "") -> str:
        """Save a linked or embedded document. PDF and Word files become Markdown (front matter: title,
        source_url = the page showing it, document_url); a file without text (scanned PDF) is kept as it is."""
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            if digest in self.hashes:
                self.manifest["files"].setdefault(url, self.hashes[digest])
                return self.hashes[digest]
        doc_title, md = "", ""
        if kind in ("pdf", "docx"):
            try:
                doc_title, md = document_to_markdown(data, kind)
            except Exception:  # noqa: BLE001, S110 - damaged or protected: the original is kept
                pass
        stem = re.sub(r"(?i)(\.(pdf|docx?|pptx?|xlsx?))+$", "", filename).strip()
        with self.lock:
            if digest in self.hashes:  # saved by another worker meanwhile
                self.manifest["files"].setdefault(url, self.hashes[digest])
                return self.hashes[digest]
            name = slug_for("/" + re.sub(r"[?#]", " ", stem), self.used) if stem else slug_for(url, self.used, self.root)
            name += ".md" if md.strip() else "." + kind
            self.hashes[digest] = name
            self.manifest["files"][url] = name
        if md.strip():
            title = title or doc_title or stem or name.removesuffix(".md")
            (self.out / name).write_text(_front(title, page_url or url, url if page_url else "") + md + "\n",
                                         encoding="utf-8")
        else:
            (self.out / name).write_bytes(data)
        return name

    def _save_page(self, url: str, html: str, attached: list[tuple] = ()) -> None:
        title, md = html_to_markdown(html)
        # only a heading around a document viewer: the document is saved (with the page's title and URL)
        # instead of a title-only page, and links to the page lead to it
        thin = bool(attached) and len(re.sub(r"(?m)^#+ .*$", "", md).strip()) < THIN_PAGE
        docs = []
        for doc_url, data, kind, filename in attached:
            name = filename if data is None else self._save_file(
                doc_url, data, kind, filename, title=title if thin and len(attached) == 1 else "", page_url=url)
            docs.append((name if data is None else filename or name, doc_url, name))
        if thin:
            with self.lock:
                self.manifest["pages"][url] = {"file": docs[0][2], "title": title, "document_only": True}
            return
        if docs:  # embedded documents: linked, so they are connected to this page after ingest
            md += "\n\n## Attached documents\n\n" + "\n".join(f"- [{label}]({u})" for label, u, _ in docs)
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

    # -- crawl -----------------------------------------------------------------------------------------------
    def run(self, resume: bool = False) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        mpath = self.out / "manifest.json"
        queue: deque[str] = deque()

        def add(urls):  # each URL is queued once (every page repeats the navigation links)
            for u in urls:
                if u not in self.seen and self.in_scope(u):
                    self.seen.add(u)
                    queue.append(u)

        if resume and mpath.exists():
            self.manifest = json.loads(mpath.read_text())
            for url, v in self.manifest["pages"].items():
                if v.get("document_only"):
                    continue
                self.used.add(v["file"].removesuffix(".md"))
                self.hashes[hashlib.sha256((self.out / v["file"]).read_bytes()).hexdigest()] = v["file"]
            for name in self.manifest["files"].values():
                self.used.add(name.rsplit(".", 1)[0])
            retry, self.manifest["failed"] = list(self.manifest["failed"]), {}  # failures are tried again
            self.seen = set(self.manifest["pages"]) | set(self.manifest["files"]) | set(self.manifest["skipped"])
            add(retry + self.manifest["queue"])
        if not queue:
            add([self.start])
        self.allowed(self.start)  # loads robots.txt (and its sitemap list)
        add(self.sitemap_urls())
        t0, last_save, done = time.time(), time.time(), 0
        with _Inline() if self.browser else ThreadPoolExecutor(self.workers) as pool:  # browser: its own thread only
            running = {}
            while queue or running:
                while queue and len(running) < self.workers and self._count() < self.max_pages:
                    url, started = queue.popleft(), time.time()  # (browser mode runs fetch inside submit)
                    running[pool.submit(self.fetch, url)] = url, started
                if not running:
                    break
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    url, started = running.pop(fut)
                    add(fut.result())
                    done += 1
                    self.log(f"[{done}/{done + len(queue) + len(running)}] {time.time() - started:4.1f} s  "
                             f"{url}  {self._outcome(url)}")
                if time.time() - last_save > 30:
                    self._save_manifest(mpath, queue)
                    last_save = time.time()
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
                if page.get("document_only"):
                    return "-> no page text, only the embedded document " + page["file"]
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
            absolute = urljoin(page_url, href)
            frag = urldefrag(absolute)[1]
            name = local.get(normalize(absolute))
            return f"{name}#{frag}" if name and frag else name or absolute

        suffix = self.site_suffix()
        for url, v in self.manifest["pages"].items():
            if v.get("document_only"):
                continue
            path = self.out / v["file"]
            text = path.read_text(encoding="utf-8")
            new = re.sub(r"\]\(([^)\s]+)\)", lambda m, u=url: f"]({fix(u, m.group(1))})", text)
            if suffix and v["title"].endswith(suffix) and len(v["title"]) > len(suffix):
                v["title"] = v["title"].removesuffix(suffix)
                new = new.replace(f"title: {json.dumps(v['title'] + suffix)}", f"title: {json.dumps(v['title'])}", 1)
            if new != text:
                path.write_text(new, encoding="utf-8")
