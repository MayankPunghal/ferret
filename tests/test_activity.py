"""Activity (trace.py): recent calls with request, response and timing, for administrators only."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from knowledge_search import enrich, trace

from .conftest import PASSWORD, search, token
from .test_mcp import browser_token, call


def calls(client, h, kind=""):
    r = client.get(f"/api/admin/activity?kind={kind}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["calls"]


def test_search_is_recorded_with_its_steps_and_secrets_are_masked(client, corpus):
    root, bob = token(client, "root"), token(client, "bob")
    assert client.get("/api/admin/activity", headers=bob).status_code == 404  # administrators only
    search(client, bob, "ERR-PAY-4012")
    s = next(c for c in calls(client, root, "http") if c["title"] == "Search “ERR-PAY-4012”")
    assert s["user"] == "bob (access token)" and s["status"] == "ok" and s["ms"] > 0
    assert {"query_embedding", "lexical"} <= {n for n, _ in s["steps"]}
    full = client.get(f"/api/admin/activity/{s['id']}", headers=root).json()
    assert full["request"]["query"] == "ERR-PAY-4012" and full["response"]["results"]
    # the sign-in that made the token: the password and the session token are masked
    login = next(c for c in calls(client, root) if c["title"] == "POST /api/auth/login" and c["user"] == "bob")
    full = client.get(f"/api/admin/activity/{login['id']}", headers=root).json()
    assert full["request"]["password"] == "•••" and PASSWORD not in json.dumps(full)
    assert full["response"]["token"] == "•••"
    assert client.get("/api/admin/activity/999999999", headers=root).status_code == 404
    idx = calls(client, root, "index")  # the corpus was indexed: one call per document, with its steps
    assert idx and "reading the file" in {n for n, _ in idx[0]["steps"]} and "passages" in idx[0]["detail"]
    # routine fast GETs are not kept; the activity list itself never is
    client.get("/api/me", headers=bob)
    assert not any(c["title"] in ("GET /api/me", "GET /api/admin/activity") for c in calls(client, root))


def test_mcp_call_links_the_search_it_made(client, corpus):
    root = token(client, "root")
    h = browser_token(client, "alice", label="activity test")
    call(client, h, "search", query="ledger service schema changes", limit=3)
    rows = calls(client, root)
    m = next(c for c in rows if c["kind"] == "mcp" and c["title"] == "MCP search")
    assert m["user"] == "alice (access token)"  # taken from the calls it made
    inner = next(c for c in rows if c["parent"] == m["id"])
    assert inner["title"] == "Search “ledger service schema changes”" and inner["user"] == "alice (access token)"
    assert client.delete("/api/admin/activity", headers=root).status_code == 204
    assert calls(client, root) == []


def test_local_model_call_keeps_prompt_reply_tokens_and_timing():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}],
                               "usage": {"prompt_tokens": 120, "completion_tokens": 9},
                               "timings": {"prompt_ms": 300.0, "predicted_ms": 450.0, "predicted_per_second": 20.0}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert enrich.chat(f"http://127.0.0.1:{server.server_port}/v1", "fake", "Summarise this", label="summary of X") == '{"ok": true}'
    finally:
        server.shutdown()
    c = trace.get(trace.calls("llm")[0]["id"])
    assert c["title"] == "Local model: summary of X" and c["tokens"] == {"prompt": 120, "output": 9, "per_second": 20.0}
    assert c["steps"] == [["reading the prompt", 300.0], ["writing", 450.0]]
    assert c["request"]["messages"][0]["content"] == "Summarise this"


def test_long_bodies_are_cut():
    cid = trace.record("http", "big", ms=1, request={"text": "x" * (trace.MAX_CHARS + 50)})
    kept = trace.get(cid)["request"]
    assert isinstance(kept, str) and kept.endswith("more characters not kept)")
