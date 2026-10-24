"""Administrator: copy a website into Markdown (site2md) in the background and keep it as a synced folder.

The crawler runs as a separate process on the computer running Knowledge Search, into
``<data_dir>/crawls/<name>.new``. When it finishes, that folder replaces ``<data_dir>/crawls/<name>`` and is
synced into the chosen collection (folders.py), so crawling the same site again takes in only the pages that
changed and removes pages that are gone. Sites behind a sign-in: sign in once on this computer with
``knowledge-search crawl <url> --out <any folder> --browser --login``; ``browser: true`` then reuses that
sign-in. The app never asks for passwords.
"""

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth, db, folders
from .deps import Admin, Conn, state

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/crawls", tags=["admin"])
_jobs: dict[str, dict] = {}  # name -> state of a crawl started since the app started
_lock = threading.Lock()


class CrawlBody(BaseModel):
    url: str = Field(pattern=r"^https?://\S+$", max_length=2000)
    collection_id: int
    max_pages: int = Field(default=1000, ge=1, le=10_000)
    browser: bool = False  # fetch with a real browser: JavaScript sites, and sign-ins saved on this computer


def name_for(url: str) -> str:
    """'https://wiki.example.com/eng/start' -> 'wiki-example-com-eng-start' (the folder, and its documents' prefix)."""
    u = urlsplit(url)
    return re.sub(r"[^a-z0-9]+", "-", f"{u.hostname or ''}{u.path}".lower()).strip("-")[:80] or "site"


def _dir() -> Path:
    return Path(state.settings.data_dir) / "crawls"


def _summary(out: Path) -> dict:
    try:
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {"pages": len(m.get("pages", {})), "files": len(m.get("files", {})), "failed": len(m.get("failed", {})),
            "skipped": len(m.get("skipped", {})), "examples_failed": dict(list(m.get("failed", {}).items())[:5])}


def _run(name: str, body: CrawlBody, user_id: int) -> None:
    job, out = _jobs[name], _dir() / name
    new, logf = out.with_name(name + ".new"), out.with_name(name + ".log")
    shutil.rmtree(new, ignore_errors=True)
    cmd = [sys.executable, "-m", "site2md", body.url, "--out", str(new), "--max-pages", str(body.max_pages)]
    if body.browser:
        cmd.append("--browser")
    try:
        with open(logf, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            job["pid"] = proc.pid
            while proc.poll() is None:
                time.sleep(2)
                job["progress"] = _last_line(logf)
        if not (new / "manifest.json").exists():
            job.update(state="failed", error=_last_line(logf) or f"the crawler stopped (exit code {proc.returncode})")
            return
        shutil.rmtree(out, ignore_errors=True)
        new.rename(out)
        job.update(state="syncing", **_summary(out))
        with db.pool().connection() as conn:
            fid = conn.execute(
                "INSERT INTO folder_sources(collection_id, path, types, created_by) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (collection_id, path) DO UPDATE SET types = EXCLUDED.types RETURNING id",
                (body.collection_id, str(out.resolve()), list(folders.TYPES), user_id)).fetchone()["id"]
            conn.commit()
        job["folder_id"] = fid
        folders._running.add(fid)
        try:
            job["sync"] = folders.sync(fid, user_id)
        finally:
            folders._running.discard(fid)
        job["state"] = "done"
    except Exception as e:
        log.exception("crawl failed %s", name)
        job.update(state="failed", error=str(e)[:300])
    finally:
        job["finished"] = time.time()


def _last_line(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return lines[-1][:300] if lines else ""


@router.post("", status_code=202)
def start(body: CrawlBody, p: Admin, conn: Conn):
    """Copy a website into a collection; crawling the same address again updates it."""
    if not conn.execute("SELECT 1 FROM collections WHERE id = %s", (body.collection_id,)).fetchone():
        raise HTTPException(404, "collection not found")
    name = name_for(body.url)
    with _lock:
        if _jobs.get(name, {}).get("state") in ("crawling", "syncing"):
            raise HTTPException(409, f"{body.url} is already being copied")
        _jobs[name] = {"name": name, "url": body.url, "collection_id": body.collection_id, "state": "crawling",
                       "started": time.time(), "progress": ""}
    _dir().mkdir(parents=True, exist_ok=True)
    auth.audit(conn, p.user_id, "crawl", body.url, collection_id=body.collection_id, max_pages=body.max_pages,
               browser=body.browser)
    conn.commit()
    threading.Thread(target=_run, args=(name, body, p.user_id), daemon=True, name=f"crawl-{name}").start()
    return _jobs[name]


@router.get("")
def list_crawls(p: Admin):
    """Crawls since the app started, and copies kept on disk from before."""
    out = {n: {k: v for k, v in j.items() if k != "pid"} for n, j in _jobs.items()}
    if _dir().is_dir():
        for d in sorted(_dir().iterdir()):
            if d.is_dir() and not d.name.endswith(".new") and d.name not in out:
                out[d.name] = {"name": d.name, "state": "kept", **_summary(d)}
    return list(out.values())
