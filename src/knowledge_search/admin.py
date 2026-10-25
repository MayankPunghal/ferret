"""Administrator API: runtime settings, reindexing, collections/access, users. All routes require an admin
(non-admins get 404, as for any resource they cannot see)."""

import logging
import threading
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth, db, enrich, runtime
from .deps import Admin, Conn, state
from .embedder import MODELS
from .ingest import reindex, retry_document
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


_downloads: dict[str, str] = {}  # "kind:name" -> "downloading" | "error: ..." (Download buttons)


def _model_rows(kind: str, current: str) -> list[dict]:
    """Every model of a kind: on this computer or not, a download under way, the one in use and its load state."""
    s = runtime.base()
    if kind == "embed":
        items = [(m.name, f"~{m.size_mb} MB", runtime.downloaded("embed", m.name)) for m in MODELS.values()]
    elif kind == "rerank":
        items = [(k, f"~{v[1]} MB", runtime.downloaded("rerank", k)) for k, v in runtime.RERANKERS.items()]
    else:
        items = [(k, v[2].split(",")[0], bool(enrich.installed(s, v[1]))) for k, v in enrich.LLMS.items()]
    loaded = runtime.model_states()
    out = []
    for name, size, have in items:
        st = _downloads.get(f"{kind}:{name}", "")
        if not st and name == current:  # the model in use: loading / ready / error
            if kind == "llm":
                st = getattr(getattr(state, "enricher", None), "state", "")
            else:
                key = f"{'embedding' if kind == 'embed' else 'reranker'}: {name}"
                st = "loading" if _loading.get(name) == "loading" else loaded.get(key) or _loading.get(name, "")
        out.append({"name": name, "size": size, "downloaded": have, "in_use": name == current, "state": st})
    return out


@router.get("/models")
def models(p: Admin, conn: Conn):
    rt = runtime.load(conn)
    e = getattr(state, "enricher", None)
    llm = rt.enrich_model or next((k for k, v in enrich.LLMS.items() if e and v[1] == e.model), "")
    return {"embed": _model_rows("embed", rt.embed_model),
            "rerank": _model_rows("rerank", rt.rerank_model if rt.rerank_enabled else ""),
            "llm": _model_rows("llm", llm if rt.enrich_enabled else "")}


class DownloadBody(BaseModel):
    kind: str = Field(pattern="^(embed|rerank|llm)$")
    name: str


@router.post("/models/download")
def download_model(body: DownloadBody, p: Admin):
    known = {"embed": MODELS, "rerank": runtime.RERANKERS, "llm": enrich.LLMS}[body.kind]
    if body.name not in known:
        raise HTTPException(404, "unknown model")
    key = f"{body.kind}:{body.name}"
    if _downloads.get(key) == "downloading":
        return {"state": "downloading"}
    if runtime.base().offline:
        raise HTTPException(409, "downloads are off (KS_OFFLINE=true)")

    def run():
        try:
            if body.kind == "llm":
                enrich.download(runtime.base(), body.name, select=False)
            else:
                runtime.download(body.kind, body.name)
                if runtime.model_states().get(f"embedding: {body.name}", "").startswith("error"):
                    runtime.embedder(body.name).warm()  # it failed to download at start: load it now
                    with db.pool().connection() as conn:  # and index what failed for want of it
                        for r in conn.execute("SELECT id FROM documents WHERE status = 'failed' "
                                              "AND error_code = 'embedding_failed'").fetchall():
                            retry_document(conn, r["id"])
            _downloads.pop(key, None)
        except Exception as e:  # noqa: BLE001 - network/disk errors come in many types; shown next to the model
            log.warning("download failed %s: %s", key, e)
            _downloads[key] = f"error: {e}"[:300]

    _downloads[key] = "downloading"
    threading.Thread(target=run, daemon=True, name=f"download-{body.name}").start()
    return {"state": "downloading"}


def _settings_view(conn) -> dict:
    rt, s, enricher = runtime.load(conn), runtime.base(), getattr(state, "enricher", None)
    cov = coverage_for(conn, [r["id"] for r in conn.execute("SELECT id FROM collections")], rt.signature())
    return {
        "values": asdict(rt),
        "defaults": asdict(runtime.defaults(rt.embed_model)),
        "thresholds_tuned": runtime.thresholds_tuned(rt.embed_model),
        "limits": runtime.LIMITS,
        "options": {
            "embed_models": [{"name": m.name, "dim": m.dim, "license": m.license, "size_mb": m.size_mb}
                             for m in MODELS.values()],
            "rerankers": [{"name": k, "license": v[0], "size_mb": v[1], "note": v[2]}
                          for k, v in runtime.RERANKERS.items()],
            "llms": [{"name": k, "file": v[1], "note": v[2], "installed": bool(enrich.installed(s, v[1]))}
                     for k, v in enrich.LLMS.items()],
        },
        # the model now writing summaries; fixed when KS_LLM_URL names another local server
        "llm": {"current": getattr(enricher, "model", "") or "", "external": bool(s.llm_url),
                "default": enrich.DEFAULT_LLM, "gpu": enrich.gpu_supported(),
                "device": "gpu" if getattr(getattr(enricher, "server", None), "gpu_exe", None) else "cpu",
                "gpu_error": getattr(enricher, "gpu_error", "")},
        "system": (conn.execute("SELECT value FROM app_settings WHERE key = 'system'").fetchone() or {}).get("value"),
        "recommended": {"embed_model": s.embed_model, "rerank_model": s.rerank_model},
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
    if "embed_model" in clean and not ({"gate_dense_high", "gate_dense_min"} & set(clean)):
        # switching models: drop threshold overrides so the new model's tuned values apply
        conn.execute("DELETE FROM app_settings WHERE key IN "
                     "('gate_dense_high', 'gate_dense_min', 'gate_lex_coverage', 'gate_all_terms_min')")
    if "rerank_model" in clean and "gate_rerank_min" not in clean:  # reranker scores have model-specific scales
        conn.execute("DELETE FROM app_settings WHERE key = 'gate_rerank_min'")
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


@router.get("/updates")
def get_updates(p: Admin, check: bool = False):
    """Settings → Updates: this version, the newest on GitHub, and whether this install can update itself."""
    from . import updates

    return updates.check() if check else updates.status()


@router.post("/updates/install")
def install_update(p: Admin):
    from . import updates

    s = updates.status()
    if not (s["can_install"] and s["newer"]):
        raise HTTPException(400, "no newer Windows installer to install from here")
    updates.install(on_started=state.quit_app)
    return updates.status()


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


@router.get("/logs")
def get_logs(p: Admin, conn: Conn, level: str = "WARNING", limit: int = 500):
    """Recent application log records (no document or query text) plus a diagnostics header."""
    from . import logs

    rt = runtime.load(conn)
    extra = {"embedding_model": rt.embed_model, "reranker": rt.rerank_model if rt.rerank_enabled else "off",
             "database": "external" if state.settings.database_url else "embedded",
             "vector_search": state.settings.vector_search,
             "documents": conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"],
             "chunks": conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"],
             "jobs": {r["status"]: r["n"] for r in conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY 1")},
             "log_file": str(state.settings.data_dir / "logs" / "knowledge-search.log")}
    return {"diagnostics": logs.diagnostics(extra), "records": logs.records(level, max(1, min(limit, 2000)))}


@router.get("/activity")
def get_activity(p: Admin, kind: str = ""):
    """Recent calls with timing (trace.py): the list, newest first, without request and response bodies."""
    from . import trace

    return {"calls": trace.calls(kind), "kept": trace.MAX_CALLS}


@router.get("/activity/{call_id}")
def get_call(call_id: int, p: Admin):
    from . import trace

    c = trace.get(call_id)
    if c is None:
        raise HTTPException(404, "no longer kept (only the most recent calls are)")
    return c


@router.delete("/activity", status_code=204)
def clear_activity(p: Admin):
    from . import trace

    trace.clear()


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
    _known(conn, [body.group])
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


# --- groups: created and deleted here; grants and users pick from the existing ones ------------

class GroupBody(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_.-]{2,64}$")


@router.get("/groups")
def list_groups(p: Admin, conn: Conn):
    return [r["name"] for r in conn.execute("SELECT name FROM groups ORDER BY name")]


@router.post("/groups", status_code=201)
def create_group(body: GroupBody, p: Admin, conn: Conn):
    if conn.execute("SELECT 1 FROM groups WHERE name = %s", (body.name,)).fetchone():
        raise HTTPException(409, "a group with this name exists")
    auth.ensure_group(conn, body.name)
    auth.audit(conn, p.user_id, "group_created", body.name)
    conn.commit()
    return {"name": body.name}


@router.delete("/groups/{name}")
def delete_group(name: str, p: Admin, conn: Conn):
    """Delete a group: its members lose the access it gave (they are signed out), its grants go with it."""
    g = conn.execute("SELECT id FROM groups WHERE name = %s", (name,)).fetchone()
    if not g:
        raise HTTPException(404, "not found")
    conn.execute("DELETE FROM sessions WHERE user_id IN (SELECT user_id FROM user_groups WHERE group_id = %s)", (g["id"],))
    conn.execute("DELETE FROM groups WHERE id = %s", (g["id"],))
    auth.audit(conn, p.user_id, "group_deleted", name)
    conn.commit()
    return {"ok": True}


def _known(conn, groups: list[str]) -> list[str]:
    groups = sorted({g.strip() for g in groups if g.strip()})
    have = {r["name"] for r in conn.execute("SELECT name FROM groups WHERE name = ANY(%s)", (groups,)).fetchall()}
    if missing := [g for g in groups if g not in have]:
        raise HTTPException(400, f"no group named {', '.join(missing)}: create it under Groups first")
    return groups


def _set_groups(conn, user_id: int, username: str, groups: list[str]) -> None:
    groups = _known(conn, groups)
    conn.execute("DELETE FROM user_groups WHERE user_id = %s", (user_id,))
    for g in groups:
        auth.add_user_to_group(conn, username, g)


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
