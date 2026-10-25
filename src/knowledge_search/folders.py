"""Folders on the app's computer, indexed by path and synced on request (administrators only, since the app
reads its own disk).

A sync walks the folder and its subfolders, leaving out names that start with '.' or '_' (.git, .claude,
_tools) and files of types not chosen. Each file is keyed by its path under the folder's name
(``repo-kit/docs/modules/index.md``) and submitted as an upload would be: unchanged files stay as they are,
changed files get a new version, new files are added. Documents that came from the folder (documents.folder_id)
whose file is gone, or is now left out, are removed. A sync runs in the background; its counts are kept on the folder.
A sync can be stopped between files: what was indexed stays, and nothing is removed (the next sync finishes it).
"""

import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import auth, db
from .deps import Admin, Conn, state
from .ingest import delete_document, folder_files, submit
from .parsing import ParseError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/folders", tags=["admin"])
TYPES = {"markdown": (".md", ".markdown"), "text": (".txt",), "html": (".html", ".htm"), "word": (".docx",),
         "powerpoint": (".pptx",), "pdf": (".pdf",), "images": (".png", ".jpg", ".jpeg", ".gif", ".webp")}
_running: dict[int, dict] = {}  # folder id -> progress {"done", "total"} while it syncs
_stop: set[int] = set()  # folders asked to stop syncing
_lock = threading.Lock()


class FolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    collection_id: int
    types: list[str] = Field(default_factory=lambda: list(TYPES), min_length=1)


def _view(r: dict) -> dict:
    return {"id": r["id"], "path": r["path"], "collection_id": r["collection_id"], "collection": r["collection"],
            "types": r["types"], "synced_at": r["synced_at"], "syncing": r["id"] in _running,
            "progress": _running.get(r["id"]), "result": r["result"]}


def _rows(conn, folder_id: int | None = None) -> list[dict]:
    return conn.execute(
        "SELECT f.*, c.name AS collection FROM folder_sources f JOIN collections c ON c.id = f.collection_id "
        "WHERE %(id)s::bigint IS NULL OR f.id = %(id)s ORDER BY f.id", {"id": folder_id}).fetchall()


def sync(folder_id: int, user_id: int | None) -> dict:
    """Bring the folder's documents in line with the files on disk; returns the counts."""
    with db.pool().connection() as conn:
        f = conn.execute("SELECT * FROM folder_sources WHERE id = %s", (folder_id,)).fetchone()
        root = Path(f["path"])
        if not root.is_dir():  # an unplugged drive or a renamed folder: change nothing
            result = {"error": "folder not found on this computer"}
        else:
            files, skipped = folder_files(root, {e for t in f["types"] for e in TYPES[t]})
            result = {"files": len(files), "queued": 0, "unchanged": 0, "removed": 0, "rejected": [],
                      "skipped": skipped}
            seen, s = set(), state.settings
            progress = _running.get(folder_id, {})
            progress["total"] = len(files)
            for p in files:
                if folder_id in _stop:  # what is indexed stays; nothing is removed from a sync cut short
                    result["stopped"] = progress.get("done", 0)
                    break
                rel = p.relative_to(root).as_posix()
                seen.add(f"{root.name}/{rel}")
                try:
                    r = submit(conn, state.blobs, collection_id=f["collection_id"], filename=p.name,
                               data=p.read_bytes(), user_id=user_id, max_bytes=s.max_upload_mb * 1024 * 1024,
                               source_key=f"{root.name}/{rel}", source_uri=p.resolve().as_uri())
                    conn.execute("UPDATE documents SET folder_id = %s WHERE id = %s", (folder_id, r.document_id))
                    result[r.outcome] += 1
                except ParseError as e:
                    result["rejected"].append(f"{rel}: {e.detail}")
                except OSError as e:
                    result["rejected"].append(f"{rel}: {e.strerror or e}")
                conn.commit()
                progress["done"] = progress.get("done", 0) + 1
            for d in [] if "stopped" in result else conn.execute("SELECT id, source_key FROM documents WHERE folder_id = %s", (folder_id,)).fetchall():
                if d["source_key"] not in seen:
                    delete_document(conn, state.blobs, d["id"])
                    result["removed"] += 1
            result["rejected"] = result["rejected"][:50]
        conn.execute("UPDATE folder_sources SET synced_at = now(), result = %s WHERE id = %s",
                     (Jsonb(result), folder_id))
        auth.audit(conn, user_id, "folder_sync", str(folder_id), **{k: v for k, v in result.items() if k != "rejected"})
        conn.commit()
    return result


def start(folder_id: int, user_id: int | None) -> bool:
    """Sync in the background; False when this folder is already syncing."""
    with _lock:
        if folder_id in _running:
            return False
        _running[folder_id] = {"done": 0, "total": 0}
        _stop.discard(folder_id)

    def run():
        try:
            sync(folder_id, user_id)
        except Exception:
            log.exception("folder sync failed id=%s", folder_id)
        finally:
            _running.pop(folder_id, None)
            _stop.discard(folder_id)

    threading.Thread(target=run, daemon=True, name=f"folder-{folder_id}").start()
    return True


@router.get("")
def list_folders(p: Admin, conn: Conn):
    return [_view(r) for r in _rows(conn)]


@router.post("", status_code=202)
def add_folder(body: FolderBody, p: Admin, conn: Conn):
    """Remember a folder and sync it now. Adding the same folder again syncs it."""
    root = Path(body.path.strip()).expanduser()
    if not root.is_absolute():
        raise HTTPException(400, "give the full path of the folder, e.g. C:\\Users\\me\\docs or /home/me/docs")
    if not root.is_dir():
        raise HTTPException(400, f"no folder at {root} on the computer running Ferret")
    if unknown := set(body.types) - set(TYPES):
        raise HTTPException(400, f"unknown types: {', '.join(sorted(unknown))}; choose from {', '.join(TYPES)}")
    if not conn.execute("SELECT 1 FROM collections WHERE id = %s", (body.collection_id,)).fetchone():
        raise HTTPException(404, "collection not found")
    root = root.resolve()
    # keys start with the folder's name, so two folders of the same name in one collection would mix
    if any(Path(r["path"]).name == root.name and r["path"] != str(root) for r in _rows(conn)
           if r["collection_id"] == body.collection_id):
        raise HTTPException(400, f"another folder named {root.name} is already synced into this collection")
    fid = conn.execute(
        "INSERT INTO folder_sources(collection_id, path, types, created_by) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (collection_id, path) DO UPDATE SET types = EXCLUDED.types RETURNING id",
        (body.collection_id, str(root), [t for t in TYPES if t in body.types], p.user_id)).fetchone()["id"]
    auth.audit(conn, p.user_id, "folder_add", str(fid), path=str(root), collection_id=body.collection_id)
    conn.commit()
    start(fid, p.user_id)
    return _view(_rows(conn, fid)[0])


@router.post("/{folder_id}/sync", status_code=202)
def sync_folder(folder_id: int, p: Admin, conn: Conn):
    if not _rows(conn, folder_id):
        raise HTTPException(404, "folder not found")
    start(folder_id, p.user_id)
    return _view(_rows(conn, folder_id)[0])


@router.post("/{folder_id}/stop")
def stop_folder(folder_id: int, p: Admin, conn: Conn):
    """Stop a running sync after the file it is on."""
    if not _rows(conn, folder_id):
        raise HTTPException(404, "folder not found")
    if folder_id in _running:
        _stop.add(folder_id)
    return _view(_rows(conn, folder_id)[0])


@router.delete("/{folder_id}")
def remove_folder(folder_id: int, p: Admin, conn: Conn, documents: bool = False):
    """Stop syncing a folder; with documents=true also remove its documents from the index (never the files)."""
    rows = _rows(conn, folder_id)
    if not rows:
        raise HTTPException(404, "folder not found")
    if folder_id in _running:
        raise HTTPException(409, "the folder is syncing; try again when it has finished")
    removed = 0
    if documents:
        for d in conn.execute("SELECT id FROM documents WHERE folder_id = %s", (folder_id,)).fetchall():
            delete_document(conn, state.blobs, d["id"])
            removed += 1
    conn.execute("DELETE FROM folder_sources WHERE id = %s", (folder_id,))
    auth.audit(conn, p.user_id, "folder_remove", str(folder_id), documents_removed=removed)
    conn.commit()
    return {"removed": True, "documents_removed": removed}
