"""Website crawler: follows every link (content, nav, footer, sitemap), one Markdown file per page, links
rewritten to local files, never follows logout/robots-disallowed links, saves linked PDFs, dedupes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from site2md.crawl import Crawler, normalize

from knowledge_search.parsing import chunk_document, parse_document

NAV = ('<header><a href="/">Home</a> <a href="/about">About us</a></header>'
       '<nav><a href="/docs/a.html">Runbook A</a> <a href="/docs/b/">Service B</a> <a href="/logout">Log out</a>'
       ' <a href="/private/secret">Private</a></nav>')
FOOT = '<footer><a href="/files/manual.pdf">Manual</a> <a href="https://elsewhere.example/">Elsewhere</a></footer>'
PAGES = {
    "/": f"<html><head><title>Intranet Home | Acme Wiki</title></head><body>{NAV}<main><h1>Welcome</h1>"
         "<p>Start here.</p></main>{FOOT}</body></html>",
    "/docs/a.html": f"<html><head><title>Runbook A | Acme Wiki</title></head><body>{NAV}<main><h1>Runbook A</h1><p>Restart "
                    "the service as described in <a href='../docs/b/#restart'>Service B</a>.</p></main>"
                    f"{FOOT}</body></html>",
    "/docs/b/": f"<html><head><title>Service B | Acme Wiki</title></head><body>{NAV}<main><h1>Service B</h1>"
                "<h2 id='restart'>Restart</h2><p>Run the restart job.</p>"
                "<a href='/docs/a.html?utm_source=mail'>back to A</a></main></body></html>",
    "/about": f"<html><head><title>About</title></head><body>{NAV}<main><h1>About</h1><p>Team page.</p></main></body></html>",
    "/about/": "<html><body><main><h1>About</h1><p>Team page.</p></main></body></html>",
    "/orphan": "<html><head><title>Orphan</title></head><body><main><h1>Orphan</h1><p>Only in the sitemap. <a href='/about/'>Team</a></p></main></body></html>",
    "/private/secret": "<html><body><main><p>Disallowed.</p></main></body></html>",
    "/logout": "<html><body>bye</body></html>",
}


def serve():
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            path = self.path.split("?")[0]
            host = f"http://127.0.0.1:{self.server.server_port}"
            if path == "/robots.txt":
                body, ctype = b"User-agent: *\nDisallow: /private/\n", "text/plain"
            elif path == "/sitemap.xml":
                body, ctype = f"<urlset><url><loc>{host}/orphan</loc></url></urlset>".encode(), "application/xml"
            elif path == "/files/manual.pdf":
                body, ctype = b"%PDF-1.4 fake", "application/pdf"
            elif path in PAGES:
                body, ctype = PAGES[path].encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, hits


def test_normalize_drops_fragments_tracking_and_default_ports():
    assert normalize("HTTP://Wiki.Example:80/a/b.html?utm_source=x&b=2&a=1#top") == "http://wiki.example/a/b.html?a=1&b=2"


def test_crawl_follows_every_link_and_writes_one_markdown_per_page(tmp_path):
    srv, hits = serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        m = Crawler(base + "/", tmp_path, delay=0, log=lambda *_: None).run()
    finally:
        srv.shutdown()
    pages = {u.removeprefix(base): v["file"] for u, v in m["pages"].items()}
    # nav, header, footer, content and sitemap links are all followed; the same page under two URLs is saved once
    assert set(pages) == {"/", "/docs/a.html", "/docs/b/", "/about", "/orphan"}
    assert "/about/" in {u.removeprefix(base) for u in m["skipped"]}
    assert m["files"] == {base + "/files/manual.pdf": "files-manual-pdf.pdf"}
    assert (tmp_path / "files-manual-pdf.pdf").read_bytes().startswith(b"%PDF")
    # never: logout, robots-disallowed, other hosts
    assert "/logout" not in hits and "/private/secret" not in hits
    assert not any("elsewhere" in u for u in m["pages"])
    a = (tmp_path / pages["/docs/a.html"]).read_text()
    assert f'source_url: "{base}/docs/a.html"' in a and 'title: "Runbook A"' in a
    assert "(docs-b.md#restart)" in a  # link to a crawled page -> local file (graph edge after ingest)
    assert "Acme Wiki" not in a  # site-wide title suffix removed
    assert "Log out" not in a  # menus are not part of the page text
    chunks = chunk_document(parse_document(a.encode(), "docs-a.md"), 900, 0)
    assert [(ln.target, ln.anchor) for c in chunks for ln in c.links] == [("docs-b.md", "restart")]
    assert json.loads((tmp_path / "manifest.json").read_text())["queue"] == []


def test_crawl_resume_and_page_limit(tmp_path):
    srv, _ = serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        first = Crawler(base + "/", tmp_path, delay=0, max_pages=2, workers=1, log=lambda *_: None).run()
        assert len(first["pages"]) + len(first["files"]) == 2 and first["queue"]
        done = Crawler(base + "/", tmp_path, delay=0, log=lambda *_: None).run(resume=True)
    finally:
        srv.shutdown()
    assert len(done["pages"]) == 5 and not done["queue"]
