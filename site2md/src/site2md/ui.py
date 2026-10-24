"""The site2md page: copy a website from the browser instead of the console (docs/plan-site2md-ui.md).

``site2md`` with no arguments starts a small web server on 127.0.0.1 (a random port) and opens the page in
the person's browser. Only that page can drive it: every request needs the one-time key in the address, and
the Host header must be this address. Python's standard library only.

One copy runs at a time. Copies are folders under ``copies`` (default: ``output`` next to the launchers, or
~/site2md); each keeps the crawler's manifest.json and the options it was made with (.site2md.json).
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .crawl import BrowserFetcher, Crawler, normalize, start_folder

STATIC = Path(__file__).parent / "static"
SIGNIN_HOSTS = re.compile(r"(^|\.)(accounts\.google\.com|login\.microsoftonline\.com|login\.live\.com|"
                          r"okta\.com|oktapreview\.com|auth0\.com|onelogin\.com|duosecurity\.com|pingone\.com)$")
SIGNIN_PATH = re.compile(r"(?i)/(login|log-in|signin|sign-in|sso|saml|oauth2?|auth|authorize|cas)(\b|/|\.|$)")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
KEEP = {".md", ".pdf", ".docx", ".pptx", ".txt", ".html", ".htm"}  # what Send to Knowledge Search uploads


def name_for(url: str) -> str:
    u = urlsplit(url if "://" in url else "https://" + url)
    first = next((x for x in u.path.split("/") if x), "")
    return re.sub(r"[^a-z0-9]+", "-", f"{(u.hostname or '').removeprefix('www.')} {first}".lower()).strip("-")[:60] or "site"


def looks_like_signin(url: str, start: str) -> bool:
    u, s = urlsplit(url), urlsplit(start)
    return bool(SIGNIN_HOSTS.search(u.hostname or "")) or (u.hostname != s.hostname and "login" in url.lower()) \
        or bool(SIGNIN_PATH.search(u.path))


def check(url: str) -> dict:
    """Does the address answer, and does it send people to a sign-in page first?"""
    url = url if "://" in url else "https://" + url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (site2md check)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            final, body = r.geturl(), r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"url": url, "ok": True, "signin": True, "note": "The site asks who you are."}
        return {"url": url, "ok": False, "note": f"The site answered HTTP {e.code}."}
    except Exception as e:  # noqa: BLE001 - DNS, TLS, timeouts: shown to the person
        return {"url": url, "ok": False, "note": f"Could not open it: {getattr(e, 'reason', e)}"}
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    signin = looks_like_signin(final, url) or bool(re.search(r'(?i)<input[^>]+type=["\']?password', body))
    return {"url": url, "ok": True, "signin": signin, "final": final,
            "title": re.sub(r"\s+", " ", title.group(1)).strip()[:120] if title else ""}


def browsers() -> list[dict]:
    """Browsers on this computer the crawler can drive (no download needed), best first."""
    env = os.environ.get
    places = {
        "chrome": [Path(env("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
                   Path(env("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
                   Path(env("LOCALAPPDATA", "~")) / "Google/Chrome/Application/chrome.exe",
                   Path("/Applications/Google Chrome.app"), Path("/opt/google/chrome/chrome")],
        "msedge": [Path(env("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                   Path(env("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
                   Path("/Applications/Microsoft Edge.app"), Path("/opt/microsoft/msedge/msedge")],
    }
    out = [{"channel": c, "name": {"chrome": "Google Chrome", "msedge": "Microsoft Edge"}[c]}
           for c, ps in places.items() if any(p.exists() for p in ps)]
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        have_pw = True
    except ImportError:
        have_pw = False
    pw_dir = Path(env("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / (
        "AppData/Local/ms-playwright" if sys.platform == "win32" else
        "Library/Caches/ms-playwright" if sys.platform == "darwin" else ".cache/ms-playwright"))
    if have_pw and pw_dir.is_dir() and any(d.name.startswith("chromium") for d in pw_dir.iterdir()):
        out.append({"channel": "chromium", "name": "Chromium (downloaded for site2md)"})
    return [dict(b, ready=have_pw) for b in out]


# --- a copy on disk ----------------------------------------------------------------------------------------

ACTIVE = ("starting", "signin", "copying", "paused", "stopping")


def _manifest(folder: Path) -> dict:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _options(folder: Path) -> dict:
    """.site2md.json: the address, the options, the total copying time and how the last run ended."""
    try:
        return json.loads((folder / ".site2md.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_options(folder: Path, data: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".site2md.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


REASONS = [  # (pattern in the crawler's note, plain words), first match wins
    (r"^embedded \S+ not saved", "embedded video or widget from another site"),
    (r"^same page as", "the same page in another format"), (r"sign-in", "needed sign-in"),
    (r"robots\.txt", "the site asks robots not to copy it"), (r"HTTP (404|410)", "page not found"),
    (r"HTTP \d+", "the server refused it"), (r"timed? ?out", "the site did not answer in time"),
    (r"no text of its own|no text", "no text on the page"), (r"same content as", "same text as another page"),
    (r"not a page|not a PDF|video|spreadsheet|larger than|not downloadable", "video, spreadsheet or file not kept"),
    (r"out of scope|outside", "redirects outside the copy"), (r".", "other problems"),
]


def reason(note: str) -> str:
    return next(words for pat, words in REASONS if re.search(pat, note, re.IGNORECASE))


def not_copied(m: dict) -> list[dict]:
    groups: dict[str, dict] = {}
    for kind in ("failed", "skipped"):
        for url, note in m.get(kind, {}).items():
            g = groups.setdefault(reason(note), {"reason": reason(note), "count": 0, "example": url, "failed": 0,
                                                 "note": note})
            g["count"] += 1
            g["failed"] += kind == "failed"
    return sorted(groups.values(), key=lambda g: (-g["failed"], -g["count"]))


def sections(m: dict, queue: list[str]) -> list[dict]:
    """The copy's sections: the first folder below the start folder, or the other site a page came from."""
    start = m.get("start", "")
    host, root = urlsplit(start).netloc, urlsplit(start_folder(start)).path.rstrip("/") if start else ""

    def section(url: str) -> str:
        p = urlsplit(url)
        if p.netloc != host:
            return p.netloc
        parts = [x for x in (p.path.removeprefix(root) if p.path.startswith(root) else p.path).split("/") if x]
        return parts[0] if len(parts) > 1 else "(top level)"

    out: dict[str, dict] = {}
    skipped = [u for u, note in m.get("skipped", {}).items() if not note.startswith(("embedded ", "same page as"))]
    for kind, urls in (("saved", [*m.get("pages", {}), *m.get("files", {})]), ("failed", list(m.get("failed", {}))),
                       ("skipped", skipped), ("waiting", queue)):
        for u in urls:
            s = out.setdefault(section(u), {"name": section(u), "saved": 0, "failed": 0, "skipped": 0, "waiting": 0})
            s[kind] += 1
    rows = sorted(out.values(), key=lambda s: (s["name"] != "(top level)", "." in s["name"],
                                               -(s["saved"] + s["waiting"]), s["name"]))
    return rows[:40] + ([{"name": f"{len(rows) - 40} more sections", "more": True}] if len(rows) > 40 else [])


def outside_groups(m: dict, limit: int = 12) -> list[dict]:
    """Links that lead out of the copy, grouped by site and folder, with the folder that would bring them in."""
    groups: dict[tuple, list] = {}
    for url in m.get("outside", {}):
        p = urlsplit(url)
        first = next((x for x in p.path.split("/") if x), "")
        groups.setdefault((p.scheme, p.netloc, first), []).append(url)
    out = []
    for (scheme, netloc, _), urls in groups.items():
        dirs = [urlsplit(u).path.split("/")[1:-1] for u in urls]  # folders of each link's path
        common = dirs[0]
        for d in dirs[1:]:
            common = [a for a, b in zip(common, d, strict=False) if a == b][:len(common)]
            n = next((i for i, (a, b) in enumerate(zip(common, d, strict=False)) if a != b), len(common))
            common = common[:n]
        prefix = f"{scheme}://{netloc}/" + "".join(f"{x}/" for x in common)
        if len(urls) == 1 and not common:  # one link to a site's front page: just that page
            prefix = urls[0]
        out.append({"prefix": prefix, "count": len(urls), "example": urls[0], "from": m["outside"][urls[0]]})
    return sorted(out, key=lambda g: -g["count"])[:limit]


def copy_state(m: dict, o: dict) -> str:
    if not m:
        return "not started"
    return o.get("last", {}).get("state") or ("stopped" if m.get("queue") else "done")


def copy_view(folder: Path) -> dict:
    m, o = _manifest(folder), _options(folder)
    pages, files, failed, left = (len(m.get("pages", {})), len(m.get("files", {})), len(m.get("failed", {})),
                                  len(m.get("queue", [])))
    state = copy_state(m, o)
    status = {"done": "complete", "stopped": "stopped", "signin-expired": "sign-in expired", "failed": "stopped with an error"
              }.get(state, state)
    try:
        updated = (folder / "manifest.json").stat().st_mtime
    except OSError:
        updated = None
    return {"name": folder.name, "url": m.get("start") or o.get("url", ""), "pages": pages, "files": files,
            "failed": failed, "left": left, "state": state, "status": status, "updated": updated,
            "options": o.get("options", {}), "updating": folder.with_name(folder.name + ".new").is_dir()}


def _tail(path: Path, n: int = 3000) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [x.rstrip("\n") for x in deque(f, maxlen=n)]
    except OSError:
        return []


# --- the running copy ------------------------------------------------------------------------------------

class Run:
    """The one copy running now: its state for the page, its controls and its log."""

    def __init__(self, name: str, url: str, opts: dict, folder: Path, target: Path, prior_seconds: float):
        self.name, self.url, self.opts = name, url, opts
        self.folder, self.target = folder, target  # the copy, and where this run writes (<name>.new for Update)
        self.state = "starting"  # signin, copying, paused, stopping, done, stopped, signin-expired, failed
        self.error = ""
        self.crawler: Crawler | None = None
        self.started, self.finished, self.prior = time.time(), None, prior_seconds
        self.recent: deque = deque(maxlen=8)
        self.signin_done = threading.Event()
        self.signin_failures = 0

    @property
    def elapsed(self) -> float:
        return self.prior + (self.finished or time.time()) - self.started

    def log(self, line: str) -> None:
        try:
            with open(self.target / ".site2md.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')}  {line}\n")
        except OSError:
            pass

    def on_page(self, url: str, outcome: str) -> None:
        if outcome.startswith("-> ") and outcome != "-> (redirected)":
            self.recent.appendleft({"url": url, "file": outcome[3:].split(" (")[0].split(" + ")[0]})
        # pages coming back as sign-in pages: the sign-in expired. Stop instead of recording hundreds of failures
        self.signin_failures = self.signin_failures + 1 if "sign-in page" in outcome else 0
        if self.signin_failures >= 3 and self.crawler and self.state == "copying":
            self.state = "signin-expired"
            self.crawler.stopped.set()


def _queued(c: Crawler) -> list[str]:
    q = getattr(c, "queue", None)
    for _ in range(3):  # the crawl thread changes it while we read
        try:
            return list(q) if q is not None else []
        except RuntimeError:
            time.sleep(0.01)
    return []


class App:
    def __init__(self, copies: Path, on_copies=None):
        self.copies = copies
        self.copies.mkdir(parents=True, exist_ok=True)
        self.on_copies = on_copies  # called with the new folder when the person changes it
        self.on_done = None  # called with (name, folder, state) when a copy stops (Knowledge Search syncs it)
        self.run: Run | None = None
        self.sends: dict[str, dict] = {}
        self.lock = threading.Lock()

    def folder(self, name: str) -> Path:
        if not NAME.match(name or "") or ".." in name:
            raise ValueError("a name uses letters, digits, spaces, dots, dashes and underscores")
        return self.copies / name

    def busy(self) -> bool:
        return bool(self.run and self.run.state in ACTIVE)

    # -- copying
    def start(self, body: dict) -> dict:
        with self.lock:
            if self.busy():
                raise ValueError("a copy is already running; stop it first")
            url = str(body.get("url") or "").strip()
            name = str(body.get("name") or "").strip() or name_for(url)
            folder = self.folder(name)
            mode = body.get("mode", "new")  # new | continue | update
            if mode not in ("new", "continue", "update"):
                raise ValueError("mode is new, continue or update")
            if mode == "new" and (folder / "manifest.json").exists():
                raise ValueError(f"a copy named {name} exists: choose another name, or use Update on it")
            new = folder.with_name(folder.name + ".new")
            # update: a fresh copy beside the old one, which it replaces when finished; continue: what was stopped
            target = new if mode == "update" or (mode == "continue" and new.is_dir()) else folder
            old = _options(target) if mode == "continue" else _options(folder)
            url = url or old.get("url", "")
            if not re.match(r"^https?://", url):
                raise ValueError("give the address of the site, starting with https://")
            if mode == "update":
                shutil.rmtree(new, ignore_errors=True)
            opts = {"follow": "linked", **old.get("options", {}), **(body.get("options") or {})}
            target.mkdir(parents=True, exist_ok=True)
            if mode != "continue":
                (target / ".site2md.log").unlink(missing_ok=True)
            prior = old.get("seconds", 0) if mode == "continue" else 0
            _save_options(target, {"url": normalize(url), "options": opts, "seconds": prior})
            self.run = Run(name, normalize(url), opts, folder, target, prior)
            threading.Thread(target=self._crawl, args=(self.run, mode, bool(body.get("signin"))),
                             daemon=True, name="site2md-copy").start()
            return {"name": name, "state": self.run.state}

    def _crawl(self, run: Run, mode: str, signin: bool) -> None:
        o, folder, target = run.opts, run.folder, run.target
        fetcher = None
        run.log(f"{'continuing' if mode == 'continue' else 'updating' if mode == 'update' else 'copying'} {run.url}")
        try:
            channel = o.get("channel") or ""
            if signin or o.get("browser") or channel:  # a browser profile keeps the sign-in between runs
                profile = Path.home() / ".site2md" / f"profile-{channel or 'chromium'}"
                profile.mkdir(parents=True, exist_ok=True)
                if signin:
                    run.state = "signin"
                    run.log("waiting for you to sign in, in the browser window")
                    login = BrowserFetcher(profile, channel or "chromium", headless=False)
                    login.login(run.url, done=lambda where: run.signin_done.is_set() or (
                        where.startswith(run.url.rsplit("/", 1)[0]) and not looks_like_signin(where, run.url)))
                    login.close()
                    if run.state != "signin":  # stopped while signing in
                        return
                fetcher = BrowserFetcher(profile, channel or "chromium", headless=True)
            prefixes = list(dict.fromkeys([start_folder(run.url), *(p for p in o.get("prefixes", []) if p)]))
            c = Crawler(run.url, target, prefixes=prefixes, allow_hosts=[h for h in o.get("hosts", []) if h] or None,
                        max_pages=int(o.get("max_pages") or 10_000), robots=not o.get("ignore_robots"),
                        files=o.get("attachments", True), browser=fetcher, log=run.log, on_page=run.on_page,
                        save_html=Path(f"{target}-html") if o.get("keep_html") else None,
                        follow=o.get("follow") or "linked")
            run.crawler, run.state = c, "copying"
            c.run(resume=mode == "continue")
            if run.state == "signin-expired":
                pass
            elif c.stopped.is_set():
                run.state = "stopped"
            else:
                run.state = "done"
        except SystemExit as e:  # Playwright missing
            run.state, run.error = "failed", str(e)
        except Exception as e:  # noqa: BLE001 - shown on the page
            run.state, run.error = "failed", f"{type(e).__name__}: {e}"[:400]
        finally:
            if fetcher:
                try:
                    fetcher.close()
                except Exception:  # noqa: BLE001, S110 - closing a browser that already went away
                    pass
            run.finished = time.time()
            if run.error:
                run.log(f"stopped with an error: {run.error}")
            _save_options(target, {**_options(target), "seconds": round(run.elapsed),
                                   "last": {"state": run.state, "error": run.error, "finished": run.finished}})
            if run.state == "done" and target != folder:  # an update finished: it replaces the copy (and its HTML)
                for a, b in ((target, folder), (Path(f"{target}-html"), Path(f"{folder}-html"))):
                    if a.is_dir():
                        shutil.rmtree(b, ignore_errors=True)
                        a.rename(b)
                run.target = folder
            if self.on_done:
                try:
                    self.on_done(run.name, folder, run.state)
                except Exception:  # noqa: BLE001, S110 - the host's follow-up must not break the copy
                    pass

    def control(self, action: str) -> dict:
        r = self.run
        if not r:
            raise ValueError("nothing is running")
        if action == "continue-signin":
            r.signin_done.set()
        elif action == "pause" and r.crawler and r.state == "copying":
            r.crawler.paused.set()
            r.state = "paused"
        elif action == "resume" and r.crawler and r.state == "paused":
            r.crawler.paused.clear()
            r.state = "copying"
        elif action == "stop":
            if r.state == "signin":
                r.state = "stopped"
                r.signin_done.set()
            elif r.crawler:
                r.crawler.paused.clear()
                r.crawler.stopped.set()
                r.state = "stopping"
        return {"name": r.name, "state": r.state}

    def _requeue(self, name: str, urls: list[str], options: dict | None = None, signin: bool = False) -> dict:
        folder = self.folder(name)
        m = _manifest(folder)
        for u in urls:
            for k in ("failed", "skipped", "outside"):
                m.get(k, {}).pop(u, None)
        m["queue"] = list(dict.fromkeys(urls + m.get("queue", [])))
        (folder / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
        return self.start({"name": name, "mode": "continue", "signin": signin, "options": options})

    def try_again(self, name: str, reason_words: str) -> dict:
        """Copy the pages of one 'not copied' group again (continues the copy)."""
        m = _manifest(self.folder(name))
        again = [u for k in ("failed", "skipped") for u, note in m.get(k, {}).items() if reason(note) == reason_words]
        return self._requeue(name, again, signin=reason_words == "needed sign-in")

    def also_copy(self, name: str, prefix: str) -> dict:
        """Bring the links under ``prefix`` (another site's folder) into the copy, and continue it."""
        prefix = normalize(prefix)
        if not re.match(r"^https?://[^/]+/", prefix):
            raise ValueError("give a folder address starting with https://")
        folder = self.folder(name)
        o = _options(folder).get("options", {})
        links = [u for u in _manifest(folder).get("outside", {}) if u.startswith(prefix)] or [prefix]
        return self._requeue(name, links, {"prefixes": list(dict.fromkeys([*o.get("prefixes", []), prefix]))})

    # -- what a copy holds, running or not
    def _snapshot(self, name: str) -> tuple[dict, list[str], Run | None, Path]:
        folder = self.folder(name)
        run = self.run if self.run and self.run.name == name else None
        if run and run.state in ACTIVE and run.crawler:
            c = run.crawler
            with c.lock:
                m = {k: dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v
                     for k, v in c.manifest.items()}
            return m, _queued(c), run, run.target
        where = run.target if run else folder
        m = _manifest(where)
        return m, m.get("queue", []), run, where

    def detail(self, name: str) -> dict:
        m, queue, run, where = self._snapshot(name)
        o = _options(where)
        live = bool(run and run.state in ACTIVE)
        state = run.state if run else copy_state(m, o)
        elapsed = round(run.elapsed if run else o.get("seconds", 0))
        pages = len(m.get("pages", {}))
        return {
            "name": name, "url": (run.url if run else "") or m.get("start") or o.get("url", ""), "state": state,
            "error": run.error if run else o.get("last", {}).get("error", ""), "live": live,
            "counts": {"saved": pages, "files": len(m.get("files", {})), "waiting": len(queue),
                       "skipped": len(m.get("skipped", {})), "failed": len(m.get("failed", {})),
                       "outside": len(m.get("outside", {}))},
            "running": run.crawler.progress["running"] if live and run.crawler else 0,
            "elapsed": elapsed, "per_minute": round(pages / elapsed * 60, 1) if elapsed > 5 and pages else None,
            "sections": sections(m, queue), "groups": not_copied(m), "outside": outside_groups(m),
            "linked": m.get("linked", []), "recent": list(run.recent) if live else [],
            "options": o.get("options", {}), "updating": where.name.endswith(".new"),
            "folder": str(self.folder(name).resolve()),
        }

    def pages(self, name: str, kind: str = "all", q: str = "", offset: int = 0) -> dict:
        m, queue, _, _ = self._snapshot(name)
        rows: list[dict] = []
        if kind in ("all", "saved"):
            rows += [{"url": u, "kind": "saved", "file": v["file"], "note": v.get("title", "")}
                     for u, v in m.get("pages", {}).items()]
        if kind in ("all", "files"):
            rows += [{"url": u, "kind": "file", "file": f, "note": ""} for u, f in m.get("files", {}).items()]
        for k in ("failed", "skipped"):
            if kind in ("all", k):
                rows += [{"url": u, "kind": k, "note": note, "reason": reason(note)} for u, note in m.get(k, {}).items()]
        if kind in ("all", "waiting"):
            rows += [{"url": u, "kind": "waiting", "note": ""} for u in queue]
        if kind == "outside":
            rows += [{"url": u, "kind": "outside", "note": f"linked from {page}"} for u, page in m.get("outside", {}).items()]
        if q := q.strip().lower():
            rows = [r for r in rows if q in r["url"].lower() or q in r.get("file", "").lower() or q in r["note"].lower()]
        return {"total": len(rows), "rows": rows[offset:offset + 200]}

    def log(self, name: str) -> list[str]:
        _, _, _, where = self._snapshot(name)
        return _tail(where / ".site2md.log")

    # -- copies
    def copies_view(self) -> list[dict]:
        rows = [copy_view(d) for d in sorted(self.copies.iterdir(), key=lambda d: d.name.lower())
                if d.is_dir() and not d.name.endswith((".new", "-html", ".new-html")) and not d.name.startswith((".", "_"))
                and ((d / "manifest.json").exists() or (d / ".site2md.json").exists())]
        return sorted(rows, key=lambda r: -(r["updated"] or 0))

    def delete(self, name: str) -> None:
        folder = self.folder(name)
        if self.busy() and self.run.name == name:
            raise ValueError("stop the copy first")
        for d in (folder, folder.with_name(name + ".new"), Path(f"{folder}-html"), Path(f"{folder}.new-html")):
            shutil.rmtree(d, ignore_errors=True)

    # -- the page's API, shared by the stand-alone server and Knowledge Search's Crawler tab
    def handle(self, method: str, path: str, q: dict, body: dict) -> tuple:
        """(status, body[, content type]) for GET/POST <path> (without /api/)."""
        try:
            if method == "GET":
                if path == "copies":
                    r = self.run
                    return 200, {"copies": self.copies_view(), "folder": str(self.copies),
                                 "run": {"name": r.name, "state": r.state} if r else None}
                if path == "copy":
                    return 200, self.detail(q["name"])
                if path == "pages":
                    return 200, self.pages(q["name"], q.get("kind", "all"), q.get("q", ""), int(q.get("offset", 0)))
                if path == "log":
                    return 200, {"lines": self.log(q["name"])}
                if path == "browsers":
                    return 200, browsers()
                if path == "sent":
                    return 200, self.sends.get(q.get("name", ""))
                if path == "file":  # a saved page, to read it
                    folder = self.folder(q["name"])
                    f = (folder / q["file"]).resolve()
                    if folder.resolve() not in f.parents or not f.is_file():
                        return 404, {"error": "not found"}
                    return 200, f.read_bytes(), "text/plain; charset=utf-8"
            elif method == "POST":
                if path == "check":
                    url = str(body.get("url", ""))
                    return 200, check(url) | {"name": name_for(url)}
                if path == "start":
                    return 200, self.start(body)
                if path == "control":
                    return 200, self.control(str(body.get("action", "")))
                if path == "try-again":
                    return 200, self.try_again(str(body["name"]), str(body["reason"]))
                if path == "also-copy":
                    return 200, self.also_copy(str(body["name"]), str(body["prefix"]))
                if path == "open":
                    open_folder(self.folder(str(body["name"])) if body.get("name") else self.copies)
                    return 200, {"ok": True}
                if path == "delete":
                    self.delete(str(body["name"]))
                    return 200, {"ok": True}
                if path == "folder":
                    return 200, {"folder": str(self.set_copies(str(body.get("path", ""))))}
                if path == "ks/collections":
                    return 200, self.ks_collections(body)
                if path == "send":
                    return 200, self.send(body)
        except (ValueError, KeyError, TypeError) as e:
            return 400, {"error": str(e)}
        return 404, {"error": "not found"}

    def set_copies(self, path: str) -> Path:
        """Keep copies in another folder from now on (existing copies stay where they are)."""
        if self.busy():
            raise ValueError("wait until the copy running now has finished")
        folder = Path(path.strip()).expanduser()
        if not folder.is_absolute():
            raise ValueError("give the full path of a folder, e.g. C:\\Users\\me\\Documents\\Websites")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / ".site2md-write-test").write_text("")
            (folder / ".site2md-write-test").unlink()
        except OSError as e:
            raise ValueError(f"cannot save copies there: {e.strerror or e}") from None
        self.copies = folder
        if self.on_copies:
            self.on_copies(folder)
        return folder

    # -- Knowledge Search
    def ks_collections(self, body: dict) -> dict:
        me = _ks(body, "GET", "/api/me")
        return {"is_admin": me.get("is_admin", False), "user": me.get("username"),
                "collections": [{"id": c["id"], "name": c["name"]} for c in me["collections"] if c["can_write"]]}

    def send(self, body: dict) -> dict:
        name = str(body.get("name", ""))
        folder = self.folder(name)
        if self.sends.get(name, {}).get("state") == "sending":
            raise ValueError("already sending")
        new_name = str(body.get("new_collection") or "").strip().lower()
        if new_name and not re.match(r"^[a-z0-9_-]{2,64}$", new_name):
            raise ValueError("a collection name uses 2–64 lower-case letters, digits, - or _")
        st = self.sends[name] = {"state": "sending", "done": 0, "total": 0, "queued": 0, "unchanged": 0,
                                 "rejected": [], "how": "", "collection": new_name}

        def run():
            try:
                server = str(body.get("server", "")).rstrip("/")
                me = _ks(body, "GET", "/api/me")
                if new_name:
                    if not me.get("is_admin"):
                        raise ValueError("only a Knowledge Search administrator can create a collection")
                    body["collection_id"] = _ks(body, "POST", "/api/admin/collections", {"name": new_name})["id"]
                local = urlsplit(server).hostname in ("127.0.0.1", "localhost", "::1")
                if local and me.get("is_admin"):  # same computer: a synced folder (later updates remove old pages)
                    _ks(body, "POST", "/api/admin/folders", {"path": str(folder.resolve()),
                                                             "collection_id": int(body["collection_id"])})
                    st.update(state="done", how="synced folder")
                    return
                files = [f for f in sorted(folder.rglob("*")) if f.is_file() and f.suffix.lower() in KEEP
                         and not any(x.startswith((".", "_")) for x in f.relative_to(folder).parts)]
                st["total"], st["how"] = len(files), "upload"
                for f in files:
                    try:
                        out = _ks_upload(body, f, f"{name}/{f.relative_to(folder).as_posix()}")
                        st[out["outcome"]] = st.get(out["outcome"], 0) + 1
                    except ValueError as e:
                        st["rejected"].append(f"{f.name}: {e}")
                    st["done"] += 1
                st["state"] = "done"
            except Exception as e:  # noqa: BLE001 - shown on the page
                st.update(state="failed", error=str(e)[:300])

        threading.Thread(target=run, daemon=True, name="site2md-send").start()
        return st


def _ks(body: dict, method: str, path: str, data: dict | None = None) -> dict:
    server, token = str(body.get("server", "")).rstrip("/"), str(body.get("token", "")).strip()
    if not re.match(r"^https?://", server) or not token:
        raise ValueError("give the Knowledge Search address and an access token")
    req = urllib.request.Request(server + path, method=method, data=json.dumps(data).encode() if data else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("Knowledge Search did not accept the token (expired or revoked?)") from None
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ValueError(f"Knowledge Search refused it (HTTP {e.code}): {detail}") from None
    except urllib.error.URLError as e:
        raise ValueError(f"cannot reach Knowledge Search at {server}: {e.reason}") from None


def _ks_upload(body: dict, f: Path, path: str) -> dict:
    server, token = str(body["server"]).rstrip("/"), str(body["token"]).strip()
    b = uuid.uuid4().hex
    parts = b"".join([
        f'--{b}\r\nContent-Disposition: form-data; name="collection_id"\r\n\r\n{int(body["collection_id"])}\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="path"\r\n\r\n{path}\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{f.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode(), f.read_bytes(), f"\r\n--{b}--\r\n".encode()])
    req = urllib.request.Request(server + "/api/documents", data=parts, method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ValueError(e.read().decode("utf-8", "replace")[:200]) from None


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


# --- HTTP ------------------------------------------------------------------------------------------------

def make_handler(app: App, key: str, port_holder: list):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet console
            pass

        def _ok_origin(self) -> bool:
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{port_holder[0]}", f"localhost:{port_holder[0]}")

        def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; "
                             "img-src 'self' data:; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _key_ok(self, q: dict) -> bool:
            return secrets.compare_digest(self.headers.get("X-Key", "") or (q.get("k") or [""])[0], key)

        def do_GET(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            if not self._ok_origin():
                return self._send(403, {"error": "wrong address"})
            if u.path in ("/", "/index.html"):
                if not self._key_ok(q):
                    return self._send(403, b"Open site2md from its launcher (Crawl Website).", "text/plain; charset=utf-8")
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path in ("/ui.css", "/ui.js"):
                ctype = "text/css" if u.path.endswith(".css") else "text/javascript"
                return self._send(200, (STATIC / u.path[1:]).read_bytes(), ctype + "; charset=utf-8")
            if not self._key_ok(q):
                return self._send(403, {"error": "missing key"})
            self._send(*app.handle("GET", u.path.removeprefix("/api/"), {k: v[0] for k, v in q.items()}, {}))

        def do_POST(self):
            u = urlsplit(self.path)
            if not self._ok_origin() or not self._key_ok({}):
                return self._send(403, {"error": "missing key"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0) or b"{}")
            except ValueError:
                return self._send(400, {"error": "not JSON"})
            self._send(*app.handle("POST", u.path.removeprefix("/api/"), {}, body))

    return Handler


CONFIG = Path.home() / ".site2md" / "config.json"  # the copies folder the person chose


def _remember(folder: Path) -> None:
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({"copies": str(folder)}), encoding="utf-8")
    except OSError:
        pass


def serve(copies: Path, open_browser: bool = True, port: int = 0, remember: bool = False) -> tuple[ThreadingHTTPServer, str]:
    key = secrets.token_urlsafe(24)
    holder = [port]
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(App(copies, _remember if remember else None), key, holder))
    holder[0] = srv.server_address[1]
    url = f"http://127.0.0.1:{holder[0]}/?k={key}"
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    return srv, url


def main(copies: str = "", open_browser: bool = True) -> None:
    """``copies`` is the default folder (the launchers pass ``output`` next to them); a folder the person chose
    on the page wins."""
    try:
        chosen = json.loads(CONFIG.read_text(encoding="utf-8")).get("copies", "")
    except (OSError, ValueError):
        chosen = ""
    folder = Path(chosen or copies or Path.home() / "site2md").expanduser()
    srv, url = serve(folder, open_browser, remember=True)
    print(("site2md is open in your browser. If it did not open, go to:" if open_browser else "site2md is at:")
          + f"\n  {url}\nKeep this window open while you copy; close it to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
