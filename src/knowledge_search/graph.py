"""Deterministic document link graph: outgoing/incoming links and bounded blast-radius traversal.

Edges come from Markdown links, [[wiki links]] and front-matter relations (parsing.extract_links /
RELATION_KEYS), stored per chunk in ``doc_links``. Targets are resolved at query time against document
file names (with or without extension) and titles, so a link starts working as soon as its target is
ingested. Every query and every traversal hop is restricted to the caller's readable collections: an
edge is visible only when both ends are readable, so links never reveal restricted documents.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from .deps import Conn, User

router = APIRouter(prefix="/api/documents", tags=["graph"])

MAX_DEPTH = 3
MAX_NODES = 200


def _resolves(doc: str, link: str) -> str:
    """SQL condition: link `link` points at document `doc`."""
    return (f"(lower({doc}.source_key) = {link}.target OR lower({doc}.source_key) = {link}.target || '.md' "
            f"OR lower(regexp_replace({doc}.source_key, '\\.[^.]+$', '')) = {link}.target "
            f"OR lower({doc}.title) = {link}.target)")


def _check_readable(conn, scope: list[int], document_id: UUID) -> dict:
    row = conn.execute(
        "SELECT id, title FROM documents WHERE id = %s AND collection_id = ANY(%s)", (document_id, scope)
    ).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    return row


def incoming(conn, scope: list[int], targets: list[UUID]) -> list[dict]:
    """Links from readable documents to any of `targets`."""
    return conn.execute(
        "SELECT DISTINCT ON (s.id, x.id, l.kind) x.id AS target_id, s.id AS document_id, s.title, "
        "col.name AS collection, l.kind, l.label, l.chunk_id "
        "FROM documents x JOIN doc_links l ON " + _resolves("x", "l") + " "
        "JOIN documents s ON s.id = l.document_id AND s.collection_id = ANY(%(scope)s) AND s.id <> x.id "
        "JOIN collections col ON col.id = s.collection_id "
        "WHERE x.id = ANY(%(targets)s) ORDER BY s.id, x.id, l.kind, l.chunk_id",
        {"scope": scope, "targets": targets},
    ).fetchall()


def neighbours(conn, scope: list[int], documents: list[UUID]) -> list[tuple[UUID, UUID]]:
    """(linked document, seed) pairs: readable documents linked from or to any of `documents` (one hop,
    both directions), excluding the seeds themselves."""
    rows = conn.execute(
        "SELECT DISTINCT t.id, l.document_id AS seed FROM doc_links l JOIN documents t ON " + _resolves("t", "l")
        + " AND t.collection_id = ANY(%(scope)s) WHERE l.document_id = ANY(%(docs)s) AND t.id <> ALL(%(docs)s) "
        "UNION SELECT DISTINCT s.id, x.id FROM documents x JOIN doc_links l ON " + _resolves("x", "l") + " "
        "JOIN documents s ON s.id = l.document_id AND s.collection_id = ANY(%(scope)s) "
        "WHERE x.id = ANY(%(docs)s) AND s.id <> ALL(%(docs)s)",
        {"scope": scope, "docs": documents},
    ).fetchall()
    return [(r["id"], r["seed"]) for r in rows]


@router.get("/{document_id}/links")
def document_links(document_id: UUID, p: User, conn: Conn):
    """Outgoing and incoming links of one document, limited to documents the caller can read."""
    scope = list(p.readable)
    _check_readable(conn, scope, document_id)
    rows = conn.execute(
        "SELECT l.kind, l.label, l.target, l.anchor, l.chunk_id AS source_chunk_id, t.id AS document_id, "
        "t.title, col.name AS collection, "
        "(SELECT c.id FROM chunks c WHERE c.document_id = t.id "
        " ORDER BY (c.anchor IS NOT DISTINCT FROM l.anchor) DESC, c.ordinal LIMIT 1) AS chunk_id "
        "FROM doc_links l LEFT JOIN LATERAL (SELECT d.id, d.title, d.collection_id FROM documents d "
        "  WHERE d.collection_id = ANY(%(scope)s) AND d.id <> l.document_id AND " + _resolves("d", "l") + " "
        "  ORDER BY d.updated_at DESC LIMIT 1) t ON true "
        "LEFT JOIN collections col ON col.id = t.collection_id "
        "WHERE l.document_id = %(doc)s ORDER BY l.chunk_id, l.id",
        {"scope": scope, "doc": document_id},
    ).fetchall()
    outgoing, unresolved, seen = [], [], set()
    for r in rows:
        if r["document_id"] is None:
            # Target text comes from this (readable) document; it may be missing or simply not readable.
            unresolved.append({"kind": r["kind"], "label": r["label"], "target": r["target"]})
            continue
        key = (r["kind"], r["document_id"], r["anchor"])
        if key not in seen:
            seen.add(key)
            outgoing.append({**r, "document_id": str(r["document_id"])})
    inc = [{**r, "document_id": str(r["document_id"]), "target_id": str(r["target_id"])}
           for r in incoming(conn, scope, [document_id])]
    return {"outgoing": outgoing, "incoming": inc, "unresolved": unresolved}


@router.get("/{document_id}/impact")
def impact(document_id: UUID, p: User, conn: Conn, depth: int = Query(2, ge=1, le=MAX_DEPTH),
           kinds: str | None = Query(None, description="comma-separated edge kinds; default all")):
    """Blast radius: documents that (transitively) link to or depend on this one, breadth-first, bounded."""
    scope = list(p.readable)
    root = _check_readable(conn, scope, document_id)
    allowed = {k.strip() for k in kinds.split(",")} if kinds else None
    visited = {document_id}
    frontier = [document_id]
    nodes = []
    for level in range(1, depth + 1):
        if not frontier or len(nodes) >= MAX_NODES:
            break
        nxt = []
        for e in incoming(conn, scope, frontier):
            if allowed and e["kind"] not in allowed:
                continue
            if e["document_id"] in visited:
                continue
            visited.add(e["document_id"])
            nxt.append(e["document_id"])
            nodes.append({"document_id": str(e["document_id"]), "title": e["title"], "collection": e["collection"],
                          "depth": level, "kind": e["kind"], "via_document_id": str(e["target_id"]),
                          "chunk_id": e["chunk_id"]})
            if len(nodes) >= MAX_NODES:
                break
        frontier = nxt
    return {"document": {"id": str(root["id"]), "title": root["title"]}, "depth": depth, "nodes": nodes,
            "truncated": len(nodes) >= MAX_NODES}


MAX_GRAPH_NODES = 60


@router.get("/{document_id}/graph")
def neighbourhood(document_id: UUID, p: User, conn: Conn, depth: int = Query(1, ge=1, le=2)):
    """Nodes and typed edges around one document (links in both directions, up to `depth` hops), for the
    graph view. Readable documents only; at most MAX_GRAPH_NODES nodes (nearest first)."""
    scope = list(p.readable)
    root = _check_readable(conn, scope, document_id)
    level = {document_id: 0}
    frontier = [document_id]
    for d in range(1, depth + 1):
        nxt = []
        for doc, _seed in sorted(neighbours(conn, scope, frontier), key=lambda x: str(x[0])):
            if doc not in level and len(level) < MAX_GRAPH_NODES:
                level[doc] = d
                nxt.append(doc)
        frontier = nxt
    ids = list(level)
    docs = {r["id"]: r for r in conn.execute(
        "SELECT d.id, d.title, col.name AS collection, "
        "(SELECT c.id FROM chunks c WHERE c.document_id = d.id ORDER BY c.ordinal LIMIT 1) AS chunk_id "
        "FROM documents d JOIN collections col ON col.id = d.collection_id WHERE d.id = ANY(%s)", (ids,))}
    edges = conn.execute(
        "SELECT DISTINCT l.document_id AS source, t.id AS target, l.kind FROM doc_links l JOIN documents t ON "
        + _resolves("t", "l") + " WHERE l.document_id = ANY(%(ids)s) AND t.id = ANY(%(ids)s) AND t.id <> l.document_id "
        "ORDER BY 1, 2, 3", {"ids": ids}).fetchall()
    return {
        "document": {"id": str(root["id"]), "title": root["title"]}, "depth": depth,
        "nodes": [{"id": str(i), "title": docs[i]["title"], "collection": docs[i]["collection"], "depth": level[i],
                   "chunk_id": docs[i]["chunk_id"]} for i in ids if i in docs],
        "edges": [{"source": str(e["source"]), "target": str(e["target"]), "kind": e["kind"]} for e in edges],
        "truncated": len(level) >= MAX_GRAPH_NODES,
    }
