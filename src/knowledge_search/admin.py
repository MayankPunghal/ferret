"""Administrator API: runtime settings, reindexing, collections/access, users. All routes require an admin
(non-admins get 404, as for any resource they cannot see)."""

import logging
import threading
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth, runtime
from .deps import Admin, Conn, state
from .embedder import MODELS
from .ingest import reindex
from .search import coverage_for

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])
_loading: dict[str, str] = {}  # model name -> "loading" | "ready" | "error: ..."


def _warm(kind: str, name: str) -> None:
    def run():
        _loading[name] = "loading"
        try:
            if kind == "embed":
                runtime.embedder(name).warm()
            else:
                runtime.rerank_scores(name, "warm up", ["warm up"])
            _loading[name] = "ready"
        except Exception as e:  # noqa: BLE001 - reported on the settings page
            log.warning("model load failed %s: %s", name, e)
            _loading[name] = f"error: {e}"[:300]

    threading.Thread(target=run, daemon=True, name=f"warm-{name}").start()


def _settings_view(conn) -> dict:
    rt = runtime.load(conn)
    cov = coverage_for(conn, [r["id"] for r in conn.execute("SELECT id FROM collections")], rt.signature())
    return {
        "values": asdict(rt),
        "defaults": asdict(runtime.defaults()),
        "limits": runtime.LIMITS,
        "options": {
            "embed_models": [{"name": m.name, "dim": m.dim, "license": m.license, "size_mb": m.size_mb}
                             for m in MODELS.values()],
            "rerankers": [{"name": k, "license": v[0], "size_mb": v[1], "note": v[2]}
                          for k, v in runtime.RERANKERS.items()],
        },
        "model_status": {**runtime.model_states(), **{f"download: {k}": v for k, v in _loading.items()}},
        "index": {"signature": rt.signature(), **cov},
    }


@router.get("/settings")
def get_settings(p: Admin, conn: Conn):
    return _settings_view(conn)


@router.put("/settings")
def put_settings(changes: dict, p: Admin, conn: Conn):
    try:
        clean = runtime.validate(changes)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e)) from e
    before = runtime.load(conn)
    runtime.save(conn, clean, p.user_id)
    auth.audit(conn, p.user_id, "settings_changed", None, keys=sorted(clean))
    conn.commit()
    after = runtime.load(conn)
    if after.embed_model != before.embed_model:
        _warm("embed", after.embed_model)
    if after.rerank_enabled and (after.rerank_model != before.rerank_model or not before.rerank_enabled):
        _warm("rerank", after.rerank_model)
    return _settings_view(conn)


class ReindexBody(BaseModel):
    all: bool = False


@router.post("/reindex")
def do_reindex(body: ReindexBody, p: Admin, conn: Conn):
    n = reindex(conn, runtime.load(conn).signature(), everything=body.all)
    auth.audit(conn, p.user_id, "reindex", None, documents=n, everything=body.all)
    conn.commit()
    return {"queued": n}


@router.get("/status")
def admin_status(p: Admin, conn: Conn):
    jobs = conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
    oldest = conn.execute(
        "SELECT extract(epoch FROM now() - min(created_at)) AS s FROM jobs WHERE status = 'queued'"
    ).fetchone()["s"]
    models = conn.execute("SELECT embedding_model, count(*) AS chunks FROM chunks GROUP BY 1").fetchall()
    return {
        "jobs": {r["status"]: r["n"] for r in jobs},
        "oldest_queued_job_seconds": oldest,
        "chunks_by_vector_space": models,
        "active_signature": runtime.load(conn).signature(),
        "workers_in_process": len(state.threads),
    }


# --- collections and access -------------------------------------------------------------------

class CollectionBody(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_-]{2,64}$")
    description: str = Field(default="", max_length=500)


class GrantBody(BaseModel):
    group: str = Field(min_length=1, max_length=64)
    can_write: bool = False


@router.get("/collections")
def list_collections(p: Admin, conn: Conn):
    rows = conn.execute(
        "SELECT c.id, c.name, c.description, (SELECT count(*) FROM documents d WHERE d.collection_id = c.id) AS documents, "
        "coalesce(json_agg(json_build_object('group', g.name, 'can_write', cg.can_write) ORDER BY g.name) "
        "  FILTER (WHERE g.id IS NOT NULL), '[]') AS grants "
        "FROM collections c LEFT JOIN collection_grants cg ON cg.collection_id = c.id "
        "LEFT JOIN groups g ON g.id = cg.group_id GROUP BY c.id ORDER BY c.name"
    ).fetchall()
    return rows


@router.post("/collections", status_code=201)
def create_collection(body: CollectionBody, p: Admin, conn: Conn):
    if conn.execute("SELECT 1 FROM collections WHERE name = %s", (body.name,)).fetchone():
        raise HTTPException(409, "a collection with this name exists")
    cid = auth.ensure_collection(conn, body.name, body.description)
    auth.audit(conn, p.user_id, "collection_created", body.name)
    conn.commit()
    return {"id": cid}


@router.put("/collections/{collection_id}/grants")
def put_grant(collection_id: int, body: GrantBody, p: Admin, conn: Conn):
    col = conn.execute("SELECT name FROM collections WHERE id = %s", (collection_id,)).fetchone()
    if not col:
        raise HTTPException(404, "not found")
    auth.grant(conn, col["name"], body.group, body.can_write)
    auth.audit(conn, p.user_id, "grant", col["name"], group=body.group, can_write=body.can_write)
    conn.commit()
    return {"ok": True}


@router.delete("/collections/{collection_id}/grants/{group}")
def delete_grant(collection_id: int, group: str, p: Admin, conn: Conn):
    conn.execute(
        "DELETE FROM collection_grants WHERE collection_id = %s AND group_id = (SELECT id FROM groups WHERE name = %s)",
        (collection_id, group),
    )
    auth.audit(conn, p.user_id, "revoke", str(collection_id), group=group)
    conn.commit()
    return {"ok": True}


# --- users ------------------------------------------------------------------------------------

class NewUser(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9_.-]{2,64}$")
    password: str = Field(min_length=10, max_length=256)
    is_admin: bool = False
    groups: list[str] = []


class UserPatch(BaseModel):
    groups: list[str] | None = None
    is_admin: bool | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=256)


@router.get("/users")
def list_users(p: Admin, conn: Conn):
    return conn.execute(
        "SELECT u.id, u.username, u.is_admin, u.disabled, coalesce(array_agg(g.name ORDER BY g.name) "
        "FILTER (WHERE g.id IS NOT NULL), '{}') AS groups FROM users u "
        "LEFT JOIN user_groups ug ON ug.user_id = u.id LEFT JOIN groups g ON g.id = ug.group_id "
        "GROUP BY u.id ORDER BY u.username"
    ).fetchall()


@router.get("/groups")
def list_groups(p: Admin, conn: Conn):
    return [r["name"] for r in conn.execute("SELECT name FROM groups ORDER BY name")]


def _set_groups(conn, user_id: int, username: str, groups: list[str]) -> None:
    conn.execute("DELETE FROM user_groups WHERE user_id = %s", (user_id,))
    for g in groups:
        if g.strip():
            auth.add_user_to_group(conn, username, g.strip())


@router.post("/users", status_code=201)
def create_user(body: NewUser, p: Admin, conn: Conn):
    if conn.execute("SELECT 1 FROM users WHERE username = %s", (body.username,)).fetchone():
        raise HTTPException(409, "username already exists")
    uid = auth.create_user(conn, body.username, body.password, body.is_admin)
    _set_groups(conn, uid, body.username, body.groups)
    auth.audit(conn, p.user_id, "user_created", body.username)
    conn.commit()
    return {"id": uid}


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, p: Admin, conn: Conn):
    u = conn.execute("SELECT id, username FROM users WHERE id = %s", (user_id,)).fetchone()
    if not u:
        raise HTTPException(404, "not found")
    if user_id == p.user_id and (body.disabled or body.is_admin is False):
        raise HTTPException(409, "you cannot disable or demote your own account")
    if body.groups is not None:
        _set_groups(conn, user_id, u["username"], body.groups)
    if body.is_admin is not None:
        conn.execute("UPDATE users SET is_admin = %s WHERE id = %s", (body.is_admin, user_id))
    if body.disabled is not None:
        conn.execute("UPDATE users SET disabled = %s WHERE id = %s", (body.disabled, user_id))
    if body.password:
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (auth.hash_password(body.password), user_id))
    if user_id != p.user_id:  # permission/credential changes sign the user out everywhere
        conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
    auth.audit(conn, p.user_id, "user_updated", u["username"], fields=sorted(body.model_dump(exclude_none=True)))
    conn.commit()
    return {"ok": True}
