"""HTTP API (OpenAPI at /docs) and static UI. One process; optional in-process ingestion workers."""

import logging
import re
import threading
from contextlib import asynccontextmanager
from importlib import resources
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db
from .auth import Principal
from .config import Settings, get_settings
from .embedder import Embedder
from .ingest import BlobStore, Worker, delete_document, retry_document, submit
from .parsing import CHUNKER_VERSION, PARSER_VERSION, ParseError
from .search import RETRIEVAL_FAILURE, GateConfig, MESSAGES, coverage_for, search

log = logging.getLogger(__name__)
COOKIE = "ks_session"
CSRF_HEADER = "x-ks-csrf"


class AppState:
    settings: Settings
    embedder: Embedder
    blobs: BlobStore
    stop: threading.Event
    threads: list[threading.Thread]


state = AppState()


def create_app(settings: Settings | None = None, embedder: Embedder | None = None, start_workers: bool = True):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.open_pool(settings.database_url)
        state.settings = settings
        state.embedder = embedder or Embedder(
            settings.embed_model, str(settings.model_cache), settings.embed_threads, settings.offline
        )
        state.blobs = BlobStore(settings.data_dir)
        state.stop = threading.Event()
        state.threads = []
        _record_index_meta()
        if embedder is None:
            state.embedder.warm()  # load the model before accepting traffic (avoids a slow first query)
        if start_workers:
            for i in range(settings.inprocess_workers):
                w = Worker(state.embedder, state.blobs, settings.max_pdf_pages, settings.max_extracted_chars)
                t = threading.Thread(
                    target=w.loop, args=(state.stop, settings.worker_poll_seconds), name=f"ingest-{i}", daemon=True
                )
                t.start()
                state.threads.append(t)
        yield
        state.stop.set()
        for t in state.threads:
            t.join(timeout=10)
        db.close_pool()

    app = FastAPI(title="Knowledge Search", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
        )
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    _routes(app)
    static = resources.files("knowledge_search") / "static"
    app.mount("/", StaticFiles(directory=str(static), html=True), name="ui")
    return app


def _record_index_meta() -> None:
    with db.pool().connection() as conn:
        for k, v in {
            "embedding_model": state.embedder.spec.name,
            "vector_space": state.embedder.spec.version_key,
            "parser_version": PARSER_VERSION,
            "chunker_version": CHUNKER_VERSION,
        }.items():
            conn.execute(
                "INSERT INTO app_meta(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (k, v),
            )
        conn.commit()


def get_conn():
    with db.pool().connection() as conn:
        yield conn


Conn = Annotated[object, Depends(get_conn)]


def current_principal(request: Request, conn: Conn) -> Principal:
    token = None
    via_cookie = False
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        token = authz[7:].strip()
    elif COOKIE in request.cookies:
        token, via_cookie = request.cookies[COOKIE], True
    if not token:
        raise HTTPException(401, "authentication required")
    # Cookie-authenticated state changes must carry a custom header (blocked cross-site by CORS).
    if via_cookie and request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "missing CSRF header")
    p = auth.load_principal(conn, token)
    if not p:
        raise HTTPException(401, "invalid or expired session")
    return p


User = Annotated[Principal, Depends(current_principal)]


def require_admin(p: User) -> Principal:
    if not p.is_admin:
        raise HTTPException(404, "not found")
    return p


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    collection_ids: list[int] | None = None
    limit: int = Field(default=10, ge=1, le=50)


def _doc_for(conn, p: Principal, document_id: UUID, write: bool = False):
    allowed = p.writable if write else p.readable
    row = conn.execute(
        "SELECT d.*, c.name AS collection FROM documents d JOIN collections c ON c.id = d.collection_id "
        "WHERE d.id = %s AND d.collection_id = ANY(%s)",
        (document_id, list(allowed)),
    ).fetchone()
    if not row:
        # Same response whether the document is missing or merely not visible to this user.
        raise HTTPException(404, "not found")
    return row


def _routes(app: FastAPI) -> None:
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(conn: Conn):
        conn.execute("SELECT 1")
        return {"status": "ready", "embedding_model": state.embedder.spec.name, "model_loaded": state.embedder._model is not None}

    @app.post("/api/auth/login")
    def login(body: LoginBody, response: Response, conn: Conn):
        uid = auth.authenticate(conn, body.username, body.password)
        if uid is None:
            auth.audit(conn, None, "login_failed", None)
            conn.commit()
            raise HTTPException(401, "invalid username or password")
        token = auth.create_session(conn, uid, state.settings.session_hours)
        auth.audit(conn, uid, "login", None)
        conn.commit()
        response.set_cookie(
            COOKIE, token, httponly=True, samesite="strict", secure=state.settings.cookie_secure,
            max_age=state.settings.session_hours * 3600, path="/",
        )
        return {"token": token}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, conn: Conn, p: User):
        token = request.cookies.get(COOKIE) or request.headers.get("authorization", "")[7:]
        auth.delete_session(conn, token)
        conn.commit()
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(p: User, conn: Conn):
        cols = conn.execute(
            "SELECT id, name, description FROM collections WHERE id = ANY(%s) ORDER BY name", (list(p.readable),)
        ).fetchall()
        return {
            "username": p.username,
            "is_admin": p.is_admin,
            "collections": [dict(c, can_write=c["id"] in p.writable) for c in cols],
        }

    @app.post("/api/search")
    def do_search(body: SearchBody, p: User, conn: Conn):
        s = state.settings
        gate = GateConfig(s.gate_dense_high, s.gate_dense_min, s.gate_lex_coverage)
        try:
            resp = search(conn, state.embedder, p, body.query, gate, body.collection_ids, body.limit)
        except Exception:  # noqa: BLE001 - explicit failure state, no internals leaked
            log.exception("search failed")
            conn.rollback()
            return JSONResponse(
                status_code=503,
                content={"outcome": RETRIEVAL_FAILURE, "message": MESSAGES[RETRIEVAL_FAILURE], "results": []},
            )
        # Audit records that a search happened and its outcome — not the query text.
        auth.audit(conn, p.user_id, "search", None, outcome=resp.outcome, n=len(resp.results))
        conn.commit()
        return resp.to_dict()

    @app.get("/api/documents")
    def list_documents(p: User, conn: Conn, collection_id: int | None = None):
        scope = [c for c in p.readable if collection_id is None or c == collection_id]
        rows = conn.execute(
            "SELECT d.id, d.title, d.source_key, d.source_uri, d.status, d.error_code, d.error_detail, d.updated_at, "
            "c.name AS collection, d.collection_id, v.version_no AS latest_version, iv.version_no AS indexed_version, "
            "v.media_type, v.size_bytes, (SELECT count(*) FROM chunks ch WHERE ch.document_id = d.id) AS chunks "
            "FROM documents d JOIN collections c ON c.id = d.collection_id "
            "LEFT JOIN document_versions v ON v.id = d.latest_version_id "
            "LEFT JOIN document_versions iv ON iv.id = d.indexed_version_id "
            "WHERE d.collection_id = ANY(%s) ORDER BY d.updated_at DESC LIMIT 500",
            (scope,),
        ).fetchall()
        return [dict(r, id=str(r["id"]), can_write=r["collection_id"] in p.writable) for r in rows]

    @app.post("/api/documents", status_code=202)
    async def upload(
        p: User,
        conn: Conn,
        file: UploadFile = File(...),
        collection_id: int = Form(...),
        source_uri: str | None = Form(None),
    ):
        if collection_id not in p.writable:
            raise HTTPException(404, "collection not found")
        max_bytes = state.settings.max_upload_mb * 1024 * 1024
        data = await file.read(max_bytes + 1)
        try:
            res = submit(
                conn, state.blobs, collection_id=collection_id, filename=file.filename or "", data=data,
                user_id=p.user_id, max_bytes=max_bytes, source_uri=(source_uri or None),
            )
        except ParseError as e:
            raise HTTPException(422, {"code": e.code, "detail": e.detail}) from e
        auth.audit(conn, p.user_id, "upload", str(res.document_id), outcome=res.outcome)
        conn.commit()
        return {"document_id": str(res.document_id), "version_id": res.version_id, "outcome": res.outcome}

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: UUID, p: User, conn: Conn):
        d = _doc_for(conn, p, document_id)
        versions = conn.execute(
            "SELECT id, version_no, sha256, filename, media_type, size_bytes, created_at FROM document_versions "
            "WHERE document_id = %s ORDER BY version_no DESC",
            (document_id,),
        ).fetchall()
        jobs = conn.execute(
            "SELECT id, version_id, status, attempts, error_code, error_detail, updated_at FROM jobs "
            "WHERE document_id = %s ORDER BY id DESC LIMIT 10",
            (document_id,),
        ).fetchall()
        return {**{k: d[k] for k in ("title", "source_key", "source_uri", "status", "error_code", "error_detail",
                                      "metadata", "collection", "latest_version_id", "indexed_version_id")},
                "id": str(d["id"]), "versions": versions, "jobs": jobs}

    @app.delete("/api/documents/{document_id}")
    def remove_document(document_id: UUID, p: User, conn: Conn):
        _doc_for(conn, p, document_id, write=True)
        delete_document(conn, state.blobs, document_id)
        auth.audit(conn, p.user_id, "delete", str(document_id))
        conn.commit()
        return {"deleted": True}

    @app.post("/api/documents/{document_id}/retry")
    def retry(document_id: UUID, p: User, conn: Conn):
        _doc_for(conn, p, document_id, write=True)
        if not retry_document(conn, document_id):
            raise HTTPException(409, "document is not in a failed state")
        auth.audit(conn, p.user_id, "retry", str(document_id))
        conn.commit()
        return {"queued": True}

    @app.get("/api/documents/{document_id}/source")
    def source(document_id: UUID, p: User, conn: Conn, version: str = "indexed"):
        d = _doc_for(conn, p, document_id)
        vid = d["indexed_version_id"] if version == "indexed" and d["indexed_version_id"] else d["latest_version_id"]
        v = conn.execute("SELECT sha256, filename, media_type FROM document_versions WHERE id = %s", (vid,)).fetchone()
        if not v:
            raise HTTPException(404, "not found")
        path = state.blobs.path(v["sha256"])
        if not path.exists():
            raise HTTPException(410, "original file is missing from storage")
        auth.audit(conn, p.user_id, "open_source", str(document_id))
        conn.commit()
        # Never serve uploaded content as HTML; sandbox it even for PDFs.
        mt = "application/pdf" if v["media_type"] == "application/pdf" else "text/plain; charset=utf-8"
        safe = re.sub(r"[^\w.\-]", "_", v["filename"])
        return FileResponse(
            path, media_type=mt,
            headers={"Content-Disposition": f'inline; filename="{safe}"', "Content-Security-Policy": "sandbox",
                     "Cache-Control": "private, no-store"},
        )

    @app.get("/api/chunks/{chunk_id}")
    def passage(chunk_id: int, p: User, conn: Conn):
        c = conn.execute(
            "SELECT c.id, c.document_id, c.ordinal, c.heading_path, c.page, c.anchor, c.text, c.doc_title, "
            "c.version_id, col.name AS collection, v.version_no, v.media_type, d.source_uri "
            "FROM chunks c JOIN collections col ON col.id = c.collection_id "
            "JOIN document_versions v ON v.id = c.version_id JOIN documents d ON d.id = c.document_id "
            "WHERE c.id = %s AND c.collection_id = ANY(%s)",
            (chunk_id, list(p.readable)),
        ).fetchone()
        if not c:
            raise HTTPException(404, "not found")
        ctx = conn.execute(
            "SELECT id, ordinal, heading_path, page, text FROM chunks WHERE version_id = %s "
            "AND ordinal BETWEEN %s AND %s ORDER BY ordinal",
            (c["version_id"], c["ordinal"] - 1, c["ordinal"] + 1),
        ).fetchall()
        return {**dict(c), "document_id": str(c["document_id"]), "context": ctx}

    @app.get("/api/status")
    def status(p: User, conn: Conn):
        """Index health for the caller's own scope (no information about other collections)."""
        cov = coverage_for(conn, list(p.readable), state.embedder.spec.version_key)
        per = conn.execute(
            "SELECT c.id, c.name, count(d.id) AS documents, "
            "count(d.id) FILTER (WHERE d.status = 'indexed') AS indexed, "
            "count(d.id) FILTER (WHERE d.status IN ('pending','processing')) AS pending, "
            "count(d.id) FILTER (WHERE d.status = 'failed') AS failed "
            "FROM collections c LEFT JOIN documents d ON d.collection_id = c.id "
            "WHERE c.id = ANY(%s) GROUP BY c.id ORDER BY c.name",
            (list(p.readable),),
        ).fetchall()
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_meta")}
        return {"coverage": cov, "collections": per, "index": meta}

    @app.get("/api/admin/status")
    def admin_status(p: Annotated[Principal, Depends(require_admin)], conn: Conn):
        jobs = conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
        oldest = conn.execute(
            "SELECT extract(epoch FROM now() - min(created_at)) AS s FROM jobs WHERE status = 'queued'"
        ).fetchone()["s"]
        models = conn.execute("SELECT embedding_model, count(*) AS chunks FROM chunks GROUP BY 1").fetchall()
        return {
            "jobs": {r["status"]: r["n"] for r in jobs},
            "oldest_queued_job_seconds": oldest,
            "chunks_by_vector_space": models,
            "active_vector_space": state.embedder.spec.version_key,
            "workers_in_process": len(state.threads),
        }
