"""Deterministic document link graph: outgoing/incoming links and bounded blast-radius traversal.

Edges come from Markdown links, [[wiki links]] and front-matter relations (parsing.extract_links /
RELATION_KEYS), stored per chunk in ``doc_links``. Targets are resolved at query time against document
file names (with or without extension; the last part of a folder path) and titles, so a link starts working as soon as its target is
ingested. Pages that do not link explicitly (crawled intranet pages, exported docs) are connected by
*mentions*: a passage naming another page's title (``mentions``), and by *meaning*: pages that are each
other's closest by document vector (``similar``). Mentions feed search expansion; both appear in the
Connections list and the graph view. Neither is used by the blast radius, which follows declared links only. Every query and every traversal hop is restricted to the caller's readable collections: an
edge is visible only when both ends are readable, so links never reveal restricted documents.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from .deps import Conn, User

router = APIRouter(prefix="/api/documents", tags=["graph"])

MAX_DEPTH = 3
MAX_NODES = 200
MENTIONS = True  # also treat a passage naming another page's title as a link (kind "mentions")
SIMILAR = True  # also link pages that are each other's closest in meaning (kind "similar")
SIMILAR_K = 3  # "closest": among each other's SIMILAR_K nearest documents (measured in eval/link_eval.py)


def _resolves(doc: str, link: str) -> str:
    """SQL condition: link `link` points at document `doc`."""
    name = f"lower(regexp_replace({doc}.source_key, '^.*/', ''))"  # the file name of a folder path key
    return (f"({name} = {link}.target OR {name} = {link}.target || '.md' "
            f"OR regexp_replace({name}, '\\.[^.]+$', '') = {link}.target "
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


def mentions(conn, scope: list[int], documents: list[UUID]) -> list[tuple[UUID, UUID]]:
    """Implicit links: (source, target) document pairs where a passage of `source` names the title of
    `target` ("... calls the Asset Service to ..." → page "Asset Service"), with source or target in
    `documents`. Stemmed phrase match of titles of two or more words (one-word titles such as "Overview" are
    too ambiguous); readable documents only. Computed at query time with the keyword index, so mentions of
    a page start working as soon as it is ingested, whichever document came first."""
    rows = conn.execute(
        "WITH t AS (SELECT id, q, tsvector_to_array(to_tsvector('english', title)) AS lex FROM documents, "
        "  phraseto_tsquery('english', title) q WHERE collection_id = ANY(%(scope)s) AND numnode(q) >= 3), "
        # lexemes of each seed document: cheap pre-filter before the phrase check
        "s AS (SELECT c.document_id AS id, array_agg(DISTINCT w) AS lex FROM chunks c, "
        "  unnest(tsvector_to_array(c.tsv)) w WHERE c.document_id = ANY(%(docs)s) "
        "  AND c.collection_id = ANY(%(scope)s) GROUP BY 1) "
        "SELECT DISTINCT c.document_id AS source, t.id AS target FROM t JOIN chunks c ON c.tsv @@ t.q "
        "AND c.collection_id = ANY(%(scope)s) AND c.document_id <> t.id WHERE t.id = ANY(%(docs)s) "
        "UNION SELECT s.id, t.id FROM s JOIN t ON s.lex @> t.lex AND t.id <> s.id "
        "WHERE EXISTS (SELECT 1 FROM chunks c WHERE c.document_id = s.id AND c.tsv @@ t.q)",
        {"scope": scope, "docs": documents},
    ).fetchall()
    return [(r["source"], r["target"]) for r in rows]


def similar(conn, scope: list[int], documents: list[UUID], floor: float) -> list[tuple[UUID, UUID, float]]:
    """Meaning-based links: (seed, other, cosine) where `seed` is in `documents` and the two documents are
    *mutual* nearest neighbours — each among the other's SIMILAR_K closest readable documents by document
    vector (mean of passage vectors, same embedding model) — and at least `floor` similar. Mutual closeness
    needs no model-specific threshold (on 623 Kubernetes pages, 61% of these pairs are pages the authors
    linked themselves; most of the rest are plainly related); `floor` only stops tiny collections from linking
    unrelated pages. Exact search over document vectors, readable documents only. With the embedded
    database the similarities are computed here (pgvector's distances crash it on Windows)."""
    from . import runtime
    from .config import memory_vectors

    if memory_vectors(runtime.base()):
        return _similar_in_memory(conn, scope, documents, floor)
    rows = conn.execute(
        "WITH s AS (SELECT id, embedding AS e, embedding_model AS m FROM documents WHERE id = ANY(%(docs)s) "
        "  AND collection_id = ANY(%(scope)s) AND embedding IS NOT NULL), "
        "nn AS (SELECT s.id AS seed, n.id AS other, n.e AS oe, s.m, 1 - (n.e <=> s.e) AS sim FROM s CROSS JOIN LATERAL "
        "  (SELECT d.id, d.embedding AS e FROM documents d WHERE d.collection_id = ANY(%(scope)s) AND d.id <> s.id "
        "   AND d.embedding_model = s.m ORDER BY d.embedding <=> s.e LIMIT %(k)s) n) "
        "SELECT seed, other, sim FROM nn WHERE sim >= %(floor)s AND seed IN (SELECT d.id FROM documents d "
        "  WHERE d.collection_id = ANY(%(scope)s) AND d.id <> nn.other AND d.embedding_model = nn.m "
        "  ORDER BY d.embedding <=> nn.oe LIMIT %(k)s)",
        {"scope": scope, "docs": documents, "k": SIMILAR_K, "floor": floor},
    ).fetchall()
    return [(r["seed"], r["other"], float(r["sim"])) for r in rows]


_doc_vectors: dict = {"key": None, "groups": []}  # every document vector, normalised, per embedding model


def _document_vectors(conn) -> list[tuple]:
    """(ids, collection ids, unit vectors) per embedding model, cached until a document is indexed or removed:
    reading every document vector took ~0.6 s per call on 3,461 documents, three times per 2-hop graph."""
    import numpy as np

    key = tuple(conn.execute("SELECT count(*) AS n, max(updated_at) AS t FROM documents "
                             "WHERE embedding IS NOT NULL").fetchone().values())
    if _doc_vectors["key"] != key:
        by_model: dict[str, list] = {}
        for r in conn.execute("SELECT id, collection_id, embedding, embedding_model FROM documents "
                              "WHERE embedding IS NOT NULL ORDER BY id").fetchall():
            by_model.setdefault(r["embedding_model"], []).append(r)
        groups = []
        for rows in by_model.values():
            m = np.stack([np.asarray(r["embedding"].to_numpy(), dtype=np.float32) for r in rows])
            m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
            groups.append(([r["id"] for r in rows], np.array([r["collection_id"] for r in rows]), m))
        _doc_vectors.update(key=key, groups=groups)
    return _doc_vectors["groups"]


def _similar_in_memory(conn, scope: list[int], documents: list[UUID], floor: float) -> list[tuple[UUID, UUID, float]]:
    """`similar` with numpy: same mutual-nearest rule, per embedding model, readable documents only."""
    import numpy as np

    out = []
    for all_ids, cols, all_m in _document_vectors(conn):
        keep = np.isin(cols, scope)
        ids, m = [d for d, k in zip(all_ids, keep) if k], all_m[keep]
        pos = {d: i for i, d in enumerate(ids)}

        def nearest(i, m=m):
            s = m @ m[i]
            s[i] = -np.inf
            return list(np.argsort(-s, kind="stable")[:SIMILAR_K]), s

        for d in documents:
            if d not in pos:
                continue
            near, sims = nearest(pos[d])
            out += [(d, ids[j], float(sims[j])) for j in near if sims[j] >= floor and pos[d] in nearest(j)[0]]
    return out


def _floor(conn) -> float:
    from . import runtime  # runtime imports models; keep graph importable without it
    return runtime.load(conn).gate_dense_min


def neighbours(conn, scope: list[int], documents: list[UUID], with_similar: bool = False) -> list[tuple[UUID, UUID]]:
    """(linked document, seed) pairs: readable documents linked from or to any of `documents` (one hop,
    both directions, explicit links and, with MENTIONS, title mentions; meaning-based links only when
    `with_similar`), excluding the seeds themselves."""
    seeds = set(documents)
    implicit = [(t, s) if s in seeds else (s, t) for s, t in mentions(conn, scope, documents)] if MENTIONS else []
    if with_similar and SIMILAR:  # graph view only: in search expansion they took slots from real dependencies
        implicit += [(o, s) for s, o, _ in similar(conn, scope, documents, _floor(conn))]
    implicit = [(d, seed) for d, seed in implicit if d not in seeds]
    rows = conn.execute(
        "SELECT DISTINCT t.id, l.document_id AS seed FROM doc_links l JOIN documents t ON " + _resolves("t", "l")
        + " AND t.collection_id = ANY(%(scope)s) WHERE l.document_id = ANY(%(docs)s) AND t.id <> ALL(%(docs)s) "
        "UNION SELECT DISTINCT s.id, x.id FROM documents x JOIN doc_links l ON " + _resolves("x", "l") + " "
        "JOIN documents s ON s.id = l.document_id AND s.collection_id = ANY(%(scope)s) "
        "WHERE x.id = ANY(%(docs)s) AND s.id <> ALL(%(docs)s)",
        {"scope": scope, "docs": documents},
    ).fetchall()
    return list(dict.fromkeys([(r["id"], r["seed"]) for r in rows] + implicit))


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
    linked = {o["document_id"] for o in outgoing} | {i["document_id"] for i in inc}
    pairs = [(t, "mentioned here") if s == document_id else (s, "mentions this")
             for s, t in (mentions(conn, scope, [document_id]) if MENTIONS else [])]
    pairs = [(d, how) for d, how in pairs if str(d) not in linked]
    docs = {r["id"]: r for r in conn.execute(
        "SELECT d.id, d.title, col.name AS collection, "
        "(SELECT c.id FROM chunks c WHERE c.document_id = d.id ORDER BY c.ordinal LIMIT 1) AS chunk_id "
        "FROM documents d JOIN collections col ON col.id = d.collection_id WHERE d.id = ANY(%s)",
        ([d for d, _ in pairs],))} if pairs else {}
    men = [{"document_id": str(d), "title": docs[d]["title"], "collection": docs[d]["collection"],
            "chunk_id": docs[d]["chunk_id"], "how": how} for d, how in sorted(pairs, key=lambda x: str(x[0]))
           if d in docs]
    shown = linked | {m["document_id"] for m in men}
    sim = [(o, s) for _, o, s in (similar(conn, scope, [document_id], _floor(conn)) if SIMILAR else [])
           if str(o) not in shown]
    sdocs = {r["id"]: r for r in conn.execute(
        "SELECT d.id, d.title, col.name AS collection, "
        "(SELECT c.id FROM chunks c WHERE c.document_id = d.id ORDER BY c.ordinal LIMIT 1) AS chunk_id "
        "FROM documents d JOIN collections col ON col.id = d.collection_id WHERE d.id = ANY(%s)",
        ([o for o, _ in sim],))} if sim else {}
    related = [{"document_id": str(o), "title": sdocs[o]["title"], "collection": sdocs[o]["collection"],
                "chunk_id": sdocs[o]["chunk_id"], "similarity": round(s, 3)}
               for o, s in sorted(sim, key=lambda x: -x[1]) if o in sdocs]
    return {"outgoing": outgoing, "incoming": inc, "mentions": men, "similar": related, "unresolved": unresolved}


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
        for doc, _seed in sorted(neighbours(conn, scope, frontier, with_similar=True), key=lambda x: str(x[0])):
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
    if MENTIONS:
        explicit = {(e["source"], e["target"]) for e in edges}
        edges += [{"source": s, "target": t, "kind": "mentions"} for s, t in sorted(
            mentions(conn, scope, ids), key=str) if s in level and t in level and (s, t) not in explicit]
    if SIMILAR:
        seen = {frozenset((e["source"], e["target"])) for e in edges}
        for s, t, _ in sorted(similar(conn, scope, ids, _floor(conn)), key=str):
            if t in level and frozenset((s, t)) not in seen:
                seen.add(frozenset((s, t)))
                edges.append({"source": s, "target": t, "kind": "similar"})
    return {
        "document": {"id": str(root["id"]), "title": root["title"]}, "depth": depth,
        "nodes": [{"id": str(i), "title": docs[i]["title"], "collection": docs[i]["collection"], "depth": level[i],
                   "chunk_id": docs[i]["chunk_id"]} for i in ids if i in docs],
        "edges": [{"source": str(e["source"]), "target": str(e["target"]), "kind": e["kind"]} for e in edges],
        "truncated": len(level) >= MAX_GRAPH_NODES,
    }
