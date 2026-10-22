"""Ingestion: content-addressed original storage, versioning, and the durable job worker.

Flow: submit() stores the original bytes, creates a document version and queues a job in
one transaction. A worker claims jobs with SKIP LOCKED, parses/chunks/embeds outside any
transaction, then atomically swaps the document's chunks to the new version. Unchanged
content (same SHA-256 as the latest version) is not reprocessed.
"""

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from . import db
from .embedder import Embedder, EmbeddingError
from .parsing import ParseError, chunk_document, embedding_text, media_type_for, parse_document

log = logging.getLogger(__name__)

STALE_LOCK_MINUTES = 15


@dataclass
class SubmitResult:
    document_id: UUID
    version_id: int
    outcome: str  # 'queued' | 'unchanged'


class BlobStore:
    def __init__(self, root: Path):
        self.root = root / "blobs"

    def path(self, sha: str) -> Path:
        return self.root / sha[:2] / sha

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        p = self.path(sha)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(f".tmp{os.getpid()}.{threading.get_ident()}")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return sha

    def get(self, sha: str) -> bytes:
        return self.path(sha).read_bytes()

    def gc(self, conn: Connection, candidates: list[str]) -> None:
        for sha in candidates:
            used = conn.execute("SELECT 1 FROM document_versions WHERE sha256 = %s LIMIT 1", (sha,)).fetchone()
            if not used:
                self.path(sha).unlink(missing_ok=True)


def normalize_source_key(filename: str) -> str:
    name = filename.replace("\\", "/").split("/")[-1].strip()
    if not name or name in (".", ".."):
        raise ParseError("invalid_filename", "filename is empty")
    return name


def submit(
    conn: Connection,
    blobs: BlobStore,
    *,
    collection_id: int,
    filename: str,
    data: bytes,
    user_id: int | None,
    max_bytes: int,
    source_key: str | None = None,
    source_uri: str | None = None,
) -> SubmitResult:
    """Validate cheaply, store the original, and queue indexing unless content is unchanged."""
    filename = normalize_source_key(filename)
    media_type = media_type_for(filename)
    if len(data) == 0:
        raise ParseError("empty_file", "file is empty")
    if len(data) > max_bytes:
        raise ParseError("too_large", f"file exceeds {max_bytes} bytes")
    key = source_key or filename
    sha = blobs.put(data)
    with conn.transaction():
        doc = conn.execute(
            "INSERT INTO documents(collection_id, source_key, source_uri, title) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (collection_id, source_key) DO UPDATE SET updated_at = now() "
            "RETURNING id, latest_version_id",
            (collection_id, key, source_uri, filename),
        ).fetchone()
        # Serialize concurrent submissions of the same logical document.
        conn.execute("SELECT 1 FROM documents WHERE id = %s FOR UPDATE", (doc["id"],))
        latest = conn.execute(
            "SELECT v.id, v.sha256 FROM documents d JOIN document_versions v ON v.id = d.latest_version_id "
            "WHERE d.id = %s",
            (doc["id"],),
        ).fetchone()
        if latest and latest["sha256"] == sha:
            return SubmitResult(doc["id"], latest["id"], "unchanged")
        vno = conn.execute(
            "SELECT coalesce(max(version_no), 0) + 1 AS n FROM document_versions WHERE document_id = %s",
            (doc["id"],),
        ).fetchone()["n"]
        vid = conn.execute(
            "INSERT INTO document_versions(document_id, version_no, sha256, filename, media_type, size_bytes, "
            "created_by) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (doc["id"], vno, sha, filename, media_type, len(data), user_id),
        ).fetchone()["id"]
        conn.execute(
            "UPDATE jobs SET status = 'superseded', updated_at = now() "
            "WHERE document_id = %s AND status = 'queued'",
            (doc["id"],),
        )
        conn.execute("INSERT INTO jobs(document_id, version_id) VALUES (%s, %s)", (doc["id"], vid))
        conn.execute(
            "UPDATE documents SET latest_version_id = %s, status = 'pending', error_code = NULL, "
            "error_detail = NULL, source_uri = coalesce(%s, source_uri), updated_at = now() WHERE id = %s",
            (vid, source_uri, doc["id"]),
        )
    return SubmitResult(doc["id"], vid, "queued")


def delete_document(conn: Connection, blobs: BlobStore, document_id: UUID) -> bool:
    with conn.transaction():
        shas = [
            r["sha256"]
            for r in conn.execute("SELECT sha256 FROM document_versions WHERE document_id = %s", (document_id,))
        ]
        n = conn.execute("DELETE FROM documents WHERE id = %s", (document_id,)).rowcount
    if n:
        blobs.gc(conn, shas)
        conn.commit()
    return bool(n)


def retry_document(conn: Connection, document_id: UUID) -> bool:
    with conn.transaction():
        doc = conn.execute(
            "SELECT latest_version_id FROM documents WHERE id = %s AND status = 'failed' FOR UPDATE", (document_id,)
        ).fetchone()
        if not doc or doc["latest_version_id"] is None:
            return False
        conn.execute(
            "INSERT INTO jobs(document_id, version_id) VALUES (%s, %s)", (document_id, doc["latest_version_id"])
        )
        conn.execute(
            "UPDATE documents SET status = 'pending', error_code = NULL, error_detail = NULL, updated_at = now() "
            "WHERE id = %s",
            (document_id,),
        )
    return True


class Worker:
    def __init__(self, embedder: Embedder, blobs: BlobStore, max_pages: int, max_chars: int):
        self.embedder = embedder
        self.blobs = blobs
        self.max_pages = max_pages
        self.max_chars = max_chars

    def recover_stale(self, conn: Connection) -> int:
        with conn.transaction():
            return conn.execute(
                "UPDATE jobs SET status = 'queued', locked_at = NULL, updated_at = now() "
                "WHERE status = 'running' AND locked_at < now() - make_interval(mins => %s)",
                (STALE_LOCK_MINUTES,),
            ).rowcount

    def _claim(self, conn: Connection):
        with conn.transaction():
            job = conn.execute(
                "SELECT j.id, j.document_id, j.version_id, j.attempts, j.max_attempts, v.sha256, v.filename, "
                "d.collection_id FROM jobs j JOIN document_versions v ON v.id = j.version_id "
                "JOIN documents d ON d.id = j.document_id "
                "WHERE j.status = 'queued' AND j.run_after <= now() ORDER BY j.id "
                "FOR UPDATE OF j SKIP LOCKED LIMIT 1"
            ).fetchone()
            if job:
                conn.execute(
                    "UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_at = now(), "
                    "updated_at = now() WHERE id = %s",
                    (job["id"],),
                )
                conn.execute("UPDATE documents SET status = 'processing' WHERE id = %s", (job["document_id"],))
        return job

    def run_once(self) -> bool:
        """Process one job. Returns False when the queue is empty."""
        with db.pool().connection() as conn:
            job = self._claim(conn)
            if not job:
                return False
            try:
                parsed = parse_document(self.blobs.get(job["sha256"]), job["filename"], self.max_pages, self.max_chars)
                chunks = chunk_document(parsed)
                vectors = self.embedder.embed_documents([embedding_text(parsed.title, c) for c in chunks])
            except ParseError as e:  # deterministic: do not retry
                self._fail(conn, job, e.code, e.detail, retry=False)
                return True
            except EmbeddingError as e:
                self._fail(conn, job, "embedding_failed", str(e)[:500], retry=True)
                return True
            except OSError as e:
                self._fail(conn, job, "storage_error", f"{type(e).__name__}", retry=True)
                return True
            except Exception as e:  # noqa: BLE001 - recorded as job failure, never swallowed silently
                log.exception("unexpected ingestion failure job=%s", job["id"])
                self._fail(conn, job, "internal_error", type(e).__name__, retry=True)
                return True
            self._commit(conn, job, parsed, chunks, vectors)
            return True

    def _commit(self, conn, job, parsed, chunks, vectors) -> None:
        model = self.embedder.spec.version_key
        with conn.transaction():
            doc = conn.execute(
                "SELECT latest_version_id FROM documents WHERE id = %s FOR UPDATE", (job["document_id"],)
            ).fetchone()
            if doc is None:  # deleted while processing
                return
            if doc["latest_version_id"] != job["version_id"]:  # a newer upload arrived
                conn.execute("UPDATE jobs SET status = 'superseded', updated_at = now() WHERE id = %s", (job["id"],))
                return
            conn.execute("DELETE FROM chunks WHERE document_id = %s", (job["document_id"],))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO chunks(document_id, version_id, collection_id, ordinal, doc_title, heading_path, "
                    "page, anchor, text, idents, embedding, embedding_model) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            job["document_id"], job["version_id"], job["collection_id"], c.ordinal, parsed.title,
                            c.heading_path, c.page, c.anchor, c.text, c.idents, v, model,
                        )
                        for c, v in zip(chunks, vectors, strict=True)
                    ],
                )
            conn.execute(
                "UPDATE documents SET indexed_version_id = %s, status = 'indexed', title = %s, metadata = %s, "
                "error_code = NULL, error_detail = NULL, updated_at = now() WHERE id = %s",
                (job["version_id"], parsed.title[:500], Jsonb(parsed.metadata), job["document_id"]),
            )
            conn.execute("UPDATE jobs SET status = 'done', updated_at = now() WHERE id = %s", (job["id"],))

    def _fail(self, conn, job, code: str, detail: str, retry: bool) -> None:
        will_retry = retry and job["attempts"] + 1 < job["max_attempts"]
        with conn.transaction():
            if will_retry:
                backoff = 5 * 2 ** job["attempts"]
                conn.execute(
                    "UPDATE jobs SET status = 'queued', locked_at = NULL, error_code = %s, error_detail = %s, "
                    "run_after = now() + make_interval(secs => %s), updated_at = now() WHERE id = %s",
                    (code, detail, backoff, job["id"]),
                )
                conn.execute("UPDATE documents SET status = 'pending' WHERE id = %s", (job["document_id"],))
            else:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', error_code = %s, error_detail = %s, updated_at = now() "
                    "WHERE id = %s",
                    (code, detail, job["id"]),
                )
                conn.execute(
                    "UPDATE documents SET status = 'failed', error_code = %s, error_detail = %s, updated_at = now() "
                    "WHERE id = %s AND latest_version_id = %s",
                    (code, detail, job["document_id"], job["version_id"]),
                )
        log.warning("ingestion job %s failed code=%s retry=%s", job["id"], code, will_retry)

    def drain(self) -> int:
        n = 0
        while self.run_once():
            n += 1
        return n

    def loop(self, stop: threading.Event, poll: float) -> None:
        with db.pool().connection() as conn:
            self.recover_stale(conn)
        while not stop.is_set():
            try:
                if not self.run_once():
                    stop.wait(poll)
            except Exception:  # noqa: BLE001 - keep the worker alive on DB hiccups
                log.exception("worker loop error")
                time.sleep(poll * 5)
