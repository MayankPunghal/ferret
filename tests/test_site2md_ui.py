"""site2md's page in the browser: the key and Host checks, a copy with its HTML kept apart, an update that
replaces the copy (and its HTML) when finished, and Send to Ferret uploading Markdown with path keys."""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from site2md import ui

from .test_crawl import serve as serve_site


@pytest.fixture
def page(tmp_path):
    srv, url = ui.serve(tmp_path / "copies", open_browser=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, key = url.split("/?k=")
    yield tmp_path / "copies", base, key
    srv.shutdown()


def call(base, key, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Key": key, "Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def finish(base, key, name="acme"):
    for _ in range(200):
        run = call(base, key, f"/api/copy?name={name}")
        if run["state"] in ("done", "stopped", "failed"):
            return run
        time.sleep(0.1)
    raise AssertionError(f"still {run['state']}")


def test_the_page_needs_its_key_and_its_own_address(page):
    _, base, key = page
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "wrong", "/api/copies")
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:  # a web page elsewhere pointing a DNS name at this port
        call(base, key, "/api/copies", headers={"Host": "attacker.example"})
    assert e.value.code == 403
    assert call(base, key, "/api/copies")["copies"] == []
    with pytest.raises(urllib.error.HTTPError) as e:  # the copies folder must be a full path
        call(base, key, "/api/folder", {"path": "relative/dir"})
    assert e.value.code == 400
    other = page[0].parent / "elsewhere"
    assert call(base, key, "/api/folder", {"path": str(other)})["folder"] == str(other)
    assert call(base, key, "/api/copies")["folder"] == str(other) and other.is_dir()


def test_copy_keep_html_apart_update_and_send(page):
    copies, base, key = page
    site, _ = serve_site()
    start = f"http://127.0.0.1:{site.server_port}/"
    try:
        assert call(base, key, "/api/check", {"url": start})["name"] == "127-0-0-1"
        call(base, key, "/api/start", {"url": start, "name": "acme", "options": {"keep_html": True}})
        run = finish(base, key)
        assert run["state"] == "done" and run["counts"]["saved"] >= 5, run
        assert run["counts"]["outside"] >= 1 and run["outside"]  # the footer's link to another site, listed
        log = call(base, key, "/api/log?name=acme")["lines"]
        assert any("->" in x for x in log) and any(x.endswith("left in queue -> " + str(copies / "acme")) for x in log)
        failed = call(base, key, "/api/pages?name=acme&kind=all&q=private")
        assert failed["total"] == 1 and failed["rows"][0]["reason"] == "the site asks robots not to copy it"
        md = sorted(p.name for p in (copies / "acme").rglob("*.md"))
        html = sorted(p.name for p in (copies / "acme-html").glob("*.html"))
        assert html and {h.removesuffix(".html") for h in html} <= {m.removesuffix(".md") for m in md}
        assert not list((copies / "acme").rglob("*.html"))  # the HTML is never inside the copy that gets searched
        assert [c["name"] for c in call(base, key, "/api/copies")["copies"]] == ["acme"]

        (copies / "acme-html" / "stale.html").write_text("old")
        call(base, key, "/api/start", {"url": start, "name": "acme", "mode": "update"})
        assert finish(base, key)["state"] == "done"
        assert not (copies / "acme.new").exists() and not (copies / "acme.new-html").exists()
        assert not (copies / "acme-html" / "stale.html").exists()  # the update's HTML replaced the old
        assert sorted(p.name for p in (copies / "acme-html").glob("*.html")) == html
    finally:
        site.shutdown()

    got = []

    class KS(BaseHTTPRequestHandler):  # just enough of Ferret: who am I, and an upload
        def do_GET(self):
            self._json({"username": "ann", "is_admin": False, "collections": [{"id": 3, "name": "Wiki", "can_write": True}]})

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8", "replace")
            got.append(body.split('name="path"\r\n\r\n')[1].split("\r\n")[0])
            self._json({"outcome": "queued"})

        def _json(self, d):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(d).encode())

        def log_message(self, *a):
            pass

    ks = ThreadingHTTPServer(("127.0.0.1", 0), KS)
    threading.Thread(target=ks.serve_forever, daemon=True).start()
    try:
        server = f"http://127.0.0.1:{ks.server_port}"
        assert call(base, key, "/api/ks/collections", {"server": server, "token": "t"})["collections"][0]["id"] == 3
        call(base, key, "/api/send", {"name": "acme", "server": server, "token": "t", "collection_id": 3})
        for _ in range(100):
            st = call(base, key, "/api/sent?name=acme")
            if st["state"] != "sending":
                break
            time.sleep(0.1)
        assert st["state"] == "done" and st["queued"] == len(got) == len(md) + 1, st  # the pages and the PDF
        assert all(p.startswith("acme/") for p in got) and any(p.endswith(".pdf") for p in got)
        assert not any(p.endswith((".html", ".json")) for p in got)
    finally:
        ks.shutdown()
