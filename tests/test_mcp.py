"""MCP endpoint: protocol handshake, tools scoped to the token's user, access tokens, the stdio bridge."""

import io
import json
import sys
import threading

import pytest

from knowledge_search import mcp

from .conftest import PASSWORD, token


def rpc(client, headers, method, params=None, id_=1):
    r = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


def call(client, headers, name, **args):
    res = rpc(client, headers, "tools/call", {"name": name, "arguments": args})["result"]
    text = res["content"][0]["text"]
    return text if res["isError"] else json.loads(text)


def browser_token(client, user, label="Claude Code", days=30):
    """Tokens are created from a signed-in browser session (cookie + CSRF header), never with a bearer token."""
    client.post("/api/auth/login", json={"username": user, "password": PASSWORD})
    r = client.post("/api/tokens", headers={"x-ks-csrf": "1"}, json={"label": label, "days": days})
    client.cookies.clear()
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_handshake_and_tool_list(client, corpus):
    h = token(client, "bob")
    init = rpc(client, h, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "1"}})["result"]
    assert init["protocolVersion"] == "2025-06-18" and "tools" in init["capabilities"]
    assert "no_adequate_evidence" in init["instructions"]
    assert rpc(client, h, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == mcp.PROTOCOLS[0]
    r = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    names = {t["name"] for t in rpc(client, h, "tools/list")["result"]["tools"]}
    assert names == {"search", "read_passage", "read_document", "document_links", "list_collections",
                     "index_status", "list_documents"}  # bob cannot write: no tools that change anything
    assert rpc(client, h, "nope")["error"]["code"] == -32601
    assert client.get("/mcp", headers=h).status_code == 405
    # no token, or a wrong one: refused at connect time
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 401
    bad = client.post("/mcp", headers={"Authorization": "Bearer nope"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert bad.status_code == 401


def test_tools_return_cited_passages_within_the_users_rights(client, corpus):
    bob, hannah = token(client, "bob"), token(client, "hannah")
    assert {c["name"] for c in call(client, bob, "list_collections")} == {"engineering", "finance", "platform-wiki"}
    out = call(client, bob, "search", query="ERR-PAY-4012", limit=3)
    assert out["outcome"] == "evidence_found" and len(out["results"]) <= 3
    top = out["results"][0]
    assert top["supported"] and "ERR-PAY-4012" in top["text"] and top["open"].startswith("http")
    p = call(client, bob, "read_passage", passage_id=top["passage_id"])
    assert p["document_id"] == top["document_id"] and p["text"] == top["text"]
    d = call(client, bob, "read_document", document_id=top["document_id"], max_chars=1000)
    assert d["title"] and len(d["text"]) <= 1000
    links = call(client, bob, "document_links", document_id=top["document_id"])
    assert set(links) >= {"links_to", "linked_from", "related_in_meaning"}
    # bob cannot read HR: no results from it, and its documents are "not found" for him
    comp = call(client, hannah, "search", query="engineering compensation bands salary", collections=["hr"])
    assert comp["results"] and all(r["collection"] == "hr" for r in comp["results"])
    secret = comp["results"][0]
    assert "hr" not in {r["collection"] for r in call(client, bob, "search", query="compensation bands salary")["results"]}
    assert call(client, bob, "read_passage", passage_id=secret["passage_id"]).startswith("Error: not found")
    assert call(client, bob, "read_document", document_id=secret["document_id"]).startswith("Error: not found")
    assert "no readable collection" in call(client, bob, "search", query="salary", collections=["hr"])


def test_access_tokens_create_list_revoke(client, corpus):
    h = browser_token(client, "alice", "Cursor")
    assert call(client, h, "list_collections")
    listed = client.get("/api/tokens", headers=h).json()
    assert listed["mcp_url"].endswith("/mcp") and listed["command"]
    mine = next(t for t in listed["tokens"] if t["label"] == "Cursor")
    # an assistant holding a token cannot mint more, and nobody revokes another user's token
    assert client.post("/api/tokens", headers=h, json={"label": "more", "days": 1}).status_code == 403
    assert client.delete(f"/api/tokens/{mine['id']}", headers=token(client, "bob")).status_code == 404
    # its access changes in place: not by the assistant itself, only by its owner signed in to the app
    tools = lambda: {t["name"] for t in rpc(client, h, "tools/list", {})["result"]["tools"]}
    assert "add_document" not in tools()
    assert client.patch(f"/api/tokens/{mine['id']}", headers=h, json={"allow_changes": True}).status_code == 403
    client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert client.patch(f"/api/tokens/{mine['id']}", headers={"x-ks-csrf": "1"}, json={"allow_changes": True}).status_code == 200
    client.cookies.clear()
    assert "add_document" in tools()
    assert not next(t for t in client.get("/api/tokens", headers=h).json()["tokens"] if t["id"] == mine["id"])["read_only"]
    assert client.delete(f"/api/tokens/{mine['id']}", headers=h).status_code == 200
    assert client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 401
    assert call(client, token(client, "alice"), "list_collections")  # her other sessions are unaffected


def test_official_mcp_client(client, corpus):
    """The reference MCP SDK connects over Streamable HTTP, lists the tools and calls one."""
    anyio = pytest.importorskip("anyio")
    httpx2 = pytest.importorskip("httpx2")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    h = token(client, "bob")

    async def run():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=client.app), base_url="http://testserver",
                                      headers=h) as http:
            async with streamable_http_client("http://testserver/mcp", http_client=http, terminate_on_close=False) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    tools = await s.list_tools()
                    res = await s.call_tool("search", {"query": "ERR-PAY-4012", "limit": 2})
                    return [t.name for t in tools.tools], res

    names, res = anyio.run(run)
    assert "search" in names and not res.is_error
    assert json.loads(res.content[0].text)["outcome"] == "evidence_found"


def test_stdio_bridge(client, corpus, monkeypatch):
    """`knowledge-search mcp` relays one JSON-RPC message per line to the HTTP endpoint (Claude Desktop)."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(client.app, host="127.0.0.1", port=0, log_config=None, lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        pass
    port = server.servers[0].sockets[0].getsockname()[1]
    tok = token(client, "bob")["Authorization"][7:]
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(json.dumps(x) for x in lines) + "\n"))
    monkeypatch.setattr(sys, "stdout", out)
    mcp.bridge(f"http://127.0.0.1:{port}", tok)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2] and replies[1]["result"]["tools"]
    # wrong token: the client gets a JSON-RPC error, not a silent hang
    out.truncate(0), out.seek(0)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(lines[0]) + "\n"))
    mcp.bridge(f"http://127.0.0.1:{port}", "wrong")
    assert json.loads(out.getvalue())["error"]["code"] == -32000
    server.should_exit = True
    t.join(5)
