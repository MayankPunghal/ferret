"""Repeatable retrieval + abstention evaluation on the FICTIONAL fixture corpus.

    uv run python eval/run_eval.py --models BAAI/bge-small-en-v1.5 [more models...]

For each model: rebuilds database ``ks_eval`` from scratch, ingests fixtures/corpus through the real
ingestion worker, runs eval/queries.jsonl through the real search function as each query's user,
and reports:
  * retrieval metrics on answerable queries (doc-level Hit@1, Recall@5, MRR@10, nDCG@10; passage Hit@1)
    for the branch ablations lexical / dense / lexical+dense / full (lexical+dense+exact);
  * abstention: false-answer rate on unanswerable, hard-negative and restricted queries, answer coverage
    and precision among answered queries, at the configured gate and over a threshold sweep
    (risk/coverage); thresholds are chosen on the ``val`` split and reported on the held-out ``test`` split;
  * warm latency p50/p95 (total and query embedding), ingestion time.
Results are written to eval/results/<model>.json. Small fixture set: treat numbers as smoke-level evidence,
not calibration.
"""

import argparse
import json
import math
import statistics
import time
from itertools import product
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from knowledge_search import auth, db
from knowledge_search.embedder import MODELS, Embedder
from knowledge_search.ingest import BlobStore, Worker, submit
from knowledge_search.search import ANSWERED_OUTCOMES, GateConfig, _conflict, gate_basis, search

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"
NEGATIVE = {"unanswerable", "hard_negative", "restricted"}
VARIANTS = {
    "lexical": ("lexical",),
    "dense": ("dense",),
    "lexical+dense": ("lexical", "dense"),
    "full": ("lexical", "dense", "exact"),
}


def setup_db(admin_url: str, url: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS ks_eval WITH (FORCE)")
        c.execute("CREATE DATABASE ks_eval")
    db.migrate(url)
    with psycopg.connect(url, row_factory=dict_row) as c:
        for u in ("alice", "bob", "hannah"):
            auth.create_user(c, u, "eval-password-123")
        for col, groups in {"engineering": ["staff", "hr"], "finance": ["staff", "hr"], "hr": ["hr"]}.items():
            for g in groups:
                auth.grant(c, col, g)
        for u, g in [("alice", "staff"), ("bob", "staff"), ("hannah", "hr")]:
            auth.add_user_to_group(c, u, g)


def ingest(url: str, embedder: Embedder, data_dir: Path) -> dict:
    blobs = BlobStore(data_dir)
    with psycopg.connect(url, row_factory=dict_row) as c:
        for col in sorted(CORPUS.iterdir()):
            cid = auth.ensure_collection(c, col.name)
            for f in sorted(col.iterdir()):
                submit(c, blobs, collection_id=cid, filename=f.name, data=f.read_bytes(), user_id=None,
                       max_bytes=50_000_000)
                c.commit()
    t = time.perf_counter()
    n = Worker(embedder, blobs, 2000, 5_000_000).drain()
    secs = time.perf_counter() - t
    with db.pool().connection() as c:
        chunks = c.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
        failed = c.execute("SELECT count(*) AS n FROM documents WHERE status <> 'indexed'").fetchone()["n"]
    return {"documents": n, "chunks": chunks, "not_indexed": failed, "seconds": round(secs, 2)}


def rel_match(rel: str, r: dict, keys: dict) -> tuple[bool, bool]:
    doc, _, anchor = rel.partition("#")
    doc_ok = keys[r["document_id"]] == doc
    return doc_ok, doc_ok and (not anchor or r["anchor"] == anchor)


def retrieval_metrics(rows: list[dict], keys: dict) -> dict:
    hit1 = rec5 = mrr = ndcg = phit1 = 0.0
    for q, results in rows:
        rel_docs = {x.partition("#")[0] for x in q["relevant"]}
        ranked_docs = []
        for r in results:  # doc-level ranking: first occurrence of each document
            k = keys[r["document_id"]]
            if k not in ranked_docs:
                ranked_docs.append(k)
        hit1 += bool(ranked_docs[:1] and ranked_docs[0] in rel_docs)
        rec5 += len(rel_docs & set(ranked_docs[:5])) / len(rel_docs)
        first = next((i for i, d in enumerate(ranked_docs[:10], 1) if d in rel_docs), None)
        mrr += 1 / first if first else 0
        dcg = sum(1 / math.log2(i + 1) for i, d in enumerate(ranked_docs[:10], 1) if d in rel_docs)
        idcg = sum(1 / math.log2(i + 1) for i in range(1, min(len(rel_docs), 10) + 1))
        ndcg += dcg / idcg
        phit1 += bool(results and any(rel_match(x, results[0], keys)[1] for x in q["relevant"]))
    n = len(rows) or 1
    return {k: round(v / n, 3) for k, v in
            {"hit@1": hit1, "recall@5": rec5, "mrr@10": mrr, "ndcg@10": ndcg, "passage_hit@1": phit1}.items()}


def regate(q: dict, results: list[dict], gate: GateConfig, keys: dict) -> dict:
    """Re-apply the evidence gate offline to the recorded candidates (same logic as search())."""
    has_idents = "query_identifier_not_found" in q["_reasons"] or any(r["signals"]["identifier_matches"] for r in results)
    supported = []
    for r in results:
        s = r["signals"]
        if gate_basis(gate, s["cosine_similarity"], s["query_term_coverage"], s["identifier_matches"], has_idents):
            supported.append(r)
    answered = bool(supported)

    class R:  # minimal adapter for _conflict
        def __init__(self, d):
            self.metadata = d["metadata"]

    conflict = answered and _conflict([R(r) for r in supported], [])
    correct = answered and any(rel_match(x, supported[0], keys)[0] for x in q["relevant"])
    return {"answered": answered, "correct": correct, "conflict": conflict}


def abstention_metrics(rows: list[dict], gate: GateConfig, keys: dict) -> dict:
    neg = [(q, r) for q, r in rows if q["category"] in NEGATIVE]
    pos = [(q, r) for q, r in rows if q["category"] not in NEGATIVE]
    g_neg = [regate(q, r, gate, keys) for q, r in neg]
    g_pos = [regate(q, r, gate, keys) for q, r in pos]
    answered = [g for g in g_pos if g["answered"]]
    conflicts = [regate(q, r, gate, keys)["conflict"] for q, r in pos if q["category"] == "conflict"]
    false_answers = [q["id"] + ":" + q["query"] for (q, _), g in zip(neg, g_neg) if g["answered"]]
    return {
        "unanswerable_n": len(neg),
        "false_answer_rate": round(sum(g["answered"] for g in g_neg) / max(len(neg), 1), 3),
        "false_answers": false_answers,
        "answerable_n": len(pos),
        "answer_coverage": round(len(answered) / max(len(pos), 1), 3),
        "precision_among_answered": round(
            sum(g["correct"] for g in answered) / max(len(answered) + sum(g["answered"] for g in g_neg), 1), 3),
        "conflict_detected": f"{sum(conflicts)}/{len(conflicts)}",
        "missed_answerable": [q["id"] + ":" + q["query"] for (q, _), g in zip(pos, g_pos) if not g["answered"]],
    }


def rerank_experiment(conn, model: str, cache: str, val, test, keys) -> dict:
    """Evidence gate = exact identifier OR cross-encoder score >= T, on the same recorded candidates."""
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    t = time.perf_counter()
    ce = TextCrossEncoder(model, cache_dir=cache)
    load = time.perf_counter() - t
    lat = []

    def scored(rows):
        out = []
        for q, results in rows:
            texts = [conn.execute("SELECT doc_title || E'\\n' || heading_path || E'\\n' || text AS t FROM chunks "
                                  "WHERE id = %s", (r["chunk_id"],)).fetchone()["t"] for r in results]
            t0 = time.perf_counter()
            scores = list(ce.rerank(q["query"], texts)) if texts else []
            lat.append((time.perf_counter() - t0) * 1000)
            out.append((q, results, scores))
        return out

    def metrics(rows, thr):
        neg_ans = pos_ans = pos_ok = n_neg = n_pos = 0
        fa = []
        for q, results, scores in rows:
            has_idents = "query_identifier_not_found" in q["_reasons"] or any(
                r["signals"]["identifier_matches"] for r in results)
            sup = [(sc, r) for r, sc in zip(results, scores)
                   if r["signals"]["identifier_matches"] or (not has_idents and sc >= thr)]
            sup.sort(key=lambda x: -x[0] if not x[1]["signals"]["identifier_matches"] else -1e9)
            if q["category"] in NEGATIVE:
                n_neg += 1
                if sup:
                    neg_ans += 1
                    fa.append(q["id"])
            else:
                n_pos += 1
                if sup:
                    pos_ans += 1
                    pos_ok += any(rel_match(x, sup[0][1], keys)[0] for x in q["relevant"])
        return {"threshold": thr, "false_answer_rate": round(neg_ans / n_neg, 3), "answer_coverage": round(pos_ans / n_pos, 3),
                "precision_among_answered": round(pos_ok / max(pos_ans + neg_ans, 1), 3), "false_answers": fa}

    sv, st = scored(val), scored(test)
    grid = sorted({round(x, 2) for _, _, sc in sv for x in sc})
    sweep = [metrics(sv, thr) for thr in grid]
    safe = [m for m in sweep if m["false_answer_rate"] == 0]
    best = max(safe, key=lambda m: (m["answer_coverage"], m["threshold"])) if safe else None
    return {"model_load_seconds": round(load, 2), "rerank10_ms_p50": pct(lat, 50), "rerank10_ms_p95": pct(lat, 95),
            "chosen_on_val": best, "test": metrics(st, best["threshold"]) if best else None}


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1) if xs else None


def run_model(model: str, args) -> dict:
    url = args.database_url
    setup_db(args.admin_url, url)
    db.close_pool()
    db.open_pool(url, max_size=2)
    embedder = Embedder(model, args.model_cache)
    t = time.perf_counter()
    embedder.warm()
    load_s = time.perf_counter() - t
    data_dir = Path(args.data_dir) / model.replace("/", "_")
    ing = ingest(url, embedder, data_dir)
    queries = [json.loads(line) for line in (ROOT / "eval" / "queries.jsonl").read_text().splitlines() if line]
    loose = GateConfig(dense_high=9, dense_min=9, lex_coverage=9)  # gate irrelevant for retrieval metrics
    out: dict = {"model": model, "license": MODELS[model].license, "model_load_seconds": round(load_s, 2),
                 "ingestion": ing, "queries": len(queries)}
    with db.pool().connection() as conn:
        keys = {str(r["id"]): r["source_key"] for r in conn.execute("SELECT id, source_key FROM documents")}
        principals = {u: auth.principal_for(conn, r["id"], u, False) for u in ("alice", "bob", "hannah")
                      for r in [conn.execute("SELECT id FROM users WHERE username = %s", (u,)).fetchone()]}
        recorded: dict[str, list] = {}
        lat_total, lat_embed = [], []
        for name, branches in VARIANTS.items():
            rows = []
            for q in queries:
                resp = search(conn, embedder, principals[q["user"]], q["query"], loose, limit=10, branches=branches)
                conn.commit()
                results = [vars(r) for r in resp.results]
                q = {**q, "_reasons": resp.reason_codes}
                rows.append((q, results))
                if name == "full":
                    lat_total.append(resp.timings_ms["total"])
                    lat_embed.append(resp.timings_ms.get("query_embedding", 0))
            recorded[name] = rows
        out["retrieval"] = {
            name: retrieval_metrics([(q, r) for q, r in rows if q["relevant"]], keys) for name, rows in recorded.items()
        }
        out["latency_ms_warm_sequential"] = {
            "total_p50": pct(lat_total, 50), "total_p95": pct(lat_total, 95),
            "query_embedding_p50": pct(lat_embed, 50), "query_embedding_p95": pct(lat_embed, 95),
        }
        full = recorded["full"]
        val = [(q, r) for q, r in full if q["split"] == "val"]
        test = [(q, r) for q, r in full if q["split"] == "test"]
        # Threshold sweep on val: risk/coverage curve; choose max coverage with zero val false answers.
        sweep = []
        for hi, lo, cov in product([0.76, 0.78, 0.80, 0.82, 0.84, 0.86, 0.88],
                                   [0.60, 0.64, 0.68, 0.72, 0.76], [0.34, 0.5, 0.67]):
            if lo >= hi:
                continue
            m = abstention_metrics(val, GateConfig(hi, lo, cov), keys)
            sweep.append({"dense_high": hi, "dense_min": lo, "lex_coverage": cov,
                          "false_answer_rate": m["false_answer_rate"], "answer_coverage": m["answer_coverage"],
                          "precision_among_answered": m["precision_among_answered"]})
        safe = [s for s in sweep if s["false_answer_rate"] == 0]
        best = max(safe, key=lambda s: (s["answer_coverage"], s["dense_high"], s["dense_min"])) if safe else None
        out["threshold_sweep_val"] = sweep
        out["chosen_on_val"] = best
        if best:
            g = GateConfig(best["dense_high"], best["dense_min"], best["lex_coverage"])
            out["abstention_val"] = abstention_metrics(val, g, keys)
            out["abstention_test"] = abstention_metrics(test, g, keys)
        for rr in args.rerankers:
            out.setdefault("reranker_gate", {})[rr] = rerank_experiment(conn, rr, args.model_cache, val, test, keys)
        cfg = GateConfig(args.gate_high, args.gate_min, args.gate_cov)
        out["abstention_test_at_configured_gate"] = {"gate": vars(cfg), **abstention_metrics(test, cfg, keys)}
    db.close_pool()
    return out


def summarize(r: dict) -> str:
    lines = [f"## {r['model']} ({r['license']})",
             f"ingest: {r['ingestion']}  model load: {r['model_load_seconds']}s  latency: {r['latency_ms_warm_sequential']}",
             "| variant | hit@1 | recall@5 | mrr@10 | ndcg@10 | passage hit@1 |", "|---|---|---|---|---|---|"]
    for k, m in r["retrieval"].items():
        lines.append(f"| {k} | {m['hit@1']} | {m['recall@5']} | {m['mrr@10']} | {m['ndcg@10']} | {m['passage_hit@1']} |")
    lines.append(f"chosen on val: {r['chosen_on_val']}")
    for k in ("abstention_val", "abstention_test", "abstention_test_at_configured_gate"):
        if k in r:
            a = r[k]
            lines.append(f"{k}: FAR={a['false_answer_rate']} (n={a['unanswerable_n']}) coverage={a['answer_coverage']} "
                         f"(n={a['answerable_n']}) precision={a['precision_among_answered']} conflicts={a['conflict_detected']}")
            if a["false_answers"]:
                lines.append(f"  false answers: {a['false_answers']}")
            if a["missed_answerable"]:
                lines.append(f"  abstained on answerable: {a['missed_answerable']}")
    for rr, m in r.get("reranker_gate", {}).items():
        lines.append(f"reranker gate {rr}: load {m['model_load_seconds']}s, rerank top-10 p50/p95 "
                     f"{m['rerank10_ms_p50']}/{m['rerank10_ms_p95']} ms; val choice {m['chosen_on_val']}; test {m['test']}")
    return "\n".join(lines)


def main():
    from knowledge_search.config import get_settings

    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=[s.embed_model])
    ap.add_argument("--admin-url", default=s.database_url)
    ap.add_argument("--database-url", default=s.database_url.rsplit("/", 1)[0] + "/ks_eval")
    ap.add_argument("--model-cache", default=str(s.model_cache))
    ap.add_argument("--data-dir", default=str(ROOT / "data" / "eval"))
    ap.add_argument("--rerankers", nargs="*", default=[])
    ap.add_argument("--gate-high", type=float, default=s.gate_dense_high)
    ap.add_argument("--gate-min", type=float, default=s.gate_dense_min)
    ap.add_argument("--gate-cov", type=float, default=s.gate_lex_coverage)
    args = ap.parse_args()
    (ROOT / "eval" / "results").mkdir(exist_ok=True)
    for m in args.models:
        r = run_model(m, args)
        (ROOT / "eval" / "results" / (m.replace("/", "_") + ".json")).write_text(json.dumps(r, indent=2))
        print(summarize(r), "\n", flush=True)


if __name__ == "__main__":
    main()
