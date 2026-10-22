"""Command line: migrations, user/permission administration, bulk ingest, server and worker."""

import argparse
import getpass
import logging
import secrets
import signal
import sys
import threading
from pathlib import Path

from . import auth, db
from .config import get_settings


def _conn():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="knowledge-search")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    p = sub.add_parser("create-user")
    p.add_argument("username")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--password", help="omit to be prompted; use '-' to generate one")
    p = sub.add_parser("add-to-group")
    p.add_argument("username")
    p.add_argument("group")
    p = sub.add_parser("remove-from-group")
    p.add_argument("username")
    p.add_argument("group")
    p = sub.add_parser("create-collection")
    p.add_argument("name")
    p.add_argument("--description", default="")
    p = sub.add_parser("grant", help="grant a group read (or write) access to a collection")
    p.add_argument("collection")
    p.add_argument("group")
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("ingest", help="queue files from a directory (idempotent)")
    p.add_argument("collection")
    p.add_argument("path", type=Path)
    p.add_argument("--wait", action="store_true", help="process the queue in this process before exiting")
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="run ingestion workers as a separate process")
    sub.add_parser("download-model", help="fetch the configured embedding model into KS_MODEL_CACHE")
    a = ap.parse_args(argv)
    s = get_settings()

    if a.cmd == "migrate":
        print("applied:", db.migrate(s.database_url) or "nothing (up to date)")
    elif a.cmd == "create-user":
        pw = a.password
        if pw == "-":
            pw = secrets.token_urlsafe(12)
            print(f"generated password for {a.username}: {pw}")
        elif not pw:
            pw = getpass.getpass("password: ")
        with _conn() as c:
            auth.create_user(c, a.username, pw, a.admin)
        print("created", a.username)
    elif a.cmd in ("add-to-group", "remove-from-group"):
        with _conn() as c:
            (auth.add_user_to_group if a.cmd == "add-to-group" else auth.remove_user_from_group)(c, a.username, a.group)
            c.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = %s)", (a.username,))
        print("ok (user sessions revoked so new permissions apply at next login)")
    elif a.cmd == "create-collection":
        with _conn() as c:
            auth.ensure_collection(c, a.name, a.description)
        print("ok")
    elif a.cmd == "grant":
        with _conn() as c:
            auth.grant(c, a.collection, a.group, a.write)
        print("ok")
    elif a.cmd == "ingest":
        _ingest(a, s)
    elif a.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(), host=a.host, port=a.port, proxy_headers=True)
    elif a.cmd == "worker":
        _worker(s)
    elif a.cmd == "download-model":
        from .embedder import Embedder

        Embedder(s.embed_model, str(s.model_cache)).warm()
        print("model ready in", s.model_cache)


def _ingest(a, s) -> None:
    from .ingest import BlobStore, submit
    from .parsing import MEDIA_TYPES, ParseError

    blobs = BlobStore(s.data_dir)
    files = [a.path] if a.path.is_file() else sorted(f for f in a.path.rglob("*") if f.suffix.lower() in MEDIA_TYPES)
    with _conn() as c:
        cid = auth.ensure_collection(c, a.collection)
        c.commit()
        for f in files:
            try:
                r = submit(c, blobs, collection_id=cid, filename=f.name, data=f.read_bytes(), user_id=None,
                           max_bytes=s.max_upload_mb * 1024 * 1024, source_uri=f"file://{f.resolve()}")
                print(f"{r.outcome:9} {f}")
            except ParseError as e:
                print(f"rejected  {f}: {e.code}")
            c.commit()
    if a.wait:
        from .embedder import Embedder
        from .ingest import Worker

        db.open_pool(s.database_url, max_size=2)
        n = Worker(Embedder(s.embed_model, str(s.model_cache), s.embed_threads, s.offline), blobs,
                   s.max_pdf_pages, s.max_extracted_chars).drain()
        db.close_pool()
        print(f"processed {n} job(s)")


def _worker(s) -> None:
    from .embedder import Embedder
    from .ingest import BlobStore, Worker

    db.open_pool(s.database_url, max_size=2)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    w = Worker(Embedder(s.embed_model, str(s.model_cache), s.embed_threads, s.offline), BlobStore(s.data_dir),
               s.max_pdf_pages, s.max_extracted_chars)
    w.loop(stop, s.worker_poll_seconds)
    db.close_pool()
    sys.exit(0)
