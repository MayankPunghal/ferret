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

from .crawl import BrowserFetcher, Crawler, normalize

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


# --- reading a copy ---------------------------------------------------------------------------------------

def _manifest(folder: Path) -> dict:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _options(folder: Path) -> dict:
    try:
        return json.loads((folder / ".site2md.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


REASONS = [  # (pattern in the crawler's note, plain words), first match wins
    (r"sign-in", "needed sign-in"), (r"robots\.txt", "the site asks robots not to copy it"),
    (r"HTTP (404|410)", "page not found"), (r"HTTP \d+", "the server refused it"),
    (r"no text of its own|no text", "no text on the page"), (r"same content as", "same as another page"),
    (r"not a page|not a PDF|video|spreadsheet|larger than", "video, spreadsheet or file not kept"),
    (r"out of scope|outside", "leads outside the copied site"), (r".", "other problems"),
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
    return sorted(groups.values(), key=lambda g: -g["count"])


def copy_view(folder: Path) -> dict:
    m, o = _manifest(folder), _options(folder)
    pages, files, failed, left = (len(m.get("pages", {})), len(m.get("files", {})), m.get("failed", {}),
                                  len(m.get("queue", [])))
    signin = sum("sign-in" in v for v in failed.values())
    if left:
        status = f"stopped at {pages + files:,} of about {pages + files + left:,}"
    elif signin:
        status = f"{signin:,} page{'s' if signin != 1 else ''} need sign-in"
    elif failed:
        status = f"complete · {len(failed):,} could not be copied"
    else:
        status = "complete" if m else "not started"
    try:
        updated = (folder / "manifest.json").stat().st_mtime
    except OSError:
        updated = None
    return {"name": folder.name, "url": m.get("start") or o.get("url", ""), "pages": pages, "files": files,
            "failed": len(failed), "left": left, "status": status, "updated": updated, "options": o,
            "updating": folder.with_name(folder.name + ".new").is_dir(), "folder": str(folder)}


# --- the running copy ------------------------------------------------------------------------------------

class Run:
    """The one copy running now: its state for the page, and its controls."""

    def __init__(self, name: str, url: str, opts: dict):
        self.name, self.url, self.opts = name, url, opts
        self.state = "starting"  # signin, copying, paused, stopping, done, stopped, signin-expired, failed
        self.error = ""
        self.crawler: Crawler | None = None
        self.started, self.finished = time.time(), None
        self.recent: deque = deque(maxlen=8)
        self.signin_done = threading.Event()
        self.signin_failures = 0
        self.folder: Path | None = None
        self.resumed = False

    def on_page(self, url: str, outcome: str) -> None:
        if outcome.startswith("-> ") and outcome != "-> (redirected)":
            self.recent.appendleft({"url": url, "file": outcome[3:].split(" (")[0].split(" + ")[0]})
        # pages coming back as sign-in pages: the sign-in expired. Stop instead of recording hundreds of failures
        self.signin_failures = self.signin_failures + 1 if "sign-in page" in outcome else 0
        if self.signin_failures >= 3 and self.crawler and self.state == "copying":
            self.state = "signin-expired"
            self.crawler.stopped.set()

    def view(self) -> dict:
        c = self.crawler
        m = c.manifest if c else {}
        with (c.lock if c else threading.Lock()):
            pages, files = len(m.get("pages", {})), len(m.get("files", {}))
            failed, skipped = len(m.get("failed", {})), len(m.get("skipped", {}))
            sections = self._sections(c) if c else []
            groups = not_copied(m) if self.state in ("done", "stopped", "signin-expired") else []
        p = c.progress if c else {"queued": 0, "running": 0}
        elapsed = (self.finished or time.time()) - self.started
        return {"name": self.name, "url": self.url, "state": self.state, "error": self.error,
                "pages": pages, "files": files, "failed": failed, "skipped": skipped, "queued": p["queued"],
                "running": p["running"], "elapsed": round(elapsed), "per_minute": round(pages / elapsed * 60, 1)
                if elapsed > 5 else None, "sections": sections, "recent": list(self.recent), "groups": groups,
                "folder": str(self.folder) if self.folder else "", "resumed": self.resumed}

    def _sections(self, c: Crawler) -> list[dict]:
        """The site's sections (first folder of the address under the start folder): saved, found, gaps."""
        root = c.root

        def section(url: str) -> str:
            path = urlsplit(url).path
            rest = path.removeprefix(root)
            parts = [x for x in rest.split("/") if x]
            return parts[0] if len(parts) > 1 else "(top level)"

        out: dict[str, dict] = {}
        for kind, urls in (("saved", list(c.manifest["pages"]) + list(c.manifest["files"])),
                           ("failed", list(c.manifest["failed"])), ("skipped", list(c.manifest["skipped"])),
                           ("waiting", _queued(c))):
            for u in urls:
                s = out.setdefault(section(u), {"name": section(u), "saved": 0, "failed": 0, "skipped": 0, "waiting": 0})
                s[kind] += 1
        rows = sorted(out.values(), key=lambda s: (s["name"] != "(top level)", -(s["saved"] + s["waiting"]), s["name"]))
        return rows[:40] + ([{"name": f"{len(rows) - 40} more sections", "more": True}] if len(rows) > 40 else [])


def _queued(c: Crawler) -> list[str]:
    q = getattr(c, "queue", None)
    for _ in range(3):  # the crawl thread changes it while we read
        try:
            return list(q) if q is not None else []
        except RuntimeError:
            time.sleep(0.01)
    return []


class App:
    def __init__(self, copies: Path):
        self.copies = copies
        self.copies.mkdir(parents=True, exist_ok=True)
        self.run: Run | None = None
        self.sends: dict[str, dict] = {}
        self.lock = threading.Lock()

    def folder(self, name: str) -> Path:
        if not NAME.match(name or "") or ".." in name:
            raise ValueError("a name uses letters, digits, spaces, dots, dashes and underscores")
        return self.copies / name

    # -- copying
    def start(self, body: dict) -> dict:
        with self.lock:
            if self.run and self.run.state in ("starting", "signin", "copying", "paused", "stopping"):
                raise ValueError("a copy is already running; stop it first")
            url = str(body.get("url") or "").strip()
            name = str(body.get("name") or "").strip() or name_for(url)
            folder = self.folder(name)
            old = _options(folder)
            if not url:
                url = old.get("url", "")
            if not re.match(r"^https?://", url):
                raise ValueError("give the address of the site, starting with https://")
            mode = body.get("mode", "new")  # new | continue | update
            if mode == "new" and (folder / "manifest.json").exists():
                raise ValueError(f"a copy named {name} exists: choose another name, or use Update on it")
            if mode not in ("new", "continue", "update"):
                raise ValueError("mode is new, continue or update")
            opts = {**old.get("options", {}), **(body.get("options") or {})}
            self.run = Run(name, normalize(url), opts)
            self.run.resumed = mode == "continue"  # the clock covers this run only
            threading.Thread(target=self._crawl, args=(self.run, folder, mode, bool(body.get("signin"))),
                             daemon=True, name="site2md-copy").start()
            return self.run.view()

    def _crawl(self, run: Run, folder: Path, mode: str, signin: bool) -> None:
        o = run.opts
        new = folder.with_name(folder.name + ".new")
        # update: a fresh copy beside the old one, which it replaces when finished; continue: what was stopped
        target = new if mode == "update" or (mode == "continue" and new.is_dir()) else folder
        if mode == "update":
            shutil.rmtree(new, ignore_errors=True)
        resume = mode == "continue"
        run.folder = target
        target.mkdir(parents=True, exist_ok=True)
        (target / ".site2md.json").write_text(json.dumps({"url": run.url, "options": o}, indent=1), encoding="utf-8")
        fetcher = None
        try:
            channel = o.get("channel") or ""
            use_browser = bool(signin or o.get("browser"))
            if use_browser:
                profile = Path.home() / ".site2md" / f"profile-{channel or 'chromium'}"
                profile.mkdir(parents=True, exist_ok=True)
                if signin:
                    run.state = "signin"
                    login = BrowserFetcher(profile, channel or "chromium", headless=False)
                    login.login(run.url, done=lambda where: run.signin_done.is_set() or (
                        where.startswith(run.url.rsplit("/", 1)[0]) and not looks_like_signin(where, run.url)))
                    login.close()
                    if run.state != "signin":  # stopped while signing in
                        return
                fetcher = BrowserFetcher(profile, channel or "chromium", headless=True)
            prefixes = [p for p in o.get("prefixes", []) if p] or None
            c = Crawler(run.url, target, prefixes=prefixes, allow_hosts=[h for h in o.get("hosts", []) if h] or None,
                        max_pages=int(o.get("max_pages") or 10_000), robots=not o.get("ignore_robots"),
                        files=o.get("attachments", True), browser=fetcher, log=lambda *_: None, on_page=run.on_page,
                        save_html=Path(str(target) + "-html") if o.get("keep_html") else None)
            run.crawler, run.state = c, "copying"
            c.run(resume=resume)
            if run.state == "signin-expired":
                pass
            elif c.stopped.is_set():
                run.state = "stopped"
            else:
                run.state = "done"
                if target != folder:  # an update finished: it replaces the copy (and its HTML, if kept)
                    for a, b in ((target, folder), (Path(f"{target}-html"), Path(f"{folder}-html"))):
                        if a.is_dir():
                            shutil.rmtree(b, ignore_errors=True)
                            a.rename(b)
                    run.folder = folder
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
        return r.view()

    def try_again(self, name: str, reason_words: str) -> dict:
        """Copy the pages of one 'not copied' group again (continues the copy)."""
        folder = self.folder(name)
        m = _manifest(folder)
        again = [u for u, note in m.get("failed", {}).items() if reason(note) == reason_words]
        again += [u for u, note in m.get("skipped", {}).items() if reason(note) == reason_words]
        for u in again:
            m.get("failed", {}).pop(u, None)
            m.get("skipped", {}).pop(u, None)
        m["queue"] = list(dict.fromkeys(again + m.get("queue", [])))
        (folder / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
        return self.start({"name": name, "mode": "continue", "signin": reason_words == "needed sign-in"})

    # -- copies
    def copies_view(self) -> list[dict]:
        rows = [copy_view(d) for d in sorted(self.copies.iterdir(), key=lambda d: d.name.lower())
                if d.is_dir() and not d.name.endswith((".new", "-html", ".new-html")) and not d.name.startswith((".", "_"))
                and ((d / "manifest.json").exists() or (d / ".site2md.json").exists())]
        return sorted(rows, key=lambda r: -(r["updated"] or 0))

    def delete(self, name: str) -> None:
        folder = self.folder(name)
        if self.run and self.run.name == name and self.run.state in ("starting", "signin", "copying", "paused", "stopping"):
            raise ValueError("stop the copy first")
        for d in (folder, folder.with_name(name + ".new"), Path(f"{folder}-html"), Path(f"{folder}.new-html")):
            shutil.rmtree(d, ignore_errors=True)

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
        st = self.sends[name] = {"state": "sending", "done": 0, "total": 0, "queued": 0, "unchanged": 0,
                                 "rejected": [], "how": ""}

        def run():
            try:
                server = str(body.get("server", "")).rstrip("/")
                me = _ks(body, "GET", "/api/me")
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
            try:
                if u.path == "/api/copies":
                    return self._send(200, {"copies": app.copies_view(), "folder": str(app.copies),
                                            "run": app.run.view() if app.run else None})
                if u.path == "/api/run":
                    return self._send(200, app.run.view() if app.run else None)
                if u.path == "/api/browsers":
                    return self._send(200, browsers())
                if u.path == "/api/sent":
                    return self._send(200, app.sends.get(q.get("name", [""])[0]))
                if u.path == "/file":  # a saved page, to read it
                    folder = app.folder(q["name"][0])
                    f = (folder / q["file"][0]).resolve()
                    if folder.resolve() not in f.parents or not f.is_file():
                        return self._send(404, {"error": "not found"})
                    return self._send(200, f.read_bytes(), "text/plain; charset=utf-8")
            except (ValueError, KeyError) as e:
                return self._send(400, {"error": str(e)})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlsplit(self.path)
            if not self._ok_origin() or not self._key_ok({}):
                return self._send(403, {"error": "missing key"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0) or b"{}")
                if u.path == "/api/check":
                    return self._send(200, check(str(body.get("url", ""))) | {"name": name_for(str(body.get("url", "")))})
                if u.path == "/api/start":
                    return self._send(200, app.start(body))
                if u.path == "/api/control":
                    return self._send(200, app.control(str(body.get("action", ""))))
                if u.path == "/api/try-again":
                    return self._send(200, app.try_again(str(body["name"]), str(body["reason"])))
                if u.path == "/api/open":
                    open_folder(app.folder(str(body["name"])) if body.get("name") else app.copies)
                    return self._send(200, {"ok": True})
                if u.path == "/api/delete":
                    app.delete(str(body["name"]))
                    return self._send(200, {"ok": True})
                if u.path == "/api/ks/collections":
                    return self._send(200, app.ks_collections(body))
                if u.path == "/api/send":
                    return self._send(200, app.send(body))
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            return self._send(404, {"error": "not found"})

    return Handler


def serve(copies: Path, open_browser: bool = True, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    key = secrets.token_urlsafe(24)
    holder = [port]
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(App(copies), key, holder))
    holder[0] = srv.server_address[1]
    url = f"http://127.0.0.1:{holder[0]}/?k={key}"
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    return srv, url


def main(copies: str = "", open_browser: bool = True) -> None:
    folder = Path(copies).expanduser() if copies else Path.home() / "site2md"
    srv, url = serve(folder, open_browser)
    print(("site2md is open in your browser. If it did not open, go to:" if open_browser else "site2md is at:")
          + f"\n  {url}\nKeep this window open while you copy; close it to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
