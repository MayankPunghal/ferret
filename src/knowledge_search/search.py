"""Hybrid retrieval (lexical + dense + exact identifiers), RRF fusion and the evidence gate.

All branches are filtered to the principal's readable collections *inside* SQL, so restricted
documents never enter candidate sets, ranks, counts, snippets or coverage computations.

Scores are ranking signals, not probabilities. The response exposes an ``outcome`` with stable
reason codes instead of a confidence number.
"""

import time
from dataclasses import asdict, dataclass, field

from psycopg import Connection

from .auth import Principal
from .embedder import Embedder
from .parsing import extract_idents

RRF_K = 60
BRANCH_LIMIT = 50

# Outcomes (stable API contract)
EVIDENCE_FOUND = "evidence_found"
CONFLICTING_EVIDENCE = "conflicting_evidence"
NO_ADEQUATE_EVIDENCE = "no_adequate_evidence"
COVERAGE_INCOMPLETE = "index_coverage_incomplete"
NO_SEARCHABLE_SCOPE = "no_searchable_scope"
RETRIEVAL_DEGRADED = "retrieval_degraded"
RETRIEVAL_FAILURE = "retrieval_failure"

ANSWERED_OUTCOMES = {EVIDENCE_FOUND, CONFLICTING_EVIDENCE}

MESSAGES = {
    EVIDENCE_FOUND: "Supporting evidence found in the indexed sources available to you.",
    CONFLICTING_EVIDENCE: "Evidence found, but sources from the same document series disagree "
    "(different versions or effective dates). Check which source applies.",
    NO_ADEQUATE_EVIDENCE: "I couldn't find adequate supporting evidence in the indexed sources available to you.",
    COVERAGE_INCOMPLETE: "No adequate supporting evidence was found, but indexing of sources in your scope is "
    "incomplete (pending, failed or outdated), so absence cannot be established.",
    NO_SEARCHABLE_SCOPE: "There are no collections available to you in the selected scope.",
    RETRIEVAL_DEGRADED: "Part of the retrieval pipeline failed; results may be incomplete and absence of evidence "
    "cannot be established.",
    RETRIEVAL_FAILURE: "Search failed due to a technical error. This is not a statement about the documents.",
}


@dataclass
class GateConfig:
    dense_high: float
    dense_min: float
    lex_coverage: float


@dataclass
class Result:
    chunk_id: int
    document_id: str
    collection: str
    title: str
    heading_path: str
    page: int | None
    anchor: str | None
    snippet: str
    rank: int
    supported: bool
    match_basis: list[str]
    signals: dict
    version_no: int
    outdated_version: bool
    metadata: dict
    media_type: str
    source_uri: str | None


@dataclass
class SearchResponse:
    query: str
    outcome: str
    message: str
    reason_codes: list[str]
    results: list[Result]
    coverage: dict
    index: dict
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _lexemes(conn: Connection, query: str) -> list[str]:
    row = conn.execute("SELECT tsvector_to_array(to_tsvector('english', %s)) AS a", (query,)).fetchone()
    return list(row["a"] or [])


def _or_tsquery(lexemes: list[str]) -> str:
    return " | ".join("'" + lx.replace("'", "''").replace("\\", "") + "'" for lx in lexemes)


def coverage_for(conn: Connection, scope: list[int], model_key: str) -> dict:
    row = conn.execute(
        "SELECT count(*) AS documents, "
        "count(*) FILTER (WHERE status = 'indexed' AND indexed_version_id = latest_version_id) AS indexed, "
        "count(*) FILTER (WHERE status IN ('pending', 'processing')) AS pending, "
        "count(*) FILTER (WHERE status = 'failed') AS failed, "
        "count(*) FILTER (WHERE indexed_version_id IS DISTINCT FROM latest_version_id "
        "                 AND indexed_version_id IS NOT NULL) AS outdated "
        "FROM documents WHERE collection_id = ANY(%s)",
        (scope,),
    ).fetchone()
    mismatched = conn.execute(
        "SELECT count(DISTINCT document_id) AS n FROM chunks WHERE collection_id = ANY(%s) AND embedding_model <> %s",
        (scope, model_key),
    ).fetchone()["n"]
    cov = dict(row)
    cov["wrong_embedding_model"] = mismatched
    cov["complete"] = cov["documents"] == cov["indexed"] and mismatched == 0
    return cov


def search(
    conn: Connection,
    embedder: Embedder,
    principal: Principal,
    query: str,
    gate: GateConfig,
    collection_ids: list[int] | None = None,
    limit: int = 10,
    branches: tuple[str, ...] = ("lexical", "dense", "exact"),
) -> SearchResponse:
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    reasons: list[str] = []
    query = " ".join(query.split())[:1000]
    scope = sorted(principal.readable if not collection_ids else principal.readable & set(collection_ids))
    if collection_ids:
        reasons.append("scope_filtered_by_user")
    model_key = embedder.spec.version_key
    index_info = {"embedding_model": embedder.spec.name, "vector_space": model_key}
    if not scope:
        return SearchResponse(query, NO_SEARCHABLE_SCOPE, MESSAGES[NO_SEARCHABLE_SCOPE], reasons, [], {}, index_info)

    cov = coverage_for(conn, scope, model_key)
    ranked: dict[str, list[int]] = {}
    failed: list[str] = []
    qvec = None
    lexemes = _lexemes(conn, query)
    q_idents = extract_idents(query)

    if "dense" in branches:
        t = time.perf_counter()
        try:
            qvec = embedder.embed_query(query)
            timings["query_embedding"] = (time.perf_counter() - t) * 1000
            t = time.perf_counter()
            rows = conn.execute(
                "SELECT id FROM chunks WHERE collection_id = ANY(%s) AND embedding_model = %s "
                "ORDER BY embedding <=> %s LIMIT %s",
                (scope, model_key, qvec, BRANCH_LIMIT),
            ).fetchall()
            ranked["dense"] = [r["id"] for r in rows]
            timings["dense"] = (time.perf_counter() - t) * 1000
        except Exception:  # noqa: BLE001 - surfaced as an explicit degraded state
            conn.rollback()
            failed.append("dense")
            reasons.append("dense_branch_failed")

    if "lexical" in branches and lexemes:
        t = time.perf_counter()
        rows = conn.execute(
            "SELECT id FROM chunks, to_tsquery('english', %s) q WHERE collection_id = ANY(%s) AND tsv @@ q "
            "ORDER BY ts_rank_cd(tsv, q, 32) DESC, id LIMIT %s",
            (_or_tsquery(lexemes), scope, BRANCH_LIMIT),
        ).fetchall()
        ranked["lexical"] = [r["id"] for r in rows]
        timings["lexical"] = (time.perf_counter() - t) * 1000

    if "exact" in branches and q_idents:
        t = time.perf_counter()
        rows = conn.execute(
            "SELECT id, cardinality(ARRAY(SELECT unnest(idents) INTERSECT SELECT unnest(%s::text[]))) AS n "
            "FROM chunks WHERE collection_id = ANY(%s) AND idents && %s::text[] ORDER BY n DESC, id LIMIT %s",
            (q_idents, scope, q_idents, BRANCH_LIMIT),
        ).fetchall()
        ranked["exact"] = [r["id"] for r in rows]
        timings["exact"] = (time.perf_counter() - t) * 1000
        if not rows:
            reasons.append("query_identifier_not_found")

    fused: dict[int, float] = {}
    for ids in ranked.values():
        for r, cid in enumerate(ids, start=1):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + r)
    order = sorted(fused, key=lambda c: (-fused[c], c))[:limit]

    results: list[Result] = []
    if order:
        rows = conn.execute(
            "SELECT c.id, c.document_id, c.doc_title, c.heading_path, c.page, c.anchor, c.idents, "
            "col.name AS collection, d.metadata, d.source_uri, v.version_no, v.media_type, "
            "(d.indexed_version_id IS DISTINCT FROM d.latest_version_id) AS outdated, "
            "CASE WHEN %(qv)s::vector IS NULL THEN NULL ELSE 1 - (c.embedding <=> %(qv)s::vector) END AS sim, "
            "cardinality(ARRAY(SELECT unnest(tsvector_to_array(c.tsv)) INTERSECT SELECT unnest(%(lx)s::text[]))) "
            "  AS lex_hits, "
            "ts_headline('english', c.text, to_tsquery('english', %(tq)s), "
            "  'StartSel=\x02, StopSel=\x03, MaxFragments=2, MaxWords=40, MinWords=15, FragmentDelimiter=\" … \"') "
            "  AS snippet "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "JOIN document_versions v ON v.id = c.version_id JOIN collections col ON col.id = c.collection_id "
            "WHERE c.id = ANY(%(ids)s) AND c.collection_id = ANY(%(scope)s)",
            {
                "qv": qvec,
                "lx": lexemes,
                "tq": _or_tsquery(lexemes) if lexemes else "''",
                "ids": order,
                "scope": scope,
            },
        ).fetchall()
        by_id = {r["id"]: r for r in rows}
        q_ident_set = set(q_idents)
        for rank, cid in enumerate(order, start=1):
            r = by_id.get(cid)
            if r is None:
                continue
            sim = float(r["sim"]) if r["sim"] is not None else None
            lex_cov = (r["lex_hits"] / len(lexemes)) if lexemes else 0.0
            ident_hits = sorted(q_ident_set & set(r["idents"]))
            basis = []
            if ident_hits:
                basis.append("exact_identifier")
            if q_idents and not ident_hits:
                pass  # identifier queries need an identifier match to be supported
            elif sim is not None and sim >= gate.dense_high:
                basis.append("strong_semantic")
            elif sim is not None and sim >= gate.dense_min and lex_cov >= gate.lex_coverage:
                basis.append("semantic_and_lexical_agreement")
            branches_hit = [b for b, ids in ranked.items() if cid in ids]
            results.append(
                Result(
                    chunk_id=cid,
                    document_id=str(r["document_id"]),
                    collection=r["collection"],
                    title=r["doc_title"],
                    heading_path=r["heading_path"],
                    page=r["page"],
                    anchor=r["anchor"],
                    snippet=r["snippet"],
                    rank=rank,
                    supported=bool(basis),
                    match_basis=basis,
                    signals={
                        "cosine_similarity": round(sim, 4) if sim is not None else None,
                        "query_term_coverage": round(lex_cov, 3),
                        "identifier_matches": ident_hits,
                        "branches": branches_hit,
                        "rrf_score": round(fused[cid], 5),
                    },
                    version_no=r["version_no"],
                    outdated_version=r["outdated"],
                    metadata=r["metadata"],
                    media_type=r["media_type"],
                    source_uri=r["source_uri"],
                )
            )

    supported = [r for r in results if r.supported]
    if failed and len(failed) == len([b for b in branches if b != "exact" or q_idents]):
        outcome = RETRIEVAL_FAILURE
    elif supported:
        outcome = CONFLICTING_EVIDENCE if _conflict(supported, reasons) else EVIDENCE_FOUND
    elif failed:
        outcome = RETRIEVAL_DEGRADED
    elif not cov["complete"]:
        outcome = COVERAGE_INCOMPLETE
    else:
        outcome = NO_ADEQUATE_EVIDENCE
    if supported and not cov["complete"]:
        reasons.append("coverage_incomplete")
    if failed and supported:
        reasons.append("results_may_be_incomplete")
    if any(r.outdated_version for r in supported):
        reasons.append("evidence_from_outdated_indexed_version")
    timings["total"] = (time.perf_counter() - t0) * 1000
    return SearchResponse(
        query=query,
        outcome=outcome,
        message=MESSAGES[outcome],
        reason_codes=reasons,
        results=results,
        coverage=cov,
        index=index_info,
        timings_ms={k: round(v, 2) for k, v in timings.items()},
    )


def _conflict(supported: list[Result], reasons: list[str]) -> bool:
    """Same document series, different version/effective date among supported evidence."""
    series: dict[str, set[str]] = {}
    for r in supported:
        s = r.metadata.get("series")
        if s:
            series.setdefault(str(s), set()).add(
                f"{r.metadata.get('version', '')}|{r.metadata.get('effective_date', '')}"
            )
    conflicting = [s for s, vs in series.items() if len(vs) > 1]
    if conflicting:
        reasons.append("same_series_different_versions")
    return bool(conflicting)
