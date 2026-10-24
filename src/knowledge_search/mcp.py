"""MCP (Model Context Protocol) server for AI assistants: Claude Code, Claude Desktop, Cursor, VS Code.

One endpoint, ``POST /mcp`` (Streamable HTTP, JSON responses, no streaming), inside the app: no extra
process or dependency. Every tool is a read-only call to the app's own REST API, made in-process with the
caller's access token, so an assistant sees exactly what that user may read and the evidence gate,
permissions and audit log apply unchanged. Tokens are created on the app's "AI assistants" page.

Claude Desktop only starts local commands, so ``knowledge-search mcp`` relays its stdin/stdout to this
endpoint (``bridge``). Passages an assistant reads go to that assistant's provider; the page says so.
"""

import importlib.metadata
import json
import os
import sys
import urllib.error
import urllib.request

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first; the client's choice when we know it

INSTRUCTIONS = """Knowledge Search: the user's own documents (runbooks, wikis, policies, slides, PDFs).
Search first; answer only from passages whose "supported" is true and cite each one by title and its "open"
link. If the outcome is no_adequate_evidence, say the documents do not answer it instead of guessing; for
conflicting_evidence, show the versions and their dates. Summaries, topics and types are written by a local
model: use them to choose what to read, never as evidence. Read a passage or the whole document before
relying on a detail the passage only hints at; follow document_links for "what depends on X" questions."""


def _tool(name, description, properties, required=()):
    return {"name": name, "description": description, "annotations": {"readOnlyHint": True},
            "inputSchema": {"type": "object", "properties": properties, "required": list(required)}}


_ID = {"type": "string", "description": "document_id from a search result"}
TOOLS = [
    _tool("search", "Search the user's documents (keywords, identifiers like ERR-PAY-4012, or a question). "
          "Returns an outcome (evidence_found / conflicting_evidence / no_adequate_evidence) and ranked passages "
          "with their location, the local model's summary of each document and an 'open' link to cite.",
          {"query": {"type": "string"},
           "collections": {"type": "array", "items": {"type": "string"},
                           "description": "collection names to search; default all the user can read"},
           "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8}}, ["query"]),
    _tool("read_passage", "One passage with the passages around it, by passage_id from a search result.",
          {"passage_id": {"type": "integer"}}, ["passage_id"]),
    _tool("read_document", "A whole document's text in reading order (long documents are cut at max_chars).",
          {"document_id": _ID, "max_chars": {"type": "integer", "minimum": 1000, "maximum": 200000, "default": 30000}},
          ["document_id"]),
    _tool("document_links", "What a document links to, what links to it (incoming = what depends on it) and "
          "documents related in meaning: for dependency, ownership and impact questions.",
          {"document_id": _ID}, ["document_id"]),
    _tool("list_collections", "The collections the user can search.", {}),
]


class ToolError(Exception):
    pass


async def _call(c: httpx.AsyncClient, method: str, path: str, body=None):
    r = await c.request(method, path, json=body)
    if r.status_code == 401:
        raise ToolError("the access token is invalid or expired: create a new one on the app's AI assistants page")
    if r.status_code == 404:
        raise ToolError("not found, or not readable with this account")
    if r.status_code >= 400 and not (r.headers.get("content-type", "").startswith("application/json")
                                     and "outcome" in r.json()):
        raise ToolError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


def _where(r: dict) -> str:
    return " > ".join(x for x in [r.get("heading_path") or "", f"page {r['page']}" if r.get("page") else ""] if x)


async def _run(name: str, a: dict, c: httpx.AsyncClient, base: str) -> dict | list:
    link = lambda u: base + u if u and u.startswith("/") else u  # noqa: E731
    if name == "list_collections":
        return [{"name": x["name"], "description": x.get("description") or ""}
                for x in (await _call(c, "GET", "/api/me"))["collections"]]
    if name == "search":
        body = {"query": str(a.get("query", "")), "limit": max(1, min(int(a.get("limit") or 8), 20))}
        if a.get("collections"):
            cols = {x["name"].lower(): x["id"] for x in (await _call(c, "GET", "/api/me"))["collections"]}
            body["collection_ids"] = [cols[n.lower()] for n in a["collections"] if n.lower() in cols]
            if not body["collection_ids"]:
                raise ToolError(f"no readable collection named {', '.join(a['collections'])}; see list_collections")
        out = await _call(c, "POST", "/api/search", body)
        results = []
        for r in out.get("results", [])[:body["limit"]]:  # the API may add passages beyond limit to fill documents
            x = {"passage_id": r["chunk_id"], "document_id": r["document_id"], "title": r.get("clearer_title") or r["title"],
                 "collection": r["collection"], "where": _where(r), "supported": r["supported"],
                 "why": r.get("match_basis"), "text": r["text"], "open": link(r["source_url"])}
            meta = {k: r["metadata"][k] for k in ("series", "version", "effective_date") if (r.get("metadata") or {}).get(k)}
            extra = {"version": meta, "summary_by_local_model": r.get("summary"), "type": r.get("doc_type"),
                     "outdated_version": r.get("outdated_version") or None,
                     "linked_from": r["signals"].get("linked_from")}
            results.append(x | {k: v for k, v in extra.items() if v})
        return {k: out[k] for k in ("outcome", "message", "reason_codes") if out.get(k)} | {"results": results}
    if name == "read_passage":
        p = await _call(c, "GET", f"/api/chunks/{int(a['passage_id'])}")
        return {"passage_id": p["id"], "document_id": p["document_id"], "title": p.get("clearer_title") or p["doc_title"],
                "collection": p["collection"], "where": _where(p), "text": p["text"],
                "around": [{"passage_id": x["id"], "where": _where(x), "text": x["text"]} for x in p["context"] if x["id"] != p["id"]],
                "open": link(f"/api/documents/{p['document_id']}/view" + (f"#{p['anchor']}" if p.get("anchor") else ""))}
    if name == "read_document":
        d = await _call(c, "GET", f"/api/documents/{a['document_id']}/text")
        limit = max(1000, min(int(a.get("max_chars") or 30000), 200000))
        text, cut = "", False
        for p in d["passages"]:
            part = (f"\n[{_where(p)}]\n" if _where(p) else "\n") + p["text"]
            if len(text) + len(part) > limit:
                cut = True
                break
            text += part
        return {"document_id": d["document_id"], "title": d.get("clearer_title") or d["title"], "collection": d["collection"],
                "open": link(f"/api/documents/{d['document_id']}/view"), "text": text.strip(),
                **({"truncated": f"cut at {limit} characters; raise max_chars or search within it"} if cut else {})}
    if name == "document_links":
        doc = a["document_id"]
        links = await _call(c, "GET", f"/api/documents/{doc}/links")
        item = lambda x, how: {"document_id": x["document_id"], "title": x["title"], "collection": x.get("collection"),  # noqa: E731
                               "how": how, "open": link(f"/api/documents/{x['document_id']}/view")}
        return {"links_to": [item(x, x["kind"]) for x in links.get("outgoing", [])],
                "linked_from": [item(x, x["kind"]) for x in links.get("incoming", [])],
                "mentions": [item(x, x.get("how", "mentions")) for x in links.get("mentions", [])],
                "related_in_meaning": [item(x, "similar") for x in links.get("similar", [])],
                "not_found": links.get("unresolved", [])}
    raise ToolError(f"unknown tool {name}")


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


router = APIRouter(include_in_schema=False)


@router.post("/mcp")
async def mcp(request: Request):
    authz = request.headers.get("authorization", "")
    if not authz.lower().startswith("bearer "):
        return JSONResponse({"detail": "send the access token from the AI assistants page as 'Authorization: Bearer <token>'"},
                            status_code=401)
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse(_error(None, -32700, "parse error"), status_code=400)
    base = str(request.base_url).rstrip("/")
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(transport=transport, base_url=base, headers={"authorization": authz}, timeout=120) as c:
        if any(isinstance(m, dict) and m.get("method") == "initialize" for m in (msg if isinstance(msg, list) else [msg])):
            if (await c.get("/api/me")).status_code == 401:  # a wrong token fails at connect, not at the first tool call
                return JSONResponse({"detail": "invalid or expired access token"}, status_code=401)
        replies = [r for m in (msg if isinstance(msg, list) else [msg]) if (r := await _handle(m, c, base)) is not None]
    if not replies:
        return Response(status_code=202)  # notifications only
    return JSONResponse(replies if isinstance(msg, list) else replies[0])


@router.get("/mcp")
def mcp_stream():
    return Response(status_code=405, headers={"Allow": "POST"})  # no server-initiated messages


async def _handle(m, c: httpx.AsyncClient, base: str) -> dict | None:
    if not isinstance(m, dict) or m.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    if "id" not in m:
        return None  # notification (initialized, cancelled): nothing to answer
    id_, method, params = m["id"], m.get("method"), m.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        try:
            version = importlib.metadata.version("knowledge-search")
        except importlib.metadata.PackageNotFoundError:
            version = "dev"
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0], "capabilities": {"tools": {}},
            "serverInfo": {"name": "knowledge-search", "version": version}, "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            out = await _run(params.get("name", ""), params.get("arguments") or {}, c, base)
            text, failed = json.dumps(out, ensure_ascii=False, indent=1), False
        except (ToolError, KeyError, ValueError, TypeError) as e:
            text, failed = f"Error: {e}", True
        return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": text}], "isError": failed}}
    return _error(id_, -32601, f"method not found: {method}")


def bridge(url: str, token: str) -> None:
    """stdio <-> HTTP for clients that only start local commands (Claude Desktop): one JSON-RPC message per
    line in, the app's reply per line out."""
    for line in sys.stdin:
        if not line.strip():
            continue
        req = urllib.request.Request(url.rstrip("/") + "/mcp", data=line.encode(), method="POST",
                                     headers={"content-type": "application/json", "accept": "application/json, text/event-stream",
                                              "authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                msg = json.loads(line)
            except ValueError:
                msg = {}
            if "error" not in body and msg.get("id") is not None:  # make an HTTP failure a JSON-RPC error
                body = json.dumps(_error(msg["id"], -32000, f"Knowledge Search: HTTP {e.code} {body[:300]}"))
        except OSError as e:
            msg = json.loads(line) if line.strip().startswith("{") else {}
            body = json.dumps(_error(msg.get("id"), -32000, f"Knowledge Search is not reachable at {url} ({e})"))
        if body.strip():
            sys.stdout.write(body.strip() + "\n")
            sys.stdout.flush()


def bridge_main() -> None:
    token = os.environ.get("KS_TOKEN", "")
    if not token:
        sys.exit("KS_TOKEN is not set: create an access token on the app's AI assistants page")
    bridge(os.environ.get("KS_URL", "http://127.0.0.1:8000"), token)
