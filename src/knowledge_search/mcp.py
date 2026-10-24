"""MCP (Model Context Protocol) server for AI assistants: Claude Code, Claude Desktop, Cursor, VS Code.

One endpoint, ``POST /mcp`` (Streamable HTTP, JSON responses, no streaming), inside the app: no extra
process or dependency. Every tool is a call to the app's own REST API, made in-process with the caller's
access token, so an assistant sees and does exactly what that user may, and the evidence gate, permissions
and audit log apply unchanged. Searching and reading are read-only; adding documents is offered to accounts
that can write to a collection, and folders, crawls, settings and reindexing to administrators. Those tools
are marked as not read-only, so assistants ask the user before running them. Tokens are created on the app's "AI assistants" page.

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
relying on a detail the passage only hints at; follow document_links for "what depends on X" questions.
Tools that change things (add_document, add_folder, crawl_website, send_crawl, change_settings, reindex, delete_document)
appear only when the user's token allows changes. Use them when the user asks; ask first before deleting,
reindex all=true, or changing the embedding model or passage size (every document is indexed again)."""


def _tool(name, description, properties, required=(), needs="", destructive=False):
    """needs: "" (anyone), "write" (an account that can upload to a collection) or "admin". Tools that change
    something are not read-only, so assistants ask the user before running them (unless the user allowed it)."""
    hints = {"readOnlyHint": True} if not needs else {"readOnlyHint": False, "destructiveHint": destructive}
    return {"name": name, "description": description, "annotations": hints, "needs": needs,
            "inputSchema": {"type": "object", "properties": properties, "required": list(required)}}


_ID = {"type": "string", "description": "document_id from a search result"}
_COL = {"type": "string", "description": "collection name (list_collections)"}
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
    _tool("list_collections", "The collections the user can search, and whether this account can add documents to each.", {}),
    _tool("index_status", "Indexing progress: documents indexed, waiting, failed; the models in use; summaries.", {}),
    _tool("list_documents", "Documents with their status (indexed, pending, failed + error_code), optionally of one "
          "collection or status. For 'is X indexed', 'what failed'.",
          {"collection": _COL, "status": {"type": "string", "enum": ["indexed", "pending", "processing", "failed"]},
           "name_contains": {"type": "string"}}),
    # --- adding and managing documents (accounts with write access) ---
    _tool("add_document", "Add or update one document from text (Markdown), e.g. notes the user asked you to keep. "
          "The same path again replaces it with a new version; unchanged text is skipped.",
          {"collection": _COL, "path": {"type": "string", "description": "its name, e.g. notes/2026-10-24-meeting.md "
                                        "(.md or .txt); identifies the document in the collection"},
           "text": {"type": "string"}}, ["collection", "path", "text"], needs="write"),
    _tool("retry_document", "Index a failed document again.", {"document_id": _ID}, ["document_id"], needs="write"),
    _tool("delete_document", "Remove a document and all its versions from the index (not the original file). "
          "Ask the user first.", {"document_id": _ID}, ["document_id"], needs="write", destructive=True),
    # --- administration ---
    _tool("add_folder", "Index a folder on the computer running Knowledge Search, with its subfolders, and keep it "
          "synced. Leaves out folders and files whose name starts with . or _ (.git, .claude, _tools) and other "
          "file types (code, JSON, YAML, images). Runs in the background: check with list_folders. Adding the same "
          "folder again syncs it.",
          {"path": {"type": "string", "description": "full path, e.g. C:\\Users\\me\\Docs\\repo-kit"},
           "collection": _COL,
           "types": {"type": "array", "items": {"type": "string", "enum": ["markdown", "text", "html", "word",
                                                                          "powerpoint", "pdf"]},
                     "description": "file types to take; default all six"}},
          ["path", "collection"], needs="admin"),
    _tool("list_folders", "Synced folders and what their last sync did (new or changed, unchanged, removed, left out).",
          {}, needs="admin"),
    _tool("sync_folder", "Sync a folder now: index new and changed files, remove documents whose file is gone.",
          {"folder_id": {"type": "integer"}}, ["folder_id"], needs="admin"),
    _tool("crawl_website", "Copy a website or intranet into Markdown (site2md) on the computer running Knowledge "
          "Search and index it into a collection when the copy is done: an existing one (collection) or a new one "
          "created then (new_collection). Crawling the same address again updates it. Follows links in the pages' "
          "text to other sections and sites (menus and footers only within the site); follow='site' stays on the "
          "site, 'everything' follows every link up to max_pages. Runs in the background: check with list_crawls; "
          "the same copies are in the app's Crawler tab. Sites behind a sign-in need the user to sign in once in the "
          "Crawler tab on that computer, then browser=true.",
          {"url": {"type": "string"},
           "collection": _COL | {"description": "an existing collection to add the copy to"},
           "new_collection": {"type": "string", "pattern": "^[a-z0-9_-]{2,64}$",
                              "description": "or: a new collection to create for the copy when it is done"},
           "max_pages": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 1000},
           "follow": {"type": "string", "enum": ["linked", "site", "everything"], "default": "linked"},
           "browser": {"type": "boolean", "default": False,
                       "description": "use a real browser: pages built by JavaScript, or a saved sign-in"}},
          ["url"], needs="admin"),
    _tool("send_crawl", "Send a finished website copy (list_crawls) to a collection, existing or new: it becomes a "
          "synced folder there, indexed in the background, and later Updates of the copy are synced into it.",
          {"name": {"type": "string", "description": "the copy's name, from list_crawls"},
           "collection": _COL | {"description": "an existing collection"},
           "new_collection": {"type": "string", "pattern": "^[a-z0-9_-]{2,64}$",
                              "description": "or: a new collection to create for it"}},
          ["name"], needs="admin"),
    _tool("list_crawls", "Website copies: running, finished (pages, failed, sync counts) or kept from before.", {},
          needs="admin"),
    _tool("create_collection", "Create a collection (lowercase letters, digits, - and _). Only administrators see it "
          "until access is granted in the app (Users & access).",
          {"name": {"type": "string"}, "description": {"type": "string"}}, ["name"], needs="admin"),
    _tool("get_settings", "Search and indexing settings: current values, defaults, the models available (embedding, "
          "reranker, summary model) and how much of the index matches the current settings.", {}, needs="admin"),
    _tool("change_settings", "Change settings, e.g. {\"rerank_enabled\": true} or {\"embed_model\": \"...\"}; names "
          "and allowed values as in get_settings. Changing the embedding model or passage size makes every document "
          "outdated until reindex runs (can take hours on a large archive): tell the user first.",
          {"changes": {"type": "object"}}, ["changes"], needs="admin"),
    _tool("download_model", "Download a model listed by get_settings (runs in the background; offline installs refuse).",
          {"kind": {"type": "string", "enum": ["embed", "rerank", "llm"]}, "name": {"type": "string"}},
          ["kind", "name"], needs="admin"),
    _tool("reindex", "Index again the documents made with older settings, or all=true for every document (hours on a "
          "large archive: ask the user first).", {"all": {"type": "boolean", "default": False}}, needs="admin"),
]


class ToolError(Exception):
    pass


async def _call(c: httpx.AsyncClient, method: str, path: str, body=None):
    r = await c.request(method, path, json=body)
    if r.status_code == 401:
        raise ToolError("the access token is invalid or expired: create a new one on the app's AI assistants page")
    if r.status_code == 404:
        raise ToolError("not found, or not readable with this account")
    if r.status_code in (400, 409, 422):
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise ToolError(f"refused: {detail}")
    if r.status_code >= 400 and not (r.headers.get("content-type", "").startswith("application/json")
                                     and "outcome" in r.json()):
        raise ToolError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


def _where(r: dict) -> str:
    return " > ".join(x for x in [r.get("heading_path") or "", f"page {r['page']}" if r.get("page") else ""] if x)


async def _run(name: str, a: dict, c: httpx.AsyncClient, base: str) -> dict | list:
    link = lambda u: base + u if u and u.startswith("/") else u  # noqa: E731
    if name == "list_collections":
        return [{"name": x["name"], "description": x.get("description") or "", "can_add_documents": x["can_write"]}
                for x in (await _call(c, "GET", "/api/me"))["collections"]]
    if name in _MANAGE:
        return await _MANAGE[name](a, c)
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


async def _collection(c: httpx.AsyncClient, name: str, write: bool = False) -> int:
    for x in (await _call(c, "GET", "/api/me"))["collections"]:
        if x["name"].lower() == str(name).lower() and (x["can_write"] or not write):
            return x["id"]
    raise ToolError(f"no collection named {name!r} that this account can {'add to' if write else 'read'}; "
                    "see list_collections (administrators can create_collection)")


async def _documents(a, c):
    rows = await _call(c, "GET", "/api/documents")
    want = (a.get("name_contains") or "").lower()
    rows = [r for r in rows if (not a.get("collection") or r["collection"].lower() == str(a["collection"]).lower())
            and (not a.get("status") or r["status"] == a["status"])
            and (not want or want in f"{r['title']} {r['source_key']}".lower())]
    keep = ("id", "title", "source_key", "collection", "status", "error_code", "error_detail", "updated_at")
    return {"count": len(rows), "documents": [{k: r[k] for k in keep if r.get(k) not in (None, "")} for r in rows[:200]],
            **({"note": "first 200 shown; narrow with collection, status or name_contains"} if len(rows) > 200 else {})}


async def _add_document(a, c):
    path = str(a["path"]).strip()
    if not path.lower().endswith((".md", ".markdown", ".txt")):
        raise ToolError("path must end in .md or .txt")
    cid = await _collection(c, a["collection"], write=True)
    r = await c.post("/api/documents", data={"collection_id": str(cid), "path": path},
                     files={"file": (path.replace("\\", "/").split("/")[-1], str(a["text"]).encode())})
    if r.status_code >= 400:
        raise ToolError(f"not added: {r.text[:300]}")
    return r.json() | {"next": "indexing runs in the background; list_documents shows when it is indexed"}


async def _add_folder(a, c):
    cid = await _collection(c, a["collection"], write=True)
    return await _call(c, "POST", "/api/admin/folders", {"path": a["path"], "collection_id": cid,
                                                         **({"types": a["types"]} if a.get("types") else {})})


async def _into(a, c) -> dict:
    if bool(a.get("collection")) == bool(a.get("new_collection")):
        raise ToolError("give collection (an existing one) or new_collection (a new one)")
    return ({"collection_id": await _collection(c, a["collection"], write=True)} if a.get("collection")
            else {"new_collection": str(a["new_collection"])})


async def _crawl(a, c):
    return await _call(c, "POST", "/api/admin/crawls", {"url": a["url"], **await _into(a, c),
                                                        "max_pages": int(a.get("max_pages") or 1000),
                                                        "follow": a.get("follow") or "linked",
                                                        "browser": bool(a.get("browser"))})


async def _send_crawl(a, c):
    return await _call(c, "POST", "/api/admin/crawls/send", {"name": str(a["name"]), **await _into(a, c)})


async def _settings(a, c):
    s = await _call(c, "GET", "/api/admin/settings")
    return {"values": s["values"], "defaults": s["defaults"], "limits": s["limits"], "index": s["index"],
            "models": {k: [x["name"] for x in v] for k, v in s["options"].items()}, "summary_model": s["llm"]}


_MANAGE = {
    "index_status": lambda a, c: _call(c, "GET", "/api/status"),
    "list_documents": _documents,
    "add_document": _add_document,
    "retry_document": lambda a, c: _call(c, "POST", f"/api/documents/{a['document_id']}/retry"),
    "delete_document": lambda a, c: _call(c, "DELETE", f"/api/documents/{a['document_id']}"),
    "add_folder": _add_folder,
    "list_folders": lambda a, c: _call(c, "GET", "/api/admin/folders"),
    "sync_folder": lambda a, c: _call(c, "POST", f"/api/admin/folders/{int(a['folder_id'])}/sync"),
    "crawl_website": _crawl,
    "send_crawl": _send_crawl,
    "list_crawls": lambda a, c: _call(c, "GET", "/api/admin/crawls"),
    "create_collection": lambda a, c: _call(c, "POST", "/api/admin/collections",
                                            {"name": a["name"], "description": a.get("description", "")}),
    "get_settings": _settings,
    "change_settings": lambda a, c: _call(c, "PUT", "/api/admin/settings", a["changes"]),
    "download_model": lambda a, c: _call(c, "POST", "/api/admin/models/download", {"kind": a["kind"], "name": a["name"]}),
    "reindex": lambda a, c: _call(c, "POST", "/api/admin/reindex", {"all": bool(a.get("all"))}),
}


async def _tools_for(c: httpx.AsyncClient) -> list[dict]:
    """The tools this account can use: administration for administrators, adding documents for writers."""
    me = await _call(c, "GET", "/api/me")
    can = {"", "admin", "write"} if me.get("is_admin") else {"", "write"} if any(
        x["can_write"] for x in me["collections"]) else {""}
    return [{k: v for k, v in t.items() if k != "needs"} for t in TOOLS if t["needs"] in can]


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
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": await _tools_for(c)}}
    if method == "tools/call":
        try:
            if params.get("name") not in {t["name"] for t in await _tools_for(c)}:
                raise ToolError(f"unknown tool {params.get('name')}, or not available to this account")
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
