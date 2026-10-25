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

import numpy as np
from psycopg import Connection
from psycopg.types.json import Jsonb

from . import db, runtime, trace
from .embedder import EmbeddingError
from .parsing import (
    ParseError,
    chunk_document,
    doc_summary,
    embedding_text,
    media_type_for,
    parse_document,
)

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


def path_key(path: str) -> str:
    """A file's place in an uploaded folder, its key in the collection: '\\kit\\docs\\a.md' -> 'kit/docs/a.md'.
    Keys by path keep docs/modules/index.md and docs/workflows/index.md apart."""
    parts = [x.strip() for x in path.replace("\\", "/").split("/") if x.strip() not in ("", ".")]
    if not parts or ".." in parts:
        raise ParseError("invalid_filename", "bad path")
    return "/".join(parts)


SKIP = (".", "_")  # folders and files starting with these are left out of a folder (.git, .claude, _tools, _notes)


def folder_files(root: Path, suffixes) -> tuple[list[Path], dict[str, int]]:
    """The files of a folder and its subfolders to index, in path order, and counts of what was left out:
    names starting with '.' or '_' (with everything inside), and files of other types. Links are not followed."""
    keep, skipped = [], {}
    for here, dirs, files in os.walk(root):
        left = [d for d in dirs if d.startswith(SKIP)]
        if left:
            skipped["folders starting with . or _"] = skipped.get("folders starting with . or _", 0) + len(left)
        dirs[:] = sorted(d for d in dirs if not d.startswith(SKIP))
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if f.startswith(SKIP):
                why = "files starting with . or _"
            elif ext in suffixes:
                keep.append(Path(here) / f)
                continue
            else:
                why = ext or "no extension"
            skipped[why] = skipped.get(why, 0) + 1
    return keep, skipped


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
        if key != filename:  # by path: a document uploaded earlier under its bare file name moves to the path
            conn.execute(
                "UPDATE documents SET source_key = %(key)s WHERE collection_id = %(c)s AND source_key = %(name)s "
                "AND NOT EXISTS (SELECT 1 FROM documents WHERE collection_id = %(c)s AND source_key = %(key)s)",
                {"key": key, "name": filename, "c": collection_id})
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


def reindex(conn: Connection, signature: str, everything: bool = False) -> int:
    """Queue indexed/failed documents whose chunks were built with a different configuration."""
    with conn.transaction():
        rows = conn.execute(
            "SELECT id, latest_version_id FROM documents WHERE latest_version_id IS NOT NULL "
            "AND status IN ('indexed', 'failed') AND (%s OR index_signature IS DISTINCT FROM %s) "
            "ORDER BY created_at, id FOR UPDATE",  # queued in the order they were added
            (everything, signature),
        ).fetchall()
        for r in rows:
            conn.execute("INSERT INTO jobs(document_id, version_id) VALUES (%s, %s)", (r["id"], r["latest_version_id"]))
            conn.execute("UPDATE documents SET status = 'pending', updated_at = now() WHERE id = %s", (r["id"],))
    return len(rows)


def requeue(conn: Connection, document_id: UUID) -> bool:
    """Index an indexed document again with the current settings (e.g. to embed its new summary)."""
    with conn.transaction():
        doc = conn.execute("SELECT latest_version_id FROM documents WHERE id = %s AND status = 'indexed' FOR UPDATE",
                           (document_id,)).fetchone()
        if not doc or doc["latest_version_id"] is None:
            return False
        conn.execute("INSERT INTO jobs(document_id, version_id) VALUES (%s, %s)", (document_id, doc["latest_version_id"]))
        conn.execute("UPDATE documents SET status = 'pending', updated_at = now() WHERE id = %s", (document_id,))
    return True


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
    """Indexes queued versions using the *current* runtime settings (model, chunk size/overlap)."""

    def __init__(self, blobs: BlobStore, max_pages: int, max_chars: int):
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
            t = trace.Timer()
            try:
                rt = runtime.load(conn)
                conn.commit()
                embedder = runtime.embedder(rt.embed_model)
                if embedder._model is None:
                    self._progress(conn, job, "loading model (first use downloads it)")
                    embedder.warm()
                self._progress(conn, job, "reading file")
                t.step("loading the model")
                parsed = parse_document(self.blobs.get(job["sha256"]), job["filename"], self.max_pages, self.max_chars)
                t.step("reading the file")
                chunks = chunk_document(parsed, rt.chunk_size, rt.chunk_overlap)
                t.step("cutting into passages")
                written = conn.execute("SELECT enrichment->>'summary' AS s FROM documents WHERE id = %s",
                                       (job["document_id"],)).fetchone()["s"] if rt.context_chars and rt.context_source != "paragraph" else None
                conn.commit()
                summary = doc_summary(parsed, rt.context_chars, written or "", rt.context_source)
                texts = [embedding_text(parsed.title, c, summary) for c in chunks]
                hashes = [hashlib.sha256(x.encode()).hexdigest() for x in texts]
                # passages whose embedding text and model are unchanged keep their vector (same text, same model)
                known = {r["embed_hash"]: r["embedding"] for r in conn.execute(
                    "SELECT embed_hash, embedding FROM chunks WHERE document_id = %s AND embedding_model = %s "
                    "AND embed_hash = ANY(%s)", (job["document_id"], embedder.spec.version_key, hashes))}
                conn.commit()
                todo = [i for i, h in enumerate(hashes) if h not in known]
                step = max(embedder.batch_size * 2, 8)
                order = sorted(todo, key=lambda i: len(texts[i]))  # similar lengths batched together
                parts = []
                for i in range(0, len(order), step):
                    self._progress(conn, job, "embedding", i, len(order))
                    parts.append(embedder.embed_documents([texts[j] for j in order[i : i + step]]))
                vectors = np.empty((len(texts), embedder.spec.dim), dtype=np.float32)
                for i, h in enumerate(hashes):
                    if h in known:
                        vectors[i] = known[h].to_numpy()
                if order:
                    vectors[order] = np.vstack(parts)
                t.step(f"embedding {len(order)} passages ({embedder.spec.name})"
                       + (f"; {len(texts) - len(order)} unchanged kept their vectors" if len(order) < len(texts) else ""))
                self._progress(conn, job, "saving", len(texts), len(texts))
            except ParseError as e:  # deterministic: do not retry
                self._fail(conn, job, e.code, e.detail, retry=False)
                trace.record("index", f"Index {job['filename']}", ms=t.ms, status=e.code, steps=t.steps, detail=e.detail)
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
            self._commit(conn, job, parsed, chunks, vectors, hashes, embedder.spec.version_key, rt.signature())
            t.step("saving")
            trace.record("index", f"Index {job['filename']}", ms=t.ms, steps=t.steps,
                         detail=f"{len(chunks)} passages from {len(parsed.sections)} sections",
                         response={"title": parsed.title, "passages": [
                             {"heading": c.heading_path, "page": c.page, "text": c.text[:300]} for c in chunks[:50]]})
            return True

    @staticmethod
    def _progress(conn, job, stage: str, done: int | None = None, total: int | None = None) -> None:
        conn.execute(
            "UPDATE jobs SET stage = %s, progress_done = %s, progress_total = %s, locked_at = now() WHERE id = %s",
            (stage, done, total, job["id"]),
        )
        conn.commit()

    def _commit(self, conn, job, parsed, chunks, vectors, hashes, model: str, signature: str) -> None:
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
                    "page, anchor, text, idents, embedding, embedding_model, embed_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            job["document_id"], job["version_id"], job["collection_id"], c.ordinal, parsed.title,
                            c.heading_path, c.page, c.anchor, c.text, c.idents, v, model, h,
                        )
                        for c, v, h in zip(chunks, vectors, hashes, strict=True)
                    ],
                )
                ids = {r["ordinal"]: r["id"] for r in conn.execute(
                    "SELECT id, ordinal FROM chunks WHERE version_id = %s", (job["version_id"],))}
                links = [(job["document_id"], ids[c.ordinal], ln.kind, ln.target, ln.anchor, ln.label[:200])
                         for c in chunks for ln in c.links]
                if links:  # links are deleted with the old chunks (cascade) and rebuilt here
                    cur.executemany(
                        "INSERT INTO doc_links(document_id, chunk_id, kind, target, anchor, label) "
                        "VALUES (%s, %s, %s, %s, %s, %s)", links)
            # a page saved by a crawler names its web address (site2md: source_url): the original location,
            # unless the uploader gave one (a file:// path from folder ingest is replaced)
            url = str(parsed.metadata.get("source_url") or "")
            url = url[:2000] if url.startswith(("https://", "http://")) else None
            conn.execute(
                "UPDATE documents SET indexed_version_id = %s, status = 'indexed', title = %s, metadata = %s, "
                "index_signature = %s, error_code = NULL, error_detail = NULL, updated_at = now(), "
                "embedding = %s, embedding_model = %s, source_uri = coalesce(CASE WHEN source_uri LIKE 'file://%%' "
                "THEN NULL ELSE source_uri END, %s, source_uri) WHERE id = %s",
                (job["version_id"], parsed.title[:500], Jsonb(parsed.metadata), signature,
                 np.mean(vectors, axis=0) if len(vectors) else None, model, url, job["document_id"]),
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
