"""Scale + abstention evaluation through the real application pipeline on a public BEIR dataset.

    uv run --group eval python eval/scale_eval.py --model BAAI/bge-base-en-v1.5 \
        --reranker Xenova/ms-marco-MiniLM-L-6-v2 --max-docs 2000

How unanswerable queries are made realistic: a seeded half of the labelled test queries has *all* of its
relevant documents removed from the index. Those queries become unanswerable while topically similar papers
remain (true hard negatives). The other half stays answerable. Queries are split val/test by id parity; gate
thresholds are chosen on val and reported on test (eval/gates.py).

Everything runs through the product code: documents are ingested with ingest.submit + Worker (parse, chunk,
embed, index into an embedded PostgreSQL), and queries go through search.search with all signals recorded.
"""

import argparse
import json
import random
import statistics
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from knowledge_search import auth, db, runtime
from knowledge_search.ingest import BlobStore, Worker, submit
from knowledge_search.search import GateConfig, search

from beir_screen import load, metrics  # noqa: E402  (same directory)
from gates import choose

ROOT = Path(__file__).resolve().parent.parent


def pct(xs, p):
    return round(statistics.quantiles(xs, n=100)[p - 1], 1) if len(xs) > 2 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="scifact")
    ap.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--reranker", default="Xenova/ms-marco-MiniLM-L-6-v2")
    ap.add_argument("--rerank-candidates", type=int, default=20)
    ap.add_argument("--max-docs", type=int, default=2000)
    ap.add_argument("--chunk-size", type=int, default=900)
    ap.add_argument("--chunk-overlap", type=int, default=150)
    ap.add_argument("--max-far", type=float, default=0.05, help="val false-answer budget for choosing a gate")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    docs, qs, qrels = load(a.dataset, a.max_docs, a.seed)
    rng = random.Random(a.seed)
    order = [q for q, _ in qs]
    rng.shuffle(order)
    holdout = set(order[: len(order) // 2])
    kept_rel = {d for q in order if q not in holdout for d in qrels[q]}
    removed = {d for q in holdout for d in qrels[q] if d not in kept_rel}
    # a holdout query is only truly unanswerable if every relevant doc was removed
    unanswerable = {q for q in holdout if all(d in removed for d in qrels[q])}
    queries = [(q, t) for q, t in qs if q not in holdout or q in unanswerable]
    corpus = [(d, t) for d, t in docs if d not in removed]
    print(f"{a.dataset}: indexing {len(corpus)} docs ({len(removed)} removed); "
          f"{len(queries) - len(unanswerable)} answerable + {len(unanswerable)} unanswerable queries", flush=True)

    pg = ROOT / "data" / f"scale-pg-{a.model.replace('/', '_')}"
    admin_url, _ = db.start_embedded(pg, database="postgres")
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS ks_scale WITH (FORCE)")
        c.execute("CREATE DATABASE ks_scale")
    url = db._embedded.get_uri("ks_scale")
    db.migrate(url)
    db.open_pool(url, max_size=2)
    with psycopg.connect(url, row_factory=dict_row) as c:
        uid = auth.create_user(c, "evaluator", "evaluation-only-password", is_admin=True)
        cid = auth.ensure_collection(c, a.dataset)
        runtime.save(c, runtime.validate({
            "embed_model": a.model, "chunk_size": a.chunk_size, "chunk_overlap": a.chunk_overlap,
            "rerank_enabled": bool(a.reranker), "rerank_model": a.reranker or runtime.defaults().rerank_model,
            "rerank_candidates": a.rerank_candidates}), uid)
        blobs = BlobStore(ROOT / "data" / "scale-blobs")
        for did, text in corpus:
            title, _, body = text.partition("\n")
            submit(c, blobs, collection_id=cid, filename=f"{did}.md", data=f"# {title or did}\n\n{body}\n".encode(),
                   user_id=None, max_bytes=10_000_000)
        c.commit()
        principal = auth.principal_for(c, uid, "evaluator", True)

    t = time.perf_counter()
    n = Worker(blobs, 2000, 5_000_000).drain()
    ingest_s = time.perf_counter() - t
    with db.pool().connection() as conn:
        chunks = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
        failed = conn.execute("SELECT count(*) AS n FROM documents WHERE status <> 'indexed'").fetchone()["n"]
        keys = {str(r["id"]): r["source_key"][:-3] for r in conn.execute("SELECT id, source_key FROM documents")}
        rt = runtime.load(conn)
        loose = GateConfig(9, 9, 9, 99, "similarity")
        rows, lat, lat_embed, lat_rerank = [], [], [], []
        for qid, text in queries:
            resp = search(conn, principal, text, rt, limit=10, gate=loose)
            conn.commit()
            res = [vars(r) for r in resp.results]
            for r in res:
                r["doc"] = keys[r["document_id"]]
            q = {"id": qid, "query": text, "answerable": qid not in unanswerable,
                 "split": "val" if int(qid) % 2 else "test", "_has_idents": False}
            q["_has_idents"] = "query_identifier_not_found" in resp.reason_codes or any(
                r["signals"]["identifier_matches"] for r in res)
            rows.append((q, res))
            lat.append(resp.timings_ms["total"])
            lat_embed.append(resp.timings_ms.get("query_embedding", 0))
            lat_rerank.append(resp.timings_ms.get("rerank", 0))
    db.close_pool()

    def ranking(rows_, by_rrf: bool):
        out = {}
        for q, res in rows_:
            if not q["answerable"]:
                continue
            ordered = sorted(res, key=lambda r: -r["signals"]["rrf_score"]) if by_rrf else res
            seen = []
            for r in ordered:
                if r["doc"] not in seen:
                    seen.append(r["doc"])
            out[q["id"]] = seen
        return out

    def is_relevant(q, r):
        return r["doc"] in qrels[q["id"]]

    val = [x for x in rows if x[0]["split"] == "val"]
    test = [x for x in rows if x[0]["split"] == "test"]
    result = {
        "dataset": a.dataset, "model": a.model, "reranker": a.reranker or None,
        "rerank_candidates": a.rerank_candidates, "chunking": [a.chunk_size, a.chunk_overlap],
        "indexed_documents": n, "chunks": chunks, "not_indexed": failed,
        "ingest_seconds": round(ingest_s, 1), "docs_per_second": round(n / ingest_s, 2),
        "queries": {"answerable": sum(q["answerable"] for q, _ in rows), "unanswerable": len(unanswerable),
                    "val": len(val), "test": len(test)},
        "retrieval_answerable": {
            "hybrid_rrf": metrics(ranking(rows, True), qrels),
            **({"hybrid_rrf+rerank": metrics(ranking(rows, False), qrels)} if a.reranker else {}),
        },
        "latency_ms": {"total_p50": pct(lat, 50), "total_p95": pct(lat, 95), "query_embed_p50": pct(lat_embed, 50),
                       "rerank_p50": pct(lat_rerank, 50), "rerank_p95": pct(lat_rerank, 95)},
        "gates": choose(val, test, is_relevant, a.max_far),
        "max_far": a.max_far,
    }
    out = ROOT / "eval" / "results" / f"scale-{a.dataset}-{a.model.replace('/', '_')}-{int(time.time())}.json"
    out.write_text(json.dumps(result, indent=2))
    summary = {k: v for k, v in result.items() if k != "gates"}
    print(json.dumps(summary, indent=1))
    for fam, g in result["gates"].items():
        if g.get("chosen"):
            t = g["test"]
            print(f"gate {fam:22s} test: false-answer {t['false_answer_rate']:.3f} (n={t['unanswerable_n']}) "
                  f"coverage {t['answer_coverage']:.3f} (n={t['answerable_n']}) precision {t['precision_among_answered']:.3f}"
                  f"  | val {g['val']['false_answer_rate']:.3f}/{g['val']['answer_coverage']:.3f}")
        else:
            print(f"gate {fam:22s} {g['note']}")
    print("wrote", out)


if __name__ == "__main__":
    main()
