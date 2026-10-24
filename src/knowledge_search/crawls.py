"""Administrator: copy websites into Markdown with site2md, from inside Ferret.

The Crawler tab is site2md's own page (the same one its stand-alone launcher opens), served at /crawler/ and
driven through /api/admin/crawler/* with the administrator's session, so no access token is needed. One site2md
App keeps the copies in a folder: <data>/crawls by default, changeable on the page (kept in
<data>/crawler.json). Sending a copy to a collection makes it a synced folder (folders.py); when an Update of a
copy that is a synced folder finishes, it is synced again, so only what changed is indexed. MCP's
crawl_website starts a copy here and syncs it into the chosen collection when it is done (a new collection is
created only then, so a copy that fails leaves no empty collection behind).
"""

import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from site2md import ui

from . import auth, db, folders
from .deps import Admin, Conn, state

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])
_app: ui.App | None = None
_app_for = ""  # the data folder _app belongs to
_lock = threading.Lock()
# copy name -> (collection id, or the name of a collection to create, and the user) to sync into when it is done
_into: dict[str, tuple[int | str, int | None]] = {}
PAGE_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'self'; base-uri 'none'")


def _config() -> Path:
    return Path(state.settings.data_dir) / "crawler.json"


def app() -> ui.App:
    global _app, _app_for
    with _lock:
        if _app is None or _app_for != str(state.settings.data_dir):
            _app_for = str(state.settings.data_dir)
            try:
                chosen = json.loads(_config().read_text(encoding="utf-8")).get("copies", "")
            except (OSError, ValueError):
                chosen = ""
            _app = ui.App(Path(chosen) if chosen else Path(state.settings.data_dir) / "crawls",
                          on_copies=lambda f: _config().write_text(json.dumps({"copies": str(f)}), encoding="utf-8"))
            _app.on_done = _done
        return _app


def _sync_into(folder: Path, into: int | str, user: int | None) -> int:
    """Make a copy a synced folder of a collection (created first when into is a new name) and sync it now."""
    with db.pool().connection() as conn:
        if isinstance(into, str):
            into, new = auth.ensure_collection(conn, into), into
            auth.audit(conn, user, "collection_created", new, by="crawl")
        fid = conn.execute(
            "INSERT INTO folder_sources(collection_id, path, types, created_by) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (collection_id, path) DO UPDATE SET types = folder_sources.types RETURNING id",
            (into, str(folder.resolve()), list(folders.TYPES), user)).fetchone()["id"]
        conn.commit()
    folders.start(fid, user)
    return into


def _done(name: str, folder: Path, copy_state: str) -> None:
    """A copy stopped: sync it where it belongs (a collection MCP asked for, or its synced folders)."""
    if copy_state != "done":
        return
    if name in _into:
        _sync_into(folder, *_into.pop(name))
        return
    with db.pool().connection() as conn:
        ids = [(r["id"], r["created_by"]) for r in conn.execute(
            "SELECT id, created_by FROM folder_sources WHERE path = %s", (str(folder.resolve()),)).fetchall()]
    for fid, user in ids:
        folders.start(fid, user)


# --- the page and its API ---------------------------------------------------------------------------------

@router.get("/crawler/")
def page(p: Admin):
    return Response((ui.STATIC / "index.html").read_bytes(), media_type="text/html",
                    headers={"Content-Security-Policy": PAGE_CSP, "Cache-Control": "no-store"})


@router.get("/crawler/{asset}")
def asset(asset: str):
    if "/" + asset not in ui.ASSETS:
        raise HTTPException(404, "not found")
    return Response((ui.STATIC / asset).read_bytes(), media_type=ui.ASSETS["/" + asset].split(";")[0],
                    headers={"Content-Security-Policy": PAGE_CSP})


@router.api_route("/api/admin/crawler/{path:path}", methods=["GET", "POST"])
async def crawler_api(path: str, request: Request, p: Admin):
    body = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "not JSON") from None
    if path in ("ks/collections", "send"):  # inside the app the page adds copies itself (synced folders)
        raise HTTPException(404, "not here: the Crawler tab sends copies with your own session")
    out = app().handle(request.method, path, dict(request.query_params), body if isinstance(body, dict) else {})
    if request.method == "POST" and path == "start" and out[0] == 200:
        with db.pool().connection() as conn:
            auth.audit(conn, p.user_id, "crawl", body.get("url") or body.get("name"), mode=body.get("mode", "new"))
            conn.commit()
    if isinstance(out[1], bytes):
        return Response(out[1], status_code=out[0], media_type=out[2] if len(out) > 2 else None)
    if out[0] >= 400:
        raise HTTPException(out[0], out[1].get("error", "refused"))
    return out[1]


# --- for MCP: copy a site into a collection ----------------------------------------------------------------

class Into(BaseModel):
    collection_id: int | None = None
    new_collection: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{2,64}$")


class CrawlBody(Into):
    url: str = Field(pattern=r"^https?://\S+$", max_length=2000)
    max_pages: int = Field(default=1000, ge=1, le=100_000)
    browser: bool = False  # fetch with a real browser: JavaScript sites, and sign-ins saved on this computer
    follow: str = Field(default="linked", pattern="^(site|linked|everything)$")


class SendBody(Into):
    name: str = Field(min_length=1, max_length=120)


def _into_of(body: Into, conn) -> int | str:
    """The collection to send to: an existing id, or a new name (refused if that name is taken)."""
    if (body.collection_id is None) == (body.new_collection is None):
        raise HTTPException(422, "give collection_id or new_collection")
    if body.new_collection and conn.execute("SELECT 1 FROM collections WHERE name = %s",
                                            (body.new_collection,)).fetchone():
        raise HTTPException(409, "a collection with this name exists")
    if body.collection_id and not conn.execute("SELECT 1 FROM collections WHERE id = %s",
                                               (body.collection_id,)).fetchone():
        raise HTTPException(404, "collection not found")
    return body.collection_id or body.new_collection


@router.post("/api/admin/crawls", status_code=202)
def start(body: CrawlBody, p: Admin, conn: Conn):
    """Copy a website into a collection; copying the same address again updates it."""
    into = _into_of(body, conn)
    a, name = app(), ui.name_for(body.url)
    mode = "update" if (a.copies / name / "manifest.json").exists() else "new"
    _into[name] = (into, p.user_id)
    try:
        out = a.start({"url": body.url, "name": name, "mode": mode,
                       "options": {"max_pages": body.max_pages, "browser": body.browser, "follow": body.follow}})
    except ValueError as e:
        _into.pop(name, None)
        raise HTTPException(409, str(e)) from None
    auth.audit(conn, p.user_id, "crawl", body.url, into=into, max_pages=body.max_pages,
               browser=body.browser, follow=body.follow)
    conn.commit()
    return out | {"into": into}


@router.get("/api/admin/crawls")
def list_crawls(p: Admin):
    """Copies kept in the copies folder (the running one first), with counts; also shown in the Crawler tab."""
    a = app()
    rows = a.copies_view()
    if a.run:
        rows.sort(key=lambda r: r["name"] != a.run.name)
        for r in rows:
            if r["name"] == a.run.name:
                r.update(a.detail(r["name"])["counts"], state=a.run.state)
    return [r | {"into": _into.get(r["name"], (None,))[0]} for r in rows]


@router.post("/api/admin/crawls/send")
def send(body: SendBody, p: Admin, conn: Conn):
    """Send a finished copy to a collection (or a new one): it becomes a synced folder there."""
    a = app()
    if a.run and a.run.name == body.name and a.run.state not in ("done", "stopped", "failed"):
        raise HTTPException(409, "still copying: it is sent when it is done if crawl_website named a collection")
    folder = a.copies / body.name
    if "/" in body.name or "\\" in body.name or not (folder / "manifest.json").exists():
        raise HTTPException(404, "no copy with this name (list_crawls)")
    into = _into_of(body, conn)
    cid = _sync_into(folder, into, p.user_id)
    auth.audit(conn, p.user_id, "crawl_sent", body.name, collection_id=cid)
    conn.commit()
    return {"name": body.name, "collection_id": cid, "next": "indexing runs in the background (list_folders)"}
