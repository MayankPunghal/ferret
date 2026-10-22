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

    return psycopg.connect(db.resolve_url(get_settings()), row_factory=dict_row)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("pixeltable_pgserver", "httpx", "huggingface_hub"):  # embedded DB and download chatter
        logging.getLogger(noisy).setLevel(logging.WARNING)
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
    p.add_argument("--workers", type=int, default=1, help="HTTP worker processes (each loads the model)")
    p.add_argument("--open", action="store_true", help="open the web UI in the default browser once ready")
    sub.add_parser("demo", help="load the FICTIONAL sample corpus with demo users alice/bob/hannah (idempotent)")
    sub.add_parser("worker", help="run ingestion workers as a separate process")
    p = sub.add_parser("download-model", help="fetch the configured embedding model and reranker into KS_MODEL_CACHE")
    p.add_argument("--model", help="a specific embedding model (default: KS_EMBED_MODEL)")
    p.add_argument("--reranker", help="a specific reranker (default: KS_RERANK_MODEL)")
    a = ap.parse_args(argv)
    s = get_settings()

    if a.cmd == "migrate":
        print("applied:", db.migrate(db.resolve_url(s)) or "nothing (up to date)")
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

        if a.open:
            _open_browser_when_ready(f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{a.port}")
        if a.workers > 1:
            uvicorn.run("knowledge_search.api:create_app", factory=True, host=a.host, port=a.port,
                        proxy_headers=True, workers=a.workers)
        else:
            uvicorn.run(create_app(), host=a.host, port=a.port, proxy_headers=True)
    elif a.cmd == "demo":
        _demo(s)
    elif a.cmd == "worker":
        _worker(s)
    elif a.cmd == "download-model":
        from . import runtime

        models = [a.model] if a.model else [s.embed_model]
        rerankers = [a.reranker] if a.reranker else ([s.rerank_model] if not a.model else [])
        for m in models:
            runtime.embedder(m).warm()
            print("embedding model ready:", m)
        for r in rerankers:
            runtime.rerank_scores(r, "warm up", ["warm up"])
            print("reranker ready:", r)
        print("model cache:", s.model_cache)


def _open_browser_when_ready(url: str) -> None:
    import time
    import urllib.request
    import webbrowser

    def run():
        for _ in range(300):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1)
                webbrowser.open(url)
                return
            except OSError:
                time.sleep(1)

    threading.Thread(target=run, daemon=True).start()


DEMO_USERS = {"alice": ["staff", "eng-writers"], "bob": ["staff"], "hannah": ["hr"]}


def _demo(s) -> None:
    """Fictional sample data: three collections with different access, three demo users."""
    corpus = Path(__file__).resolve().parents[2] / "fixtures" / "corpus"
    if not corpus.is_dir():
        sys.exit(f"sample corpus not found at {corpus} (run from a source checkout)")
    with _conn() as c:
        for col, groups in {"engineering": ["staff", "hr"], "finance": ["staff", "hr"], "hr": ["hr"]}.items():
            for g in groups:
                auth.grant(c, col, g)
        auth.grant(c, "engineering", "eng-writers", can_write=True)
        for u, groups in DEMO_USERS.items():
            if not c.execute("SELECT 1 FROM users WHERE username = %s", (u,)).fetchone():
                auth.create_user(c, u, f"demo-password-{u}")
            for g in groups:
                auth.add_user_to_group(c, u, g)
    for col in ("engineering", "finance", "hr"):
        _ingest(argparse.Namespace(collection=col, path=corpus / col, wait=True), s)
    print("\nDemo users (password = demo-password-<name>): alice (engineering+finance, can upload), "
          "bob (read-only), hannah (also sees the confidential 'hr' collection)")


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

        db.open_pool(db.resolve_url(s), max_size=2)
        n = Worker(blobs, s.max_pdf_pages, s.max_extracted_chars).drain()
        db.close_pool()
        print(f"processed {n} job(s)")


def _worker(s) -> None:
    from .embedder import Embedder
    from .ingest import BlobStore, Worker

    db.open_pool(db.resolve_url(s), max_size=2)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    w = Worker(BlobStore(s.data_dir), s.max_pdf_pages, s.max_extracted_chars)
    w.loop(stop, s.worker_poll_seconds)
    db.close_pool()
    sys.exit(0)
