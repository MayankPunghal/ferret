"""In-process exact vector search (numpy) used with the embedded database (laptop profile).

Why: the Windows build of the embedded PostgreSQL crashes in pgvector's distance functions, while storing
and reading vectors works. Computing cosine similarity in-process avoids that code path entirely and is fast
at laptop scale (a 100k x 768 float32 matrix is ~300 MB; one query is a single matrix-vector product).
Servers with an external PostgreSQL keep using pgvector in SQL (``KS_VECTOR_SEARCH``).

The cache is per vector space and refreshes incrementally: newly inserted chunks are appended; any
deletion (detected by a row-count mismatch) triggers a full reload. Vectors are L2-normalised at
ingestion, so the dot product equals cosine similarity.
"""

import threading

import numpy as np
from psycopg import Connection


class _Space:
    def __init__(self, dim: int = 0):
        self.ids = np.empty(0, dtype=np.int64)
        self.cols = np.empty(0, dtype=np.int64)
        self.mat = np.empty((0, dim), dtype=np.float32)
        self.max_id = 0
        self.pos: dict[int, int] = {}


class MemoryIndex:
    def __init__(self):
        self._spaces: dict[str, _Space] = {}
        self._lock = threading.Lock()

    def _load(self, conn: Connection, space: str, after_id: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with conn.cursor(binary=True) as cur:
            cur.execute(
                "SELECT id, collection_id, embedding FROM chunks WHERE embedding_model = %s AND id > %s ORDER BY id",
                (space, after_id),
            )
            rows = cur.fetchall()
        if not rows:
            return np.empty(0, np.int64), np.empty(0, np.int64), np.empty((0, 0), np.float32)
        ids = np.fromiter((r["id"] for r in rows), np.int64, len(rows))
        cols = np.fromiter((r["collection_id"] for r in rows), np.int64, len(rows))
        mat = np.stack([np.asarray(r["embedding"].to_numpy(), dtype=np.float32) for r in rows])
        return ids, cols, mat

    def _refresh(self, conn: Connection, space: str) -> _Space:
        stat = conn.execute(
            "SELECT count(*) AS n, coalesce(max(id), 0) AS m FROM chunks WHERE embedding_model = %s", (space,)
        ).fetchone()
        s = self._spaces.get(space)
        if s is not None and len(s.ids) == stat["n"] and s.max_id == stat["m"]:
            return s
        if s is not None and stat["m"] > s.max_id:
            ids, cols, mat = self._load(conn, space, s.max_id)
            if len(ids) and (s.mat.shape[0] == 0 or s.mat.shape[1] == mat.shape[1]):
                s.ids = np.concatenate([s.ids, ids])
                s.cols = np.concatenate([s.cols, cols])
                s.mat = mat if s.mat.shape[0] == 0 else np.vstack([s.mat, mat])
                s.max_id = int(s.ids[-1])
        if s is None or len(s.ids) != stat["n"]:  # first load, or rows were deleted
            ids, cols, mat = self._load(conn, space, 0)
            s = _Space()
            s.ids, s.cols, s.mat = ids, cols, mat
            s.max_id = int(ids[-1]) if len(ids) else 0
        s.pos = {int(i): k for k, i in enumerate(s.ids)}
        self._spaces[space] = s
        return s

    def warm(self, conn: Connection, space: str) -> int:
        """Load a vector space now (at startup), not on the first search. Returns the number of vectors."""
        with self._lock:
            return len(self._refresh(conn, space).ids)

    def top_k(self, conn: Connection, space: str, query: np.ndarray, scope: list[int], k: int) -> list[int]:
        with self._lock:
            s = self._refresh(conn, space)
        if len(s.ids) == 0:
            return []
        mask = np.isin(s.cols, np.asarray(scope, dtype=np.int64))
        idx = np.nonzero(mask)[0]
        if len(idx) == 0:
            return []
        sims = s.mat[idx] @ query.astype(np.float32)
        k = min(k, len(idx))
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top], kind="stable")]
        return [int(s.ids[idx[t]]) for t in top]

    def similarities(self, space: str, query: np.ndarray, chunk_ids: list[int]) -> dict[int, float]:
        s = self._spaces.get(space)
        if s is None:
            return {}
        out = {}
        for cid in chunk_ids:
            p = s.pos.get(cid)
            if p is not None:
                out[cid] = float(s.mat[p] @ query)
        return out


index = MemoryIndex()
