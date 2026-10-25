"""Command line: migrations, user/permission administration, bulk ingest, server and worker."""

import argparse
import getpass
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
    from . import logs

    logs.install(get_settings().data_dir)
    ap = argparse.ArgumentParser(prog="ferret")
    sub = ap.add_subparsers(dest="cmd")  # none: start the app and open it in the browser
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
    p = sub.add_parser("crawl", help="save a website as Markdown files, one per page (site2md; see site2md --help)")
    p.add_argument("args", nargs=argparse.REMAINDER, help="URL --out DIR [options]")
    sub.add_parser("demo", help="load the FICTIONAL sample corpus with demo users alice/bob/hannah (idempotent)")
    sub.add_parser("worker", help="run ingestion workers as a separate process")
    sub.add_parser("mcp", help="MCP over stdin/stdout for AI assistants that start a local command (Claude Desktop); "
                   "needs KS_TOKEN (the app's AI assistants page) and KS_URL")
    p = sub.add_parser("download-model", help="fetch the configured embedding model and reranker into KS_MODEL_CACHE")
    p.add_argument("--model", help="a specific embedding model (default: KS_EMBED_MODEL)")
    p.add_argument("--reranker", help="a specific reranker (default: KS_RERANK_MODEL)")
    p = sub.add_parser("download-llm", help="optional: fetch a local language model (and llama.cpp's server) for "
                       "document summaries; nothing is sent anywhere")
    p.add_argument("--model", default=None, help="gemma-4-e2b (default, ~3.1 GB), gemma-4-e4b (~5 GB, servers), qwen3.5-2b (~1.3 GB) or qwen3.5-0.8b (~0.5 GB, faster, less accurate)")
    a = ap.parse_args(argv)
    if a.cmd is None:
        a = ap.parse_args(["serve", "--open"])
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
    elif a.cmd == "crawl":
        import site2md

        site2md.main(a.args)
    elif a.cmd == "ingest":
        _ingest(a, s)
    elif a.cmd == "serve":
        import uvicorn

        from .api import create_app

        print("Starting Ferret - loading the AI model (the first start can take a minute).\n"
              "Keep this window open while you use the app; close it to stop.", flush=True)
        _announce_when_ready(f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{a.port}", a.open)
        if a.workers > 1:
            uvicorn.run("knowledge_search.api:create_app", factory=True, host=a.host, port=a.port,
                        proxy_headers=True, workers=a.workers, access_log=False, log_config=None)
        else:
            uvicorn.run(create_app(), host=a.host, port=a.port, proxy_headers=True, access_log=False, log_config=None)
    elif a.cmd == "demo":
        _demo(s)
    elif a.cmd == "worker":
        _worker(s)
    elif a.cmd == "mcp":
        from .mcp import bridge_main

        bridge_main()
    elif a.cmd == "download-llm":
        from . import enrich

        name = a.model or enrich.DEFAULT_LLM
        if name not in enrich.LLMS:
            sys.exit(f"unknown model {name}; choose one of: {', '.join(enrich.LLMS)}")
        try:
            path = enrich.download(s, name)
        except Exception as e:  # noqa: BLE001 - network/disk errors from urllib or huggingface_hub
            sys.exit(f"Download failed: {str(e)[:300]}\nRun the same command again to retry.")
        print("local language model ready:", path, "\nRestart the app; summaries are written in the background.")
    elif a.cmd == "download-model":
        from . import runtime

        models = [a.model] if a.model else [s.embed_model]
        rerankers = [a.reranker] if a.reranker else ([s.rerank_model] if not a.model else [])
        from .embedder import EmbeddingError

        try:
            for m in models:
                runtime.embedder(m).warm()
                print("embedding model ready:", m)
            for r in rerankers:
                runtime.rerank_scores(r, "warm up", ["warm up"])
                print("reranker ready:", r)
        except (EmbeddingError, runtime.RerankError) as e:
            sys.exit(f"Model download failed: {str(e).splitlines()[0][:300]}\n"
                     "Completed downloads are kept; run the same command (or setup) again.")
        print("model cache:", s.model_cache)


def _announce_when_ready(url: str, open_browser: bool) -> None:
    """Print a clear 'ready' line (and open the browser) once the server answers health checks."""
    import time
    import urllib.request
    import webbrowser

    def run():
        for _ in range(600):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1)
                print(f"\nReady: {url}" + (" (opening your browser)" if open_browser else ""), flush=True)
                if open_browser:
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
        for col, groups in {"engineering": ["staff", "hr"], "finance": ["staff", "hr"], "hr": ["hr"],
                            "platform-wiki": ["staff", "hr"]}.items():
            for g in groups:
                auth.grant(c, col, g)
        auth.grant(c, "engineering", "eng-writers", can_write=True)
        for u, groups in DEMO_USERS.items():
            if not c.execute("SELECT 1 FROM users WHERE username = %s", (u,)).fetchone():
                auth.create_user(c, u, f"demo-password-{u}")
            for g in groups:
                auth.add_user_to_group(c, u, g)
    for col in ("engineering", "finance", "hr", "platform-wiki"):
        _ingest(argparse.Namespace(collection=col, path=corpus / col, wait=True), s)
    print("\nDemo users (password = demo-password-<name>): alice (engineering+finance, can upload), "
          "bob (read-only), hannah (also sees the confidential 'hr' collection)")


def _ingest(a, s) -> None:
    from .ingest import BlobStore, folder_files, submit
    from .parsing import MEDIA_TYPES, ParseError

    blobs = BlobStore(s.data_dir)
    root = a.path.resolve()
    # a folder: its subfolders too, without names starting with . or _; each file keyed by its path in the folder
    files = [root] if root.is_file() else folder_files(root, set(MEDIA_TYPES))[0]
    with _conn() as c:
        cid = auth.ensure_collection(c, a.collection)
        c.commit()
        for f in files:
            try:
                r = submit(c, blobs, collection_id=cid, filename=f.name, data=f.read_bytes(), user_id=None,
                           max_bytes=s.max_upload_mb * 1024 * 1024, source_uri=f.resolve().as_uri(),
                           source_key=None if f == root else f"{root.name}/{f.relative_to(root).as_posix()}")
                print(f"{r.outcome:9} {f}")
            except ParseError as e:
                print(f"rejected  {f}: {e.code}")
            c.commit()
    if a.wait:
        from .ingest import Worker

        db.open_pool(db.resolve_url(s), max_size=2)
        n = Worker(blobs, s.max_pdf_pages, s.max_extracted_chars).drain()
        db.close_pool()
        print(f"processed {n} job(s)")


def _worker(s) -> None:
    from .enrich import Enricher
    from .ingest import BlobStore, Worker

    db.open_pool(db.resolve_url(s), max_size=3)
    from . import runtime

    with db.pool().connection() as c:  # load the embedding model now, not with the first document
        runtime.embedder(runtime.load(c).embed_model).warm()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    enricher = Enricher(s)  # document summaries as documents are indexed (optional local model)
    t = threading.Thread(target=enricher.loop, args=(stop,), name="enrich", daemon=True)
    t.start()
    w = Worker(BlobStore(s.data_dir), s.max_pdf_pages, s.max_extracted_chars)
    w.loop(stop, s.worker_poll_seconds)
    enricher.stop()
    t.join(timeout=10)
    db.close_pool()
    sys.exit(0)
