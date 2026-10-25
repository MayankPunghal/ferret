"""HTTP API (OpenAPI at /docs) and static UI. One process; optional in-process ingestion workers."""

import json
import logging
import mimetypes
import re
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from uuid import UUID

import psycopg
import psycopg_pool
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import admin, auth, crawls, db, dbview, enrich, folders, graph, mcp, runtime, trace, vectors
from .auth import Principal
from .config import Settings, get_settings, memory_vectors
from .deps import COOKIE, Conn, User, state
from .embedder import Embedder
from .ingest import BlobStore, Worker, delete_document, path_key, reindex, retry_document, submit
from .parsing import CHUNKER_VERSION, DOCX, PARSER_VERSION, PPTX, ParseError
from .search import MESSAGES, RETRIEVAL_FAILURE, coverage_for, search

mimetypes.add_type("text/javascript", ".mjs")  # PDF.js modules; some systems map .mjs to nothing
mimetypes.add_type("text/javascript", ".js")  # Windows takes .js types from the registry (often application/javascript)

# The Documents tab's filters and sort columns (SQL over `documents d`, `collections c`, the job `j`).
SHOWN = ("(CASE WHEN d.enrichment ? 'summary' AND d.enrichment->>'version_id' = d.indexed_version_id::text "
         "THEN d.enrichment->>'{}' END)")  # what enrich.shown() shows: written for the indexed version
DOC_STATUS = {"indexing": "AND d.status IN ('pending', 'processing') ", "failed": "AND d.status = 'failed' ",
              "outdated": "AND d.status = 'indexed' AND d.index_signature IS DISTINCT FROM %(sig)s "}
# ponytail: lower() sorts "Step 10" before "Step 2"; an ICU collation with numeric ordering if anyone minds.
DOC_SORT = {"name": ("lower(coalesce(" + SHOWN.format("title") + ", d.title))",),
            "type": ("coalesce(" + SHOWN.format("type") + ", '~')",), "collection": ("c.name",),
            "status": ("CASE d.status WHEN 'processing' THEN 0 WHEN 'pending' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END",
                       "j.id"),  # waiting documents in queue order
            "chunks": ("(SELECT count(*) FROM chunks ch WHERE ch.document_id = d.id)",), "updated": ("d.updated_at",)}
# The PDF viewer page: our own scripts only (PDF.js with eval off; WebAssembly for JPEG 2000 images), the PDF
# fetched from this server, fonts PDF.js builds from the file, no framing.
PDF_VIEWER_CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; connect-src 'self'; "
                  "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data: blob:; "
                  "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
log = logging.getLogger(__name__)
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def create_app(settings: Settings | None = None, embedder: Embedder | None = None, start_workers: bool = True):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from . import logs

        logs.install(settings.data_dir)
        runtime.configure(settings)
        url = db.resolve_url(settings)
        applied = db.migrate(url)  # upgrades are applied automatically on start (forward-only)
        if applied:
            log.warning("database upgraded: %s", ", ".join(applied))
        db.open_pool(url)
        if settings.system_check:
            from . import system

            with db.pool().connection() as conn:
                system.apply_once(conn)
        state.settings = settings
        state.blobs = BlobStore(settings.data_dir)
        state.stop = threading.Event()
        state.threads = []
        if embedder is not None:
            runtime.register_embedder(embedder)
        with db.pool().connection() as conn:
            rt = runtime.load(conn)
            if settings.auto_reindex:
                n = reindex(conn, rt.signature())
                if n:
                    log.warning("search settings changed since these documents were indexed; re-indexing %d "
                                "document(s) in the background", n)
        state.embedder = runtime.embedder(rt.embed_model)
        _record_index_meta(rt)

        def warm_models():
            log.warning("Loading the embedding model %s…", rt.embed_model)
            try:
                state.embedder.warm()  # load the model before the first query (avoids a slow first search)
            except Exception as e:  # noqa: BLE001 - offline or download failed: shown in the app, Download retries
                log.warning("search model not available yet: %s", e)
            else:
                if memory_vectors(settings):  # and the vectors: on a large archive the first search would wait
                    with db.pool().connection() as conn:
                        n = vectors.index.warm(conn, state.embedder.spec.version_key)
                    log.warning("search index loaded: %d passage vectors", n)
            if rt.rerank_enabled:
                try:
                    runtime.rerank_scores(rt.rerank_model, "warm up", ["warm up"])
                except runtime.RerankError as e:
                    log.warning("reranker not available yet: %s", e)

        on_disk = embedder is not None or (runtime.downloaded("embed", rt.embed_model) and (
            not rt.rerank_enabled or runtime.downloaded("rerank", rt.rerank_model)))
        if on_disk:  # a few seconds: ready before the first request
            warm_models()
        else:  # first start: the app opens at once and shows the download; search uses keywords meanwhile
            log.warning("downloading the search models in the background; the app is usable meanwhile")
            threading.Thread(target=warm_models, daemon=True, name="warm-models").start()
        # Summaries run where indexing runs: here, or in `ferret worker` (KS_INPROCESS_WORKERS=0).
        state.enricher = enrich.Enricher(settings) if start_workers else None
        if state.enricher and settings.inprocess_workers > 0:
            if state.enricher.available:
                log.warning("document summaries: local language model %s (runs while indexing is idle)",
                            state.enricher.model or state.enricher.url)
            t = threading.Thread(target=state.enricher.loop, args=(state.stop,), name="enrich", daemon=True)
            t.start()
            state.threads.append(t)
        elif state.enricher:
            state.enricher.state = "runs in the worker process"
        if start_workers:
            for i in range(settings.inprocess_workers):
                w = Worker(state.blobs, settings.max_pdf_pages, settings.max_extracted_chars)
                t = threading.Thread(
                    target=w.loop, args=(state.stop, settings.worker_poll_seconds), name=f"ingest-{i}", daemon=True
                )
                t.start()
                state.threads.append(t)
        yield
        state.stop.set()
        if state.enricher:  # even mid-request: the model server must not outlive the app
            state.enricher.stop()
        for t in state.threads:
            t.join(timeout=10)
        db.close_pool()
        if not settings.database_url:
            db.release_embedded()

    app = FastAPI(title="Ferret", version="1.0.0", lifespan=lifespan)

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

    @app.middleware("http")
    async def activity(request: Request, call_next):  # trace.py: the admin's Activity view
        path = request.url.path
        if not (path.startswith("/api/") or path == "/mcp") or path.startswith("/api/admin/activity"):
            return await call_next(request)
        t0, writes = time.perf_counter(), request.method not in ("GET", "HEAD")
        ctype = request.headers.get("content-type", "")
        body = None
        if writes and ctype.startswith("application/json"):
            raw = await request.body()
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw[:2000].decode("utf-8", "replace")
        elif writes and ctype.startswith("multipart/"):
            body = f"(file upload, {request.headers.get('content-length', '?')} bytes; files are not kept here)"
        cid = trace.reserve()
        token = trace.current.set(cid)
        try:
            resp = await call_next(request)
        finally:
            trace.current.reset(token)
        ms = (time.perf_counter() - t0) * 1000
        if not (writes or resp.status_code >= 400 or ms > 1000):
            return resp
        out = None
        if resp.headers.get("content-type", "").startswith("application/json") and (writes or resp.status_code >= 400):
            raw = b"".join([part async for part in resp.body_iterator])
            resp = Response(raw, status_code=resp.status_code, headers=dict(resp.headers))
            try:
                out = json.loads(raw)
            except ValueError:
                out = raw[:2000].decode("utf-8", "replace")
        title, kind, steps = f"{request.method} {path}", "http", None
        if path == "/mcp" and isinstance(body, dict):
            kind = "mcp"
            title = "MCP " + (f"{body['params'].get('name', '')}" if body.get("method") == "tools/call"
                              and isinstance(body.get("params"), dict) else str(body.get("method", "")))
        elif path == "/api/search" and isinstance(body, dict):
            title = f"Search “{str(body.get('query', ''))[:80]}”"
            if isinstance(out, dict):
                steps = [(k, v) for k, v in (out.get("timings_ms") or {}).items() if k != "total"]
        user = getattr(request.state, "trace_user", "") or trace.child_user(cid)
        if not user and path == "/api/auth/login" and isinstance(body, dict):
            user = str(body.get("username", ""))[:80]
        trace.record(kind, title, ms=ms, user=user, cid=cid,
                     status="ok" if resp.status_code < 400 else f"HTTP {resp.status_code}",
                     request=body, response=out, steps=steps, detail=f"HTTP {resp.status_code}")
        return resp

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(psycopg_pool.PoolTimeout)
    async def database_unavailable(request: Request, exc: Exception):
        log.error("database unavailable: %s", type(exc).__name__)
        body = {"detail": "The database is temporarily unavailable. Try again in a moment."}
        if request.url.path == "/api/search":
            body = {"outcome": RETRIEVAL_FAILURE, "message": MESSAGES[RETRIEVAL_FAILURE], "results": []}
        return JSONResponse(status_code=503, content=body)

    _routes(app)
    app.include_router(admin.router)
    app.include_router(dbview.router)
    app.include_router(folders.router)
    app.include_router(crawls.router)
    app.include_router(graph.router)
    app.include_router(mcp.router)
    static = resources.files("knowledge_search") / "static"
    app.mount("/", StaticFiles(directory=str(static), html=True), name="ui")
    return app


def _record_index_meta(rt: "runtime.Runtime") -> None:
    with db.pool().connection() as conn:
        for k, v in {
            "embedding_model": rt.embed_model,
            "index_signature": rt.signature(),
            "parser_version": PARSER_VERSION,
            "chunker_version": CHUNKER_VERSION,
        }.items():
            conn.execute(
                "INSERT INTO app_meta(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (k, v),
            )
        conn.commit()


class TokenBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    days: int = Field(default=90, ge=1, le=365)
    allow_changes: bool = False  # else read-only: search and read, nothing else


class TokenAccessBody(BaseModel):
    allow_changes: bool


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class SetupBody(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9_.-]{2,64}$")
    password: str = Field(min_length=10, max_length=256)


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


def _start_session(conn, response: Response, uid: int) -> dict:
    token = auth.create_session(conn, uid, state.settings.session_hours)
    response.set_cookie(
        # lax, not strict: opening a source in a new tab, a reload or a pasted link is a top-level navigation
        # that must stay signed in; state changes still need the CSRF header (deps.current_principal)
        COOKIE, token, httponly=True, samesite="lax", secure=state.settings.cookie_secure,
        max_age=state.settings.session_hours * 3600, path="/",
    )
    return {"token": token}


def _search_model_state(rt) -> str:
    """ready | downloading | loading | error: ... (shown above the search box until the model is ready)."""
    st = runtime.embedder(rt.embed_model).state
    if st == "ready" or st.startswith("error"):
        return st
    return "loading" if runtime.downloaded("embed", rt.embed_model) else "downloading"


def _routes(app: FastAPI) -> None:
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(conn: Conn):
        conn.execute("SELECT 1")
        return {"status": "ready", "embedding_model": state.embedder.spec.name,
                "model_loaded": state.embedder._model is not None}

    @app.get("/api/setup")
    def setup_status(conn: Conn):
        return {"needs_admin": not conn.execute("SELECT 1 FROM users WHERE is_admin AND NOT disabled").fetchone()}

    @app.post("/api/setup/admin")
    def setup_admin(body: SetupBody, request: Request, response: Response, conn: Conn):
        """First-run: create the administrator. Only while no admin exists, and only from this machine."""
        if (request.client.host if request.client else "") not in LOOPBACK:
            raise HTTPException(403, "first-run setup is only allowed from the machine running the server")
        conn.execute("LOCK TABLE users IN EXCLUSIVE MODE")
        if conn.execute("SELECT 1 FROM users WHERE is_admin AND NOT disabled").fetchone():
            raise HTTPException(409, "setup already completed")
        if conn.execute("SELECT 1 FROM users WHERE username = %s", (body.username,)).fetchone():
            raise HTTPException(409, "that username is taken; choose another")
        uid = auth.create_user(conn, body.username, body.password, is_admin=True)
        auth.audit(conn, uid, "first_run_admin_created", body.username)
        out = _start_session(conn, response, uid)
        conn.commit()
        return out

    @app.post("/api/auth/login")
    def login(body: LoginBody, response: Response, conn: Conn):
        uid = auth.authenticate(conn, body.username, body.password)
        if uid is None:
            auth.audit(conn, None, "login_failed", None)
            conn.commit()
            raise HTTPException(401, "invalid username or password")
        out = _start_session(conn, response, uid)
        auth.audit(conn, uid, "login", None)
        conn.commit()
        return out

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, conn: Conn, p: User):
        token = request.cookies.get(COOKIE) or request.headers.get("authorization", "")[7:]
        auth.delete_session(conn, token)
        conn.commit()
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # Access tokens for AI assistants (MCP, skill): the user's own rights, listed and revoked by their owner.
    @app.get("/api/tokens")
    def tokens(request: Request, p: User, conn: Conn):
        rows = conn.execute("SELECT id, label, created_at, expires_at, read_only FROM sessions WHERE user_id = %s "
                            "AND label IS NOT NULL AND expires_at > now() ORDER BY created_at DESC", (p.user_id,)).fetchall()
        exe = Path(sys.executable).with_name("ferret" + (".exe" if sys.platform == "win32" else ""))
        return {"tokens": rows, "mcp_url": str(request.base_url) + "mcp", "url": str(request.base_url).rstrip("/"),
                "command": str(exe if exe.is_file() else shutil.which("ferret") or "ferret")}

    @app.post("/api/tokens")
    def create_token(body: TokenBody, request: Request, p: User, conn: Conn):
        if request.headers.get("authorization"):  # an assistant cannot mint itself more tokens
            raise HTTPException(403, "create access tokens in the app, signed in with your password")
        token = auth.create_session(conn, p.user_id, body.days * 24, body.label.strip(), not body.allow_changes)
        auth.audit(conn, p.user_id, "token_created", None, label=body.label.strip(), days=body.days,
                   allow_changes=body.allow_changes)
        conn.commit()
        return {"token": token}

    @app.patch("/api/tokens/{token_id}")
    def change_token(token_id: int, body: TokenAccessBody, request: Request, p: User, conn: Conn):
        """Switch a token between read only and read-and-make-changes, keeping the token itself."""
        if request.headers.get("authorization"):  # an assistant cannot give itself more rights
            raise HTTPException(403, "change access tokens in the app, signed in with your password")
        n = conn.execute("UPDATE sessions SET read_only = %s WHERE id = %s AND user_id = %s AND label IS NOT NULL "
                         "AND expires_at > now()", (not body.allow_changes, token_id, p.user_id)).rowcount
        if not n:
            raise HTTPException(404, "not found")
        auth.audit(conn, p.user_id, "token_changed", str(token_id), allow_changes=body.allow_changes)
        conn.commit()
        return {"ok": True}

    @app.delete("/api/tokens/{token_id}")
    def revoke_token(token_id: int, p: User, conn: Conn):
        n = conn.execute("DELETE FROM sessions WHERE id = %s AND user_id = %s AND label IS NOT NULL",
                         (token_id, p.user_id)).rowcount
        if not n:
            raise HTTPException(404, "not found")
        auth.audit(conn, p.user_id, "token_revoked", str(token_id))
        conn.commit()
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
        try:
            rt = runtime.load(conn)
            resp = search(conn, p, body.query, rt, body.collection_ids, body.limit)
        except Exception:  # noqa: BLE001 - explicit failure state, no internals leaked
            log.exception("search failed")
            try:
                conn.rollback()
            except psycopg.Error:
                pass  # connection is gone; the pool discards it
            return JSONResponse(
                status_code=503,
                content={"outcome": RETRIEVAL_FAILURE, "message": MESSAGES[RETRIEVAL_FAILURE], "results": []},
            )
        # Audit records that a search happened and its outcome — not the query text.
        auth.audit(conn, p.user_id, "search", None, outcome=resp.outcome, n=len(resp.results))
        conn.commit()
        return resp.to_dict()

    def doc_filter(p, conn, collection_id, status, q, doc_type) -> tuple[str, dict]:
        """WHERE clause and arguments for the Documents tab's filters, within the caller's readable collections."""
        a = {"scope": [c for c in p.readable if collection_id is None or c == collection_id],
             "sig": runtime.load(conn).signature(), "q": f"%{q.strip().lower()}%", "type": doc_type}
        w = "d.collection_id = ANY(%(scope)s) " + DOC_STATUS.get(status, "")
        if q.strip():
            w += f"AND lower(coalesce({SHOWN.format('title')}, '') || ' ' || d.title || ' ' || d.source_key) LIKE %(q)s "
        if doc_type:
            w += f"AND {SHOWN.format('type')} = %(type)s "
        return w, a

    @app.get("/api/documents/counts")
    def document_counts(p: User, conn: Conn, collection_id: int | None = None, status: str = "", q: str = "",
                        doc_type: str = ""):
        """Counts for the Documents tab's state tabs, the document types present, and how many rows match."""
        w, a = doc_filter(p, conn, collection_id, "", "", "")
        n = conn.execute(
            'SELECT count(*) AS "all", ' + ", ".join(f"count(*) FILTER (WHERE {c[4:]}) AS {k}" for k, c in DOC_STATUS.items())
            + f", array_remove(array_agg(DISTINCT {SHOWN.format('type')}), NULL) AS types FROM documents d WHERE " + w,
            a).fetchone()
        w, a = doc_filter(p, conn, collection_id, status, q, doc_type)
        return {**n, "types": sorted(n["types"]), "matching": conn.execute(
            "SELECT count(*) AS n FROM documents d WHERE " + w, a).fetchone()["n"]}

    @app.get("/api/documents")
    def list_documents(p: User, conn: Conn, response: Response, collection_id: int | None = None, status: str = "",
                       q: str = "", doc_type: str = "", sort: str = "updated", desc: bool = True,
                       limit: int = 1000, offset: int = 0):
        """Documents the caller can read: filtered, sorted and paged by the server (the total is in
        X-Total-Count; without `limit` at most 1,000 rows, newest first)."""
        w, a = doc_filter(p, conn, collection_id, status, q, doc_type)
        limit, offset = max(1, min(limit, 1000)), max(0, offset)
        response.headers["X-Total-Count"] = str(conn.execute("SELECT count(*) AS n FROM documents d WHERE " + w,
                                                             a).fetchone()["n"])
        order = ", ".join(x + (" DESC" if desc else "") for x in DOC_SORT.get(sort, DOC_SORT["updated"]))
        scope, sig = a["scope"], a["sig"]
        rows = conn.execute(
            "SELECT d.id, d.title, d.source_key, d.source_uri, d.status, d.error_code, d.error_detail, d.updated_at, "
            "c.name AS collection, d.collection_id, v.version_no AS latest_version, iv.version_no AS indexed_version, "
            "v.media_type, v.size_bytes, (SELECT count(*) FROM chunks ch WHERE ch.document_id = d.id) AS chunks, "
            "(d.status = 'indexed' AND d.index_signature IS DISTINCT FROM %(sig)s) AS needs_reindex, "
            "d.enrichment, d.indexed_version_id, "
            "j.stage, j.progress_done, j.progress_total, "
            "(SELECT count(*) FROM jobs q WHERE q.status = 'queued' AND q.id < coalesce(j.id, 0)) AS queue_position, "
            # being indexed again only to add its new summary to the passages (enrich: context_chars > 0)
            "(d.enrichment ? 'summary' AND d.enrichment->>'version_id' = d.indexed_version_id::text "
            " AND d.latest_version_id = d.indexed_version_id) AS adding_summary "
            "FROM documents d JOIN collections c ON c.id = d.collection_id "
            "LEFT JOIN LATERAL (SELECT id, stage, progress_done, progress_total FROM jobs "
            "  WHERE document_id = d.id AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1) j ON true "
            "LEFT JOIN document_versions v ON v.id = d.latest_version_id "
            "LEFT JOIN document_versions iv ON iv.id = d.indexed_version_id "
            f"WHERE {w} ORDER BY {order}, lower(coalesce({SHOWN.format('title')}, d.title)), d.id "
            "LIMIT %(limit)s OFFSET %(offset)s",
            a | {"limit": limit, "offset": offset},
        ).fetchall()
        summary = {}  # indexed documents: waiting / writing / done / failed, while summaries are on
        e = getattr(state, "enricher", None)
        if e and e.available and runtime.load(conn).enrich_enabled:
            summary = {r["id"]: r["s"] for r in conn.execute(
                "SELECT d.id, " + enrich.STAGE + " AS s FROM documents d WHERE d.id = ANY(%(ids)s) "
                "AND d.status = 'indexed' AND d.indexed_version_id IS NOT NULL",
                enrich._args(e.model, ids=[r["id"] for r in rows]))}
        out = []
        for r in rows:
            r = dict(r, id=str(r["id"]), can_write=r["collection_id"] in p.writable, summary_stage=summary.get(r["id"]))
            shown = enrich.shown(r.pop("enrichment"), r.pop("indexed_version_id"))
            out.append(r | {"summary_text": shown["summary"], "topics": shown["topics"], "clearer_title": shown["clearer_title"],
                            "doc_type": shown["doc_type"]})
        return out

    def summary_model(conn) -> str | None:
        """The local model writing summaries now, or None when summaries are off or no model is available."""
        e = getattr(state, "enricher", None)
        return e.model if e and e.available and runtime.load(conn).enrich_enabled else None

    @app.get("/api/summaries/status")
    def summaries_status(p: User, conn: Conn):
        """The Summaries tab's tiles and per-collection table, by the worker's own rules, readable collections only."""
        rt, model, scope = runtime.load(conn), summary_model(conn), list(p.readable)
        out = {"line": enrich.describe(getattr(state, "enricher", None), conn, rt.enrich_enabled, scope),
               "model": model, "hours": rt.enrich_hours, "in_hours": enrich.in_hours(rt.enrich_hours, time.localtime().tm_hour),
               "collections": [], "seconds_per_document": None}
        if model is not None:
            out["collections"] = enrich.by_collection(conn, model, scope)
            out["seconds_per_document"] = conn.execute(  # the pace so far, for an estimate of the time left
                "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY (enrichment->>'seconds')::float) AS s FROM documents "
                "WHERE collection_id = ANY(%s) AND enrichment->>'model' = %s AND enrichment ? 'seconds'",
                (scope, model)).fetchone()["s"]
        return out

    @app.get("/api/summaries")
    def summaries(p: User, conn: Conn, stage: str = "done", q: str = "", topic: str = "", limit: int = 100,
                  offset: int = 0):
        """Indexed documents at one summary stage (writing / failed / done), filtered by name or topic, paged."""
        model = summary_model(conn)
        a = enrich._args(model or "", cols=list(p.readable), stage=stage, q=f"%{q.strip().lower()}%")
        w = "d.status = 'indexed' AND d.indexed_version_id IS NOT NULL AND d.collection_id = ANY(%(cols)s) "
        if model is not None:
            w += f"AND ({enrich.STAGE}) = %(stage)s "
        elif stage == "done":  # no model running: the summaries already written for the indexed version
            w += f"AND {SHOWN.format('summary')} IS NOT NULL "
        else:
            return {"rows": [], "total": 0}
        if q.strip():
            w += (f"AND lower(coalesce({SHOWN.format('title')}, '') || ' ' || d.title || ' ' || d.source_key || ' ' "
                  f"|| coalesce({SHOWN.format('topics')}, '')) LIKE %(q)s ")
        if topic:  # the same spelling-insensitive key as the topics themselves
            key = enrich.topic_key(topic)
            a["ids"] = [r["id"] for r in conn.execute("SELECT d.id, d.enrichment->'topics' AS t FROM documents d WHERE " + w, a)
                        if any(enrich.topic_key(x) == key for x in r["t"] or [])]
            w += "AND d.id = ANY(%(ids)s) "
        total = conn.execute("SELECT count(*) AS n FROM documents d WHERE " + w, a).fetchone()["n"]
        rows = conn.execute(
            "SELECT d.id, d.title, d.enrichment, d.indexed_version_id, c.name AS collection, v.media_type "
            "FROM documents d JOIN collections c ON c.id = d.collection_id "
            "LEFT JOIN document_versions v ON v.id = d.indexed_version_id WHERE " + w
            + f"ORDER BY lower(coalesce({SHOWN.format('title')}, d.title)), d.id LIMIT %(limit)s OFFSET %(offset)s",
            a | {"limit": max(1, min(limit, 1000)), "offset": max(0, offset)}).fetchall()
        out = []
        for r in rows:
            shown = enrich.shown(r["enrichment"], r["indexed_version_id"])
            out.append({"id": str(r["id"]), "title": r["title"], "collection": r["collection"], "media_type": r["media_type"],
                        "summary_stage": stage, "summary_text": shown["summary"], "topics": shown["topics"],
                        "clearer_title": shown["clearer_title"], "doc_type": shown["doc_type"]})
        return {"rows": out, "total": total}

    @app.post("/api/documents", status_code=202)
    async def upload(
        p: User,
        conn: Conn,
        file: UploadFile = File(...),
        collection_id: int = Form(...),
        source_uri: str | None = Form(None),
        path: str | None = Form(None),
    ):
        """One file; `path` is its place in an uploaded folder (kit/docs/a.md), which then identifies it."""
        if collection_id not in p.writable:
            raise HTTPException(404, "collection not found")
        max_bytes = state.settings.max_upload_mb * 1024 * 1024
        data = await file.read(max_bytes + 1)
        try:
            res = submit(
                conn, state.blobs, collection_id=collection_id, filename=file.filename or "", data=data,
                user_id=p.user_id, max_bytes=max_bytes, source_uri=(source_uri or None),
                source_key=path_key(path) if path else None,
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

    def _original(conn, p: Principal, document_id: UUID, version: str, audit: bool = True):
        """(version row, stored file) of the indexed (or latest) version; audited as opened."""
        d = _doc_for(conn, p, document_id)
        vid = d["indexed_version_id"] if version == "indexed" and d["indexed_version_id"] else d["latest_version_id"]
        v = conn.execute("SELECT sha256, filename, media_type FROM document_versions WHERE id = %s", (vid,)).fetchone()
        if not v:
            raise HTTPException(404, "not found")
        path = state.blobs.path(v["sha256"])
        if not path.exists():
            raise HTTPException(410, "original file is missing from storage")
        if audit:
            auth.audit(conn, p.user_id, "open_source", str(document_id))
            conn.commit()
        return v, path

    def _viewer_response(page_html: str) -> Response:
        return Response(page_html, media_type="text/html; charset=utf-8", headers={
            # no scripts at all; styles inline; links work, so does the download link (no sandbox)
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Cache-Control": "private, no-store"})

    @app.get("/api/documents/{document_id}/view")
    def view(document_id: UUID, p: User, conn: Conn, version: str = "indexed", text: bool = False):
        """The document readable in the browser: PDF and HTML as themselves; PowerPoint as its slides (or
        `text=1`: as text); Markdown, text and Word rendered as a page (raw HTML escaped, nothing can run)."""
        v, path = _original(conn, p, document_id, version, audit=False)
        source = f"/api/documents/{document_id}/source?version={version}"
        download = f"/api/documents/{document_id}/source?download=1&version={version}"
        if v["media_type"] == "text/html" or v["media_type"].startswith("image/"):  # the browser shows these itself
            return RedirectResponse(source, status_code=303)
        if v["media_type"] == "application/pdf":  # PDF.js page; it fetches /source, which audits the opening
            from .viewer import pdf_page

            return Response(pdf_page(v["filename"], source, download), media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "private, no-store", "Content-Security-Policy": PDF_VIEWER_CSP})
        auth.audit(conn, p.user_id, "open_source", str(document_id))
        conn.commit()
        from .convert import docx_to_markdown, pptx_to_markdown
        from .viewer import page

        data = path.read_bytes()
        here = f"/api/documents/{document_id}/view?version={version}"
        if v["media_type"] == PPTX and not text:
            from .slides import render
            from .viewer import slides_page

            try:
                return _viewer_response(slides_page(v["filename"], render(data), download, here + "&text=1"))
            except Exception:  # noqa: BLE001 - an unusual deck still opens as text
                log.exception("drawing the slides failed; showing the text")
        if v["media_type"] == DOCX:
            body = docx_to_markdown(data)[1]
        elif v["media_type"] == PPTX:
            body = pptx_to_markdown(data)[1]
        else:
            body = data.decode("utf-8", "replace")
        links = {r["target"]: str(r["id"]) for r in conn.execute(  # links to documents this user can read
            "SELECT DISTINCT ON (l.target) l.target, d.id FROM doc_links l JOIN documents d ON "
            + graph._resolves("d", "l") + " AND d.collection_id = ANY(%s) AND d.id <> l.document_id "
            "WHERE l.document_id = %s ORDER BY l.target, d.updated_at DESC", (list(p.readable), document_id))}
        return _viewer_response(page(v["filename"], body, plain=v["media_type"] == "text/plain", links=links,
                                     download=download,
                                     other_view=("Slide view", here) if v["media_type"] == PPTX else None))

    @app.get("/api/documents/{document_id}/source")
    def source(document_id: UUID, p: User, conn: Conn, version: str = "indexed", download: bool = False):
        v, path = _original(conn, p, document_id, version)
        # Uploaded content is sandboxed (opaque origin, no scripts). HTML is rendered with nothing allowed to
        # load except inline styles and embedded images; Word and PowerPoint files are downloaded. PDFs get no
        # sandbox, only the app's own policy: Chrome's PDF viewer refuses sandboxed documents
        # (ERR_BLOCKED_BY_CLIENT); nosniff keeps the browser from treating the file as anything but a PDF.
        mt, disposition, csp = "text/plain; charset=utf-8", "inline", "sandbox"
        if v["media_type"] == "application/pdf":
            mt, csp = "application/pdf", None
        elif v["media_type"] == "text/html":
            mt, csp = "text/html", "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        elif v["media_type"] in (DOCX, PPTX):
            mt, disposition = v["media_type"], "attachment"
        elif v["media_type"].startswith("image/"):  # PNG, JPEG, GIF, WebP only (no SVG: it can carry scripts)
            mt = v["media_type"]
        if download:
            mt, disposition = v["media_type"], "attachment"
        safe = re.sub(r"[^\w.\-]", "_", v["filename"])
        return FileResponse(
            path, media_type=mt,
            headers={"Content-Disposition": f'{disposition}; filename="{safe}"', "X-Content-Type-Options": "nosniff",
                     "Cache-Control": "private, no-store"} | ({"Content-Security-Policy": csp} if csp else {}),
        )

    @app.get("/api/documents/{document_id}/text")
    def document_text(document_id: UUID, p: User, conn: Conn):
        """The indexed version's passages in reading order (for tools that read a whole document)."""
        d = _doc_for(conn, p, document_id)
        auth.audit(conn, p.user_id, "open_source", str(document_id))
        rows = conn.execute("SELECT id AS chunk_id, heading_path, page, text FROM chunks WHERE version_id = %s "
                            "ORDER BY ordinal", (d["indexed_version_id"],)).fetchall() if d["indexed_version_id"] else []
        conn.commit()
        return {"document_id": str(d["id"]), "title": d["title"], "collection": d["collection"],
                "source_key": d["source_key"], "source_uri": d["source_uri"], "status": d["status"],
                **enrich.shown(d["enrichment"], d["indexed_version_id"]), "passages": rows}

    @app.get("/api/chunks/{chunk_id}")
    def passage(chunk_id: int, p: User, conn: Conn):
        c = conn.execute(
            "SELECT c.id, c.document_id, c.ordinal, c.heading_path, c.page, c.anchor, c.text, c.doc_title, "
            "c.version_id, col.name AS collection, v.version_no, v.media_type, d.source_uri, d.enrichment, "
            "d.indexed_version_id "
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
        c = dict(c)
        written = enrich.shown(c.pop("enrichment"), c.pop("indexed_version_id"))
        return {**c, **written, "document_id": str(c["document_id"]), "context": ctx}

    @app.get("/api/status")
    def status(p: User, conn: Conn):
        """Index health for the caller's own scope (no information about other collections)."""
        rt = runtime.load(conn)
        cov = coverage_for(conn, list(p.readable), rt.signature())
        per = conn.execute(
            "SELECT c.id, c.name, count(d.id) AS documents, "
            "count(d.id) FILTER (WHERE d.status = 'indexed') AS indexed, "
            "count(d.id) FILTER (WHERE d.status IN ('pending','processing')) AS pending, "
            "count(d.id) FILTER (WHERE d.status = 'failed') AS failed "
            "FROM collections c LEFT JOIN documents d ON d.collection_id = c.id "
            "WHERE c.id = ANY(%s) GROUP BY c.id ORDER BY c.name",
            (list(p.readable),),
        ).fetchall()
        index = {"embedding_model": rt.embed_model, "chunk_size": rt.chunk_size, "chunk_overlap": rt.chunk_overlap,
                 "reranker": rt.rerank_model if rt.rerank_enabled else "off",
                 "evidence_gate": rt.gate_mode if rt.rerank_enabled else "similarity",
                 "parser_version": PARSER_VERSION}
        index["summaries_by_local_model"] = enrich.describe(getattr(state, "enricher", None), conn, rt.enrich_enabled,
                                                            list(p.readable))
        out = {"coverage": cov, "collections": per, "index": index}
        out["search_model"] = _search_model_state(rt)
        if p.is_admin:
            out["models"] = runtime.model_states()
        return out
