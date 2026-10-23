"""Hybrid retrieval (lexical + dense + exact identifiers), RRF fusion, optional cross-encoder reranking,
and the evidence gate.

All branches are filtered to the principal's readable collections *inside* SQL, so restricted documents
never enter candidate sets, ranks, counts, snippets, term statistics or coverage computations.

Scores are ranking signals, not probabilities. The response exposes an ``outcome`` with stable reason codes
instead of a confidence number.
"""

import math
import time
from dataclasses import asdict, dataclass, field

from psycopg import Connection

from . import enrich, graph, runtime, vectors
from .auth import Principal
from .config import memory_vectors
from .embedder import Embedder
from .parsing import extract_idents

RRF_K = 60
BRANCH_LIMIT = 50
GRAPH_SEEDS = 3  # link expansion: follow the links of up to this many documents ...
GRAPH_SLOTS = 5  # ... and reserve this many of the result slots for passages of linked documents
# After the top `limit` passages, the best passage of each next document is added until the results cover
# `limit` documents: often 2-3 of the top passages come from one page. Relationship questions needing several
# pages had all of them in the results 42% -> 58% of the time; nothing is removed (eval/relational_eval.py).
FILL_DOCUMENTS = True

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
    EVIDENCE_FOUND: "Relevant passages found in the indexed sources available to you. Check them before relying "
    "on a specific detail.",
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
    """Evidence gate. ``mode``: 'similarity' (cosine + lexical agreement), 'rerank' (cross-encoder score),
    or 'rerank_and_similarity' (both must agree). ``lexical``: 'coverage' (share of query terms present) or
    'idf' (share weighted by term rarity, so distinctive terms count more)."""

    dense_high: float = 0.88
    dense_min: float = 0.76
    lex_coverage: float = 0.34
    rerank_min: float = 0.0
    mode: str = "similarity"
    lexical: str = "coverage"
    all_terms_min: float = 9.0  # similarity sufficient when every query term occurs in the passage


    @classmethod
    def from_runtime(cls, rt: "runtime.Runtime") -> "GateConfig":
        mode = rt.gate_mode if rt.rerank_enabled else "similarity"
        return cls(rt.gate_dense_high, rt.gate_dense_min, rt.gate_lex_coverage, rt.gate_rerank_min, mode,
                   rt.gate_lexical, rt.gate_all_terms_min)


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
    text: str
    rank: int
    supported: bool
    match_basis: list[str]
    signals: dict
    version_no: int
    outdated_version: bool
    metadata: dict
    media_type: str
    source_uri: str | None
    source_url: str
    passage_url: str
    # written by the local language model (enrich.py), shown as such; never evidence
    summary: str | None = None
    topics: list[str] = field(default_factory=list)
    clearer_title: str | None = None  # only when the document's own title is vague


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


def _quote(lx: str) -> str:
    return "'" + lx.replace("'", "''").replace("\\", "") + "'"


def _or_tsquery(lexemes: list[str]) -> str:
    return " | ".join(_quote(lx) for lx in lexemes)


def _idf(conn: Connection, scope: list[int], lexemes: list[str]) -> dict[str, float]:
    """BM25-style IDF of each query lexeme, computed only over chunks the caller may read."""
    if not lexemes:
        return {}
    row = conn.execute(
        "SELECT count(*) AS n, " + ", ".join(
            f"count(*) FILTER (WHERE tsv @@ to_tsquery('english', %s)) AS d{i}" for i in range(len(lexemes))
        ) + " FROM chunks WHERE collection_id = ANY(%s)",
        [_quote(lx) for lx in lexemes] + [scope],
    ).fetchone()
    n = row["n"]
    return {lx: math.log(1 + (n - row[f"d{i}"] + 0.5) / (row[f"d{i}"] + 0.5)) for i, lx in enumerate(lexemes)}


def coverage_for(conn: Connection, scope: list[int], signature: str) -> dict:
    row = conn.execute(
        "SELECT count(*) AS documents, "
        "count(*) FILTER (WHERE status = 'indexed' AND indexed_version_id = latest_version_id "
        "                 AND index_signature = %s) AS indexed, "
        "count(*) FILTER (WHERE status IN ('pending', 'processing')) AS pending, "
        "count(*) FILTER (WHERE status = 'failed') AS failed, "
        "count(*) FILTER (WHERE status = 'indexed' AND (indexed_version_id IS DISTINCT FROM latest_version_id "
        "                 OR index_signature IS DISTINCT FROM %s)) AS outdated "
        "FROM documents WHERE collection_id = ANY(%s)",
        (signature, signature, scope),
    ).fetchone()
    cov = dict(row)
    cov["complete"] = cov["documents"] == cov["indexed"]
    return cov


def gate_basis(g: GateConfig, s: dict, query_has_idents: bool) -> list[str]:
    """Evidence gate for one candidate. Empty list = not adequate evidence.

    A query containing identifiers is only supported by a passage containing one of them. Otherwise the
    configured mode decides; all thresholds are tuned on labelled validation data (eval/)."""
    if s["identifier_matches"]:
        return ["exact_identifier"]
    if query_has_idents:
        return []
    sim, rr = s["cosine_similarity"], s.get("rerank_score")
    lex = s["idf_coverage"] if g.lexical == "idf" else s["query_term_coverage"]
    sim_strong = sim is not None and sim >= g.dense_high
    sim_agree = sim is not None and sim >= g.dense_min and lex >= g.lex_coverage
    # keyword searches ("parental leave policy"): short queries have low cosine similarity with every model,
    # so every query term present (title, headings or text) plus moderate similarity is also evidence
    sim_all = sim is not None and s["query_term_coverage"] >= 1.0 and sim >= g.all_terms_min
    if g.mode == "similarity":
        return (["strong_semantic"] if sim_strong else ["semantic_and_lexical_agreement"] if sim_agree
                else ["all_query_terms_and_semantic"] if sim_all else [])
    rr_ok = rr is not None and rr >= g.rerank_min
    if g.mode == "rerank":
        return ["reranker_judged_relevant"] if rr_ok else []
    # rerank_and_similarity: cross-encoder must agree with at least moderate similarity
    return ["reranker_and_similarity_agree"] if rr_ok and sim is not None and sim >= g.dense_min else []


def search(
    conn: Connection,
    principal: Principal,
    query: str,
    rt: "runtime.Runtime",
    collection_ids: list[int] | None = None,
    limit: int = 10,
    branches: tuple[str, ...] = ("lexical", "dense", "exact"),
    gate: GateConfig | None = None,
    embedder: Embedder | None = None,
) -> SearchResponse:
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    reasons: list[str] = []
    gate = gate or GateConfig.from_runtime(rt)
    query = " ".join(query.split())[:1000]
    scope = sorted(principal.readable if not collection_ids else principal.readable & set(collection_ids))
    if collection_ids:
        reasons.append("scope_filtered_by_user")
    embedder = embedder or runtime.embedder(rt.embed_model)
    model_key = embedder.spec.version_key
    in_memory = memory_vectors(runtime.base())
    index_info = {"embedding_model": embedder.spec.name, "vector_space": model_key,
                  "reranker": rt.rerank_model if rt.rerank_enabled else None, "gate": gate.mode}
    if not scope:
        return SearchResponse(query, NO_SEARCHABLE_SCOPE, MESSAGES[NO_SEARCHABLE_SCOPE], reasons, [], {}, index_info)

    cov = coverage_for(conn, scope, rt.signature())
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
            if in_memory:
                ranked["dense"] = vectors.index.top_k(conn, model_key, qvec, scope, BRANCH_LIMIT)
            else:
                rows = conn.execute(
                    "SELECT id FROM chunks WHERE collection_id = ANY(%s) AND embedding_model = %s "
                    "ORDER BY embedding <=> %s LIMIT %s",
                    (scope, model_key, qvec, BRANCH_LIMIT),
                ).fetchall()
                ranked["dense"] = [r["id"] for r in rows]
            timings["dense"] = (time.perf_counter() - t) * 1000
        except Exception:  # noqa: BLE001 - surfaced as an explicit degraded state
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 - connection lost; later queries fail and are reported
                pass
            qvec = None
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

    # Weighted RRF: the generic keyword branch is down-weighted (it adds weaker candidates for natural-language
    # questions); dense and exact-identifier branches keep full weight. Weights are tuned in eval/.
    weights = {"lexical": rt.fusion_lexical_weight}
    fused = _fuse(ranked, weights)
    order = sorted(fused, key=lambda c: (-fused[c], c))
    pool = order[: max(limit, rt.rerank_candidates if rt.rerank_enabled else 0)]
    via: dict[int, str] = {}
    if rt.graph_expansion and qvec is not None and order:
        t = time.perf_counter()
        via = _linked(conn, scope, query, order, qvec, embedder, in_memory)
        keep = max(limit - GRAPH_SLOTS, 1)
        extra = [c for c in via if c not in pool[:keep]][: limit - keep]
        if extra:
            ranked["linked"] = extra
            pool = (pool[:keep] + extra + [c for c in pool[keep:] if c not in extra])[: len(pool)]
        timings["linked"] = (time.perf_counter() - t) * 1000

    fill = _other_documents(conn, order, pool, limit) if FILL_DOCUMENTS else []
    rows_by_id: dict[int, dict] = {}
    if pool:
        rows = conn.execute(
            "SELECT c.id, c.document_id, c.doc_title, c.heading_path, c.page, c.anchor, c.idents, c.text, "
            "col.name AS collection, d.metadata, d.source_uri, d.enrichment, d.indexed_version_id, v.version_no, v.media_type, "
            "(d.indexed_version_id IS DISTINCT FROM d.latest_version_id) AS outdated, "
            "CASE WHEN %(qv)s::vector IS NULL OR %(mem)s OR c.embedding_model <> %(mk)s THEN NULL "
            "     ELSE 1 - (c.embedding <=> %(qv)s::vector) END AS sim, "
            "ARRAY(SELECT unnest(tsvector_to_array(c.tsv)) INTERSECT SELECT unnest(%(lx)s::text[])) AS lex_hit, "
            "ts_headline('english', c.text, to_tsquery('english', %(tq)s), "
            "  'StartSel=\x02, StopSel=\x03, MaxFragments=2, MaxWords=40, MinWords=15, FragmentDelimiter=\" … \"') "
            "  AS snippet "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "JOIN document_versions v ON v.id = c.version_id JOIN collections col ON col.id = c.collection_id "
            "WHERE c.id = ANY(%(ids)s) AND c.collection_id = ANY(%(scope)s)",
            {"qv": qvec, "lx": lexemes, "tq": _or_tsquery(lexemes) if lexemes else "''", "ids": pool + fill,
             "scope": scope, "mk": model_key, "mem": in_memory},
        ).fetchall()
        rows_by_id = {r["id"]: r for r in rows}
        if in_memory and qvec is not None:
            sims = vectors.index.similarities(model_key, qvec, list(rows_by_id))
            for cid, r in rows_by_id.items():
                if cid in sims:
                    r["sim"] = sims[cid]
    pool = [c for c in pool if c in rows_by_id]
    fill = [c for c in fill if c in rows_by_id]

    rerank: dict[int, float] = {}
    if rt.rerank_enabled and pool:
        t = time.perf_counter()
        try:
            texts = [f"{rows_by_id[c]['doc_title']}\n{rows_by_id[c]['heading_path']}\n{rows_by_id[c]['text']}"
                     for c in pool]
            rerank = dict(zip(pool, runtime.rerank_scores(rt.rerank_model, query, texts), strict=True))
            pool.sort(key=lambda c: -rerank[c])
        except runtime.RerankError:
            failed.append("rerank")
            reasons.append("reranker_failed")
        timings["rerank"] = (time.perf_counter() - t) * 1000

    idf = _idf(conn, scope, lexemes) if lexemes else {}
    idf_total = sum(idf.values()) or 1.0
    q_ident_set = set(q_idents)
    shown = pool[:limit]
    docs = {rows_by_id[c]["document_id"] for c in shown}
    for c in fill:
        if len(docs) >= limit:
            break
        if rows_by_id[c]["document_id"] not in docs:
            docs.add(rows_by_id[c]["document_id"])
            shown.append(c)
    if len(shown) > limit:
        ranked["more_documents"] = shown[limit:]  # not reranked: with a reranker gate they count as weaker matches
    results: list[Result] = []
    for rank, cid in enumerate(shown, start=1):
        r = rows_by_id[cid]
        hits = set(r["lex_hit"] or [])
        signals = {
            "cosine_similarity": round(float(r["sim"]), 4) if r["sim"] is not None else None,
            "query_term_coverage": round(len(hits) / len(lexemes), 3) if lexemes else 0.0,
            "idf_coverage": round(sum(idf.get(h, 0) for h in hits) / idf_total, 3) if lexemes else 0.0,
            "identifier_matches": sorted(q_ident_set & set(r["idents"])),
            "rerank_score": round(rerank[cid], 4) if cid in rerank else None,
            "branches": [b for b, ids in ranked.items() if cid in ids],
            **({"linked_from": via[cid]} if cid in via else {}),
            "rrf_score": round(fused.get(cid, 0.0), 5),
        }
        basis = gate_basis(gate, signals, bool(q_idents))
        doc_id = str(r["document_id"])
        results.append(
            Result(
                chunk_id=cid, document_id=doc_id, collection=r["collection"], title=r["doc_title"],
                heading_path=r["heading_path"], page=r["page"], anchor=r["anchor"], snippet=r["snippet"],
                text=r["text"], rank=rank, supported=bool(basis), match_basis=basis, signals=signals,
                version_no=r["version_no"], outdated_version=r["outdated"], metadata=r["metadata"],
                media_type=r["media_type"], source_uri=r["source_uri"],
                source_url=f"/api/documents/{doc_id}/view" + (f"#page={r['page']}" if r["page"] else ""),
                passage_url=f"/api/chunks/{cid}",
                **enrich.shown(r["enrichment"], r["indexed_version_id"]),
            )
        )

    supported = [r for r in results if r.supported]
    if failed and not supported and "lexical" not in ranked and "exact" not in ranked:
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
        query=query, outcome=outcome, message=MESSAGES[outcome], reason_codes=reasons, results=results,
        coverage=cov, index=index_info, timings_ms={k: round(v, 2) for k, v in timings.items()},
    )


def _other_documents(conn: Connection, order: list[int], pool: list[int], limit: int) -> list[int]:
    """The best passage (fused order) of each of the next `limit` documents that have no passage in `pool`."""
    if not order:
        return []
    doc_of = {r["id"]: r["document_id"] for r in conn.execute(
        "SELECT id, document_id FROM chunks WHERE id = ANY(%s)", (order,))}
    seen = {doc_of.get(c) for c in pool}
    out = []
    for c in order:
        d = doc_of.get(c)
        if d is not None and d not in seen and len(out) < limit:
            seen.add(d)
            out.append(c)
    return out


def _fuse(ranked: dict[str, list[int]], weights: dict[str, float]) -> dict[int, float]:
    fused: dict[int, float] = {}
    for branch, ids in ranked.items():
        w = weights.get(branch, 1.0)
        for r, cid in enumerate(ids, start=1):
            fused[cid] = fused.get(cid, 0.0) + w / (RRF_K + r)
    return fused


def _linked(conn: Connection, scope: list[int], query: str, order: list[int], qvec, embedder: Embedder,
            in_memory: bool) -> dict[int, str]:
    """Link expansion for multi-hop questions ("who owns the services X depends on"): passages of documents
    linked to/from the documents the question is about, which may never mention X themselves.

    Seeds are the documents whose title or file name the query contains, in rank order, else the
    top-ranked document (covers names like `orders-svc`; measured harmless for single-hop questions).
    Linked passages are scored against the query *without* the seed names (the link already
    covers that part: "which ports do the services that [Media Service] depends on listen on" → runtime
    sections, not the neighbours' own dependency lists). Candidates are the best passage of each linked
    document, then the second best, ..., so each related document gets a slot first.
    Only links between documents the caller can read are followed (graph.neighbours).
    Returns {chunk_id: title of the seed document it was reached from}."""
    q = query.lower()
    model_key = embedder.spec.version_key
    rows = conn.execute("SELECT id, document_id FROM chunks WHERE id = ANY(%s)", (order[:BRANCH_LIMIT],)).fetchall()
    doc_of = {r["id"]: r["document_id"] for r in rows}
    named = {}  # document -> the strings by which the query names it (removed before scoring linked passages)
    for r in conn.execute(
        "SELECT id, title, lower(regexp_replace(source_key, '\\.[^.]+$', '')) AS stem FROM documents "
        "WHERE collection_id = ANY(%(scope)s) AND (length(title) >= 4 AND strpos(%(q)s, lower(title)) > 0 "
        "OR strpos(%(q)s, lower(regexp_replace(source_key, '\\.[^.]+$', ''))) > 0)", {"scope": scope, "q": q}):
        named[r["id"]] = [n for n in (r["title"].lower(), r["stem"]) if len(n) >= 4 and n in q]
    ranked_docs = list(dict.fromkeys(doc_of[c] for c in order if c in doc_of))
    seeds = ([d for d in ranked_docs if d in named] + [d for d in named if d not in ranked_docs])[:GRAPH_SEEDS]
    seeds = seeds or ranked_docs[:1]
    if not seeds:
        return {}
    rest = q
    for n in sorted({n for d in seeds for n in named.get(d, [])}, key=len, reverse=True):
        rest = rest.replace(n, " ")
    if rest != q and rest.strip():
        qvec = embedder.embed_query(" ".join(rest.split()))
    titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM documents WHERE id = ANY(%s)", (seeds,))}
    reached = {}
    for d, seed in graph.neighbours(conn, scope, seeds):
        reached.setdefault(d, titles.get(seed, ""))
    if not reached:
        return {}
    rows = conn.execute(
        "SELECT id, document_id FROM chunks WHERE document_id = ANY(%s) AND collection_id = ANY(%s) "
        "AND embedding_model = %s", (list(reached), scope, model_key)).fetchall()
    if in_memory:
        sims = vectors.index.similarities(model_key, qvec, [r["id"] for r in rows])
    else:
        sims = {r["id"]: r["sim"] for r in conn.execute(
            "SELECT id, 1 - (embedding <=> %s) AS sim FROM chunks WHERE id = ANY(%s)", (qvec, [r["id"] for r in rows]))}
    per_doc: dict = {}
    for r in sorted(rows, key=lambda r: (-sims.get(r["id"], -1), r["id"])):
        per_doc.setdefault(r["document_id"], []).append(r["id"])
    out: dict[int, str] = {}
    for depth in range(max(len(v) for v in per_doc.values())):
        layer = [(v[depth], d) for d, v in per_doc.items() if len(v) > depth]
        for cid, d in sorted(layer, key=lambda x: (-sims.get(x[0], -1), x[0])):
            out[cid] = reached[d]
    return out


def _conflict(supported: list, reasons: list[str]) -> bool:
    """Same document series, different version/effective date among supported evidence."""
    series: dict[str, set[str]] = {}
    for r in supported:
        md = r.metadata if hasattr(r, "metadata") else r["metadata"]
        s = md.get("series")
        if s:
            series.setdefault(str(s), set()).add(f"{md.get('version', '')}|{md.get('effective_date', '')}")
    conflicting = [s for s, vs in series.items() if len(vs) > 1]
    if conflicting:
        reasons.append("same_series_different_versions")
    return bool(conflicting)
