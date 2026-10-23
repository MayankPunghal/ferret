"""Browser mode (needs: pip install playwright, and a Chromium: playwright install chromium, or set
SITE2MD_CHROMIUM to an executable; SITE2MD_CHANNEL=firefox tests Firefox). A site behind a sign-in, with a
page built by JavaScript."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright")

from site2md.crawl import BrowserFetcher

from site2md import main

EXE = os.environ.get("SITE2MD_CHROMIUM", "")
CHANNEL = os.environ.get("SITE2MD_CHANNEL", "chromium")  # or firefox, chrome, msedge

PAGES = {
    "/site/home": "<html><head><title>Home | Intranet</title></head><body><nav><a href='/site/js'>JS page</a> "
                  "<a href='/site/policy'>Policy</a></nav><main><h1>Home</h1><p>Welcome.</p></main></body></html>",
    "/site/policy": "<html><head><title>Leave Policy | Intranet</title></head><body><main><h1>Leave Policy</h1>"
                    "<p>Twenty days of leave.</p><a href='/site/files/handbook.pdf'>Handbook</a>"
                    "<div style='height:3000px'></div><p id='lazy' style='min-height:40px'></p></main><script>new IntersectionObserver("
                    "e => { if (e.some(x => x.isIntersecting)) document.getElementById('lazy').textContent = 'Carry over at "
                    "most five days.' }).observe(document.getElementById('lazy'))</script></body></html>",
    "/site/js": "<html><head><title>Dashboard | Intranet</title></head><body><main id='m'></main><script>"
                "document.getElementById('m').innerHTML='<h1>Dashboard</h1><p>Rendered by JavaScript: on-call is Priya.</p>'"
                "</script></body></html>",
}

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/login"):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<html><body><form>Sign in</form></body></html>"); return
        if self.path.startswith("/auth"):
            self.send_response(302); self.send_header("Set-Cookie", "session=ok; Path=/; Max-Age=3600")
            self.send_header("Location", "/site/home"); self.end_headers(); return
        if "session=ok" not in (self.headers.get("Cookie") or ""):
            self.send_response(302); self.send_header("Location", "/login"); self.end_headers(); return
        if self.path == "/site/files/handbook.pdf":
            body, ct = b"%PDF-1.4 handbook", "application/pdf"
        elif self.path in PAGES:
            body, ct = PAGES[self.path].encode(), "text/html"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass



def test_sign_in_is_kept_in_the_profile_and_javascript_pages_are_rendered(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, profile = f"http://127.0.0.1:{srv.server_port}", tmp_path / "profile"
    exe = EXE if CHANNEL == "chromium" else ""
    args = ["--browser", "--channel", CHANNEL, "--profile", str(profile), *(["--browser-path", exe] if exe else [])]
    try:
        main([base + "/site/home", "--out", str(tmp_path / "before"), *args])
        assert "sign-in page" in (tmp_path / "before" / "manifest.json").read_text()
        f = BrowserFetcher(profile, CHANNEL, executable=exe)  # stands in for the person signing in (--login)
        f.page.goto(base + "/auth")
        f.close()
        out = tmp_path / "after"
        main([base + "/site/home", "--out", str(out), *args])
    finally:
        srv.shutdown()
    assert sorted(p.name for p in out.iterdir()) == ["files-handbook-pdf.pdf", "home.md", "js.md", "manifest.json",
                                                    "policy.md"]
    assert "on-call is Priya" in (out / "js.md").read_text()  # rendered by JavaScript
    policy = (out / "policy.md").read_text()
    assert 'title: "Leave Policy"' in policy and "(files-handbook-pdf.pdf)" in policy
    assert "Carry over at most five days." in policy  # drawn only when scrolled into view
