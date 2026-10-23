"""Website → Markdown crawler: point it at a URL, get one Markdown file per page (plus the PDFs / Word files
the site links to), ready for ``knowledge-search ingest``. Separate utility: no database, no models,
standard library only.

    knowledge-search crawl https://wiki.example.internal/ --out data/crawl/wiki
    knowledge-search ingest wiki data/crawl/wiki --wait

Traversal: breadth-first over every link on every page — content, navigation bar, header, footer, sidebars,
image maps, frames, <link rel=next> — plus the site's sitemap.xml, until no new page is found (or
``--max-pages``). Scope: the start URL's host (``--prefix`` narrows it to URL prefixes, ``--allow-host`` adds
hosts). Robots.txt is respected unless ``--ignore-robots`` (your own intranet). Links that change state or
end a session (logout, delete, edit, …) are never followed. Pages reached under several URLs are saved once
(same content).

Output: ``<name>.md`` per page with front matter (title, source_url, crawled_at); the page body is converted
like an uploaded HTML file (main content only, menus and footers dropped). After the crawl every link between
crawled pages is rewritten to the local file name, so they become graph edges when ingested; other links stay
absolute, and a title suffix shared by most pages (" | Company Wiki") is removed. ``manifest.json`` lists url → file, failures and the remaining queue; ``--resume`` continues an
interrupted crawl. Pages that need JavaScript to show their content come out empty (listed as ``empty`` in the
manifest); export those systems instead (Confluence/SharePoint export, API) or crawl their print views.
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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlsplit, urlunsplit

from .convert import html_to_markdown

USER_AGENT = "knowledge-search-crawler/1 (+offline knowledge search)"
# never follow: ends the session or changes data (crawlers that click "logout" lose the login; "delete"
# links on wikis are GET in old systems), plus endless calendars / print / feed variants
NO_FOLLOW = re.compile(
    r"log-?out|sign-?out|log-?off|/delete|[?&](action|do)=(edit|delete|remove|logout|diff|history|revision)"
    r"|/edit\b|/print\b|[?&]print=|/feed\b|/rss\b|\.rss$|/calendar/|[?&]replytocom=|[?&]share=", re.IGNORECASE)
FILE_TYPES = {".pdf": "pdf", ".docx": "docx", ".md": "md", ".markdown": "md", ".txt": "txt"}
SKIP_TYPES = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|mjs|json|xml|zip|gz|tgz|tar|exe|msi|dmg|iso|mp4|"
                        r"mp3|wav|avi|mov|woff2?|ttf|eot|pptx?|xlsx?|csv)$", re.IGNORECASE)
TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|_ga|ref|sessionid|jsessionid|phpsessid|sid)$", re.IGNORECASE)
MAX_BYTES = 50_000_000


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


def slug_for(url: str, used: set[str]) -> str:
    """Readable unique file stem from the URL path: /docs/Ops/Runbook.html -> docs-ops-runbook."""
    p = urlsplit(url)
    path = re.sub(r"\.(html?|php|aspx?|jsp)$", "", p.path, flags=re.IGNORECASE)
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
                 robots: bool = True, files: bool = True, timeout: float = 30, log=print):
        self.start, self.out = normalize(start), Path(out)
        host = urlsplit(self.start).netloc
        self.prefixes = [normalize(p) for p in prefixes] if prefixes else None
        self.hosts = {host, *(allow_hosts or [])}
        self.max_pages, self.workers, self.delay, self.timeout = max_pages, workers, delay, timeout
        self.headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self.robots, self.files, self.log = robots, files, log
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
        if self.prefixes and not any(url.startswith(x) for x in self.prefixes):
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

    def _get(self, url: str) -> tuple[bytes, str, str]:
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # http(s) only (in_scope)
            data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("larger than 50 MB")
            return data, r.headers.get("Content-Type", ""), r.geturl()

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
            data, ctype, final = self._get(url)
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
        self._save_page(final, html)
        return links

    def _save_file(self, url: str, data: bytes, kind: str) -> None:
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            if digest in self.hashes:
                return
            name = slug_for(url, self.used) + "." + kind
            self.hashes[digest] = name
            self.manifest["files"][url] = name
        (self.out / name).write_bytes(data)

    def _save_page(self, url: str, html: str) -> None:
        title, md = html_to_markdown(html)
        digest = hashlib.sha256(md.encode()).hexdigest()
        with self.lock:
            if digest in self.hashes:  # same page under another URL
                self.manifest["skipped"][url] = f"same content as {self.hashes[digest]}"
                return
            name = slug_for(url, self.used) + ".md"
            self.hashes[digest] = name
            self.manifest["pages"][url] = {"file": name, "title": title, **({"empty": True} if len(md) < 40 else {})}
        title = title or (re.search(r"(?m)^#\s+(.+)$", md) or [None, urlsplit(url).path])[1]
        front = (f"---\ntitle: {json.dumps(title)}\nsource_url: {json.dumps(url)}\n"
                 f"crawled_at: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n---\n\n")
        (self.out / name).write_text(front + md + "\n", encoding="utf-8")

    # -- crawl -----------------------------------------------------------------------------------------------
    def run(self, resume: bool = False) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        mpath = self.out / "manifest.json"
        queue: deque[str] = deque()
        if resume and mpath.exists():
            self.manifest = json.loads(mpath.read_text())
            for url, v in self.manifest["pages"].items():
                self.used.add(v["file"].removesuffix(".md"))
                self.hashes[hashlib.sha256((self.out / v["file"]).read_bytes()).hexdigest()] = v["file"]
            for name in self.manifest["files"].values():
                self.used.add(name.rsplit(".", 1)[0])
            self.seen = set(self.manifest["pages"]) | set(self.manifest["files"]) | set(self.manifest["failed"]) \
                | set(self.manifest["skipped"])
            queue.extend(u for u in self.manifest["queue"] if u not in self.seen)
        if not queue:
            queue.append(self.start)
        self.allowed(self.start)  # loads robots.txt (and its sitemap list)
        queue.extend(u for u in self.sitemap_urls() if self.in_scope(u))
        t0, last_save = time.time(), time.time()
        with ThreadPoolExecutor(self.workers) as pool:
            running = {}
            while queue or running:
                while queue and len(running) < self.workers and self._count() < self.max_pages:
                    url = queue.popleft()
                    if url in self.seen or not self.in_scope(url):
                        continue
                    self.seen.add(url)
                    running[pool.submit(self.fetch, url)] = url
                if not running:
                    break
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    running.pop(fut)
                    queue.extend(u for u in fut.result() if u not in self.seen and self.in_scope(u))
                if time.time() - last_save > 30:
                    self._save_manifest(mpath, queue)
                    last_save = time.time()
                    self.log(f"{self._count()} pages/files saved, {len(queue)} queued, {time.time() - t0:.0f} s")
        self.rewrite_links()
        self._save_manifest(mpath, queue)
        m = self.manifest
        self.log(f"done: {len(m['pages'])} pages, {len(m['files'])} files, {len(m['failed'])} failed, "
                 f"{len(m['skipped'])} skipped, {len(queue)} left in queue -> {self.out}")
        return m

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
            path = self.out / v["file"]
            text = path.read_text(encoding="utf-8")
            new = re.sub(r"\]\(([^)\s]+)\)", lambda m, u=url: f"]({fix(u, m.group(1))})", text)
            if suffix and v["title"].endswith(suffix) and len(v["title"]) > len(suffix):
                v["title"] = v["title"].removesuffix(suffix)
                new = new.replace(f"title: {json.dumps(v['title'] + suffix)}", f"title: {json.dumps(v['title'])}", 1)
            if new != text:
                path.write_text(new, encoding="utf-8")
