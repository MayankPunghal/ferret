"""Shared request dependencies: process state, DB connection, authenticated principal."""

import threading
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from . import auth, db
from .auth import Principal
from .config import Settings
from .embedder import Embedder
from .ingest import BlobStore

COOKIE = "ks_session"
CSRF_HEADER = "x-ks-csrf"


class AppState:
    settings: Settings
    embedder: Embedder  # model loaded at startup (tests patch this object)
    blobs: BlobStore
    stop: threading.Event
    threads: list[threading.Thread]


state = AppState()


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


Admin = Annotated[Principal, Depends(require_admin)]
