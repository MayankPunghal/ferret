"""MCP tools that change things: offered only to tokens allowed to make changes, within the user's rights;
folders and website copies (crawls.py) for administrators."""

import http.server
import json
import threading
import time
from functools import partial

from .conftest import collection_id
from .test_mcp import browser_token, call, rpc


def names(client, h):
    return {t["name"] for t in rpc(client, h, "tools/list")["result"]["tools"]}


def test_tokens_are_read_only_unless_allowed(client, corpus):
    ro = browser_token(client, "root", "read only")
    assert "add_document" not in names(client, ro) and "get_settings" not in names(client, ro)
    # the REST API refuses too, so a read-only token cannot be used around the MCP tool list
    up = client.post("/api/documents", headers=ro, data={"collection_id": collection_id("scratch")},
                     files={"file": ("x.md", b"# x")})
    assert up.status_code == 404
    assert client.get("/api/admin/settings", headers=ro).status_code == 404
    assert "Error" in str(call(client, ro, "add_document", collection="scratch", path="x.md", text="# x"))
    client.post("/api/auth/login", json={"username": "root", "password": "correct-horse-battery"})
    listed = client.get("/api/tokens", headers={"x-ks-csrf": "1"}).json()["tokens"]
    client.cookies.clear()
    assert any(t["label"] == "read only" and t["read_only"] for t in listed)


def test_writer_adds_documents_admin_manages(client, corpus, drain, tmp_path):
    alice = browser_token(client, "alice", "alice writes")
    client.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-battery"})
    r = client.post("/api/tokens", headers={"x-ks-csrf": "1"}, json={"label": "w", "days": 1, "allow_changes": True})
    client.cookies.clear()
    alice = {"Authorization": f"Bearer {r.json()['token']}"}
    have = names(client, alice)
    assert {"add_document", "retry_document", "delete_document"} <= have and "add_folder" not in have
    out = call(client, alice, "add_document", collection="engineering", path="notes/standup.md",
               text="# Standup\nThe Zorblax migration moves to Thursday.")
    assert out["outcome"] == "queued", out
    assert "Error" in str(call(client, alice, "add_document", collection="hr", path="x.md", text="# x"))  # not hers
    drain()
    docs = call(client, alice, "list_documents", collection="engineering", name_contains="standup")
    assert docs["documents"][0]["status"] == "indexed" and docs["documents"][0]["source_key"] == "notes/standup.md"
    assert call(client, alice, "search", query="Zorblax migration")["results"][0]["title"] == "Standup"
    assert call(client, alice, "delete_document", document_id=docs["documents"][0]["id"])["deleted"]

    client.post("/api/auth/login", json={"username": "root", "password": "correct-horse-battery"})
    r = client.post("/api/tokens", headers={"x-ks-csrf": "1"}, json={"label": "admin", "days": 1, "allow_changes": True})
    client.cookies.clear()
    root = {"Authorization": f"Bearer {r.json()['token']}"}
    assert {"add_folder", "crawl_website", "get_settings", "change_settings", "reindex"} <= names(client, root)
    s = call(client, root, "get_settings")
    assert "rerank_enabled" in s["values"] and s["models"]["embed_models"]
    made = rpc(client, root, "tools/call", {"name": "create_collection", "arguments": {"name": "mcp-made"}})["result"]
    assert not made["isError"], made
    (tmp_path / "kit" / "docs").mkdir(parents=True)
    (tmp_path / "kit" / "docs" / "a.md").write_text("# A\nalpha page")
    (tmp_path / "kit" / "_tools").mkdir()
    (tmp_path / "kit" / "_tools" / "t.md").write_text("# tool")
    f = call(client, root, "add_folder", path=str(tmp_path / "kit"), collection="mcp-made", types=["markdown"])
    for _ in range(100):
        f = next(x for x in call(client, root, "list_folders") if x["id"] == f["id"])
        if f["result"] and not f["syncing"]:
            break
        time.sleep(0.1)
    assert f["result"]["queued"] == 1 and f["result"]["skipped"] == {"folders starting with . or _": 1}
    assert "Error" in str(call(client, root, "add_folder", path="relative", collection="mcp-made"))


def test_crawl_website_into_a_collection(client, corpus, tmp_path):
    site = tmp_path / "site" / "wiki"
    site.mkdir(parents=True)
    page = "<html><head><title>{t}</title></head><body><main><h1>{t}</h1><p>{p}</p>{a}</main></body></html>"
    (site / "index.html").write_text(page.format(t="Wiki home", p="The on-call rota is kept in the Orbit tool. " * 3,
                                                 a='<a href="rota.html">Rota</a>'))
    (site / "rota.html").write_text(page.format(t="Rota", p="Week one is covered by the Ledger team, always. " * 3, a=""))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(http.server.SimpleHTTPRequestHandler,
                                                                     directory=str(tmp_path / "site")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client.post("/api/auth/login", json={"username": "root", "password": "correct-horse-battery"})
        r = client.post("/api/tokens", headers={"x-ks-csrf": "1"}, json={"label": "c", "days": 1, "allow_changes": True})
        client.cookies.clear()
        root = {"Authorization": f"Bearer {r.json()['token']}"}
        url = f"http://127.0.0.1:{srv.server_port}/wiki/index.html"
        job = call(client, root, "crawl_website", url=url, collection="scratch", max_pages=10)
        assert job["state"] == "starting", job
        for _ in range(300):
            job = next(j for j in call(client, root, "list_crawls") if j["name"] == job["name"])
            if job["state"] in ("done", "failed"):
                break
            time.sleep(0.2)
        assert job["state"] == "done" and job["pages"] == 2, job
        for _ in range(100):  # synced into the collection in the background
            keys = {d["source_key"] for d in call(client, root, "list_documents", collection="scratch")["documents"]}
            if len([k for k in keys if k.startswith(job["name"] + "/")]) == 2:
                break
            time.sleep(0.2)
        assert len([k for k in keys if k.startswith(job["name"] + "/")]) == 2, keys
        # again, into a collection made when the copy is done (an update: the same pages, now in "site-copy")
        assert "new_collection" in call(client, root, "crawl_website", url=url, collection="scratch",
                                        new_collection="site-copy")
        call(client, root, "crawl_website", url=url, new_collection="site-copy", max_pages=10)
        for _ in range(300):
            cols = {c["name"] for c in call(client, root, "list_collections")}
            if "site-copy" in cols and len(call(client, root, "list_documents", collection="site-copy")["documents"]) == 2:
                break
            time.sleep(0.2)
        assert len(call(client, root, "list_documents", collection="site-copy")["documents"]) == 2
        # a finished copy sent on to another new collection
        def send_crawl(**args):  # call() takes the tool's name as `name`, which this tool uses as an argument
            res = rpc(client, root, "tools/call", {"name": "send_crawl", "arguments": args})["result"]
            return res["content"][0]["text"] if res["isError"] else json.loads(res["content"][0]["text"])
        assert "exists" in send_crawl(name=job["name"], new_collection="site-copy")
        sent = send_crawl(name=job["name"], new_collection="sent-copy")
        assert sent["collection_id"], sent
        for _ in range(100):
            if len(call(client, root, "list_documents", collection="sent-copy")["documents"]) == 2:
                break
            time.sleep(0.2)
        assert len(call(client, root, "list_documents", collection="sent-copy")["documents"]) == 2
        # the Crawler tab: the same copy, through the administrator's session, without a token
        client.post("/api/auth/login", json={"username": "root", "password": "correct-horse-battery"})
        assert "site2md" in client.get("/crawler/").text
        copy = client.get(f"/api/admin/crawler/copy?name={job['name']}").json()
        assert copy["counts"]["saved"] == 2 and copy["state"] == "done"
        assert client.post("/api/admin/crawler/send", headers={"x-ks-csrf": "1"}, json={}).status_code == 404
    finally:
        srv.shutdown()
