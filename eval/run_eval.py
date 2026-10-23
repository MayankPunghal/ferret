"""Repeatable retrieval + abstention evaluation on the FICTIONAL fixture corpus (small; smoke-level evidence).

    uv run --group eval python eval/run_eval.py --model BAAI/bge-small-en-v1.5 --reranker Xenova/ms-marco-MiniLM-L-6-v2

Rebuilds database ``ks_eval`` in an embedded PostgreSQL (data/eval-pg), ingests fixtures/corpus through the
real worker, runs eval/queries.jsonl through the real search function as each query's user, and reports
retrieval metrics per branch ablation plus evidence-gate families chosen on val and reported on test
(see eval/gates.py). For a statistically meaningful abstention test at scale use eval/scale_eval.py.
"""

import argparse
import json
import math
import statistics
import time
from pathlib import Path

import psycopg
from gates import choose, dump_rows, evaluate
from psycopg.rows import dict_row

from knowledge_search import auth, db, parsing, runtime
from knowledge_search.ingest import BlobStore, Worker, submit
from knowledge_search.search import GateConfig, gate_basis, search

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"
NEGATIVE = {"unanswerable", "hard_negative", "restricted", "keyword_negative"}
VARIANTS = {"lexical": ("lexical",), "dense": ("dense",), "lexical+dense": ("lexical", "dense"),
            "full": ("lexical", "dense", "exact")}


def rel_match(rel: str, r: dict) -> tuple[bool, bool]:
    doc, _, anchor = rel.partition("#")
    doc_ok = r["doc"] == doc
    return doc_ok, doc_ok and (not anchor or r["anchor"] == anchor)


def retrieval_metrics(rows) -> dict:
    hit1 = rec5 = mrr = ndcg = phit1 = 0.0
    for q, results in rows:
        rel_docs = {x.partition("#")[0] for x in q["relevant"]}
        ranked = []
        for r in results:
            if r["doc"] not in ranked:
                ranked.append(r["doc"])
        hit1 += bool(ranked[:1] and ranked[0] in rel_docs)
        rec5 += len(rel_docs & set(ranked[:5])) / len(rel_docs)
        first = next((i for i, d in enumerate(ranked[:10], 1) if d in rel_docs), None)
        mrr += 1 / first if first else 0
        dcg = sum(1 / math.log2(i + 1) for i, d in enumerate(ranked[:10], 1) if d in rel_docs)
        ndcg += dcg / sum(1 / math.log2(i + 1) for i in range(1, min(len(rel_docs), 10) + 1))
        phit1 += bool(results and any(rel_match(x, results[0])[1] for x in q["relevant"]))
    n = len(rows) or 1
    return {k: round(v / n, 3) for k, v in
            {"hit@1": hit1, "recall@5": rec5, "mrr@10": mrr, "ndcg@10": ndcg, "passage_hit@1": phit1}.items()}


def pct(xs, p):
    return round(statistics.quantiles(xs, n=100)[p - 1], 1) if len(xs) > 2 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--reranker", default="", help="also record cross-encoder scores (enables rerank gate families)")
    ap.add_argument("--max-far", type=float, default=0.0)
    ap.add_argument("--lexical-weight", type=float, default=1.0)
    ap.add_argument("--graph", action="store_true", help="enable link expansion (graph_expansion)")
    ap.add_argument("--context-chars", type=int, default=None, help="document summary added to each passage's embedding")
    a = ap.parse_args()
    if a.context_chars is not None:
        parsing.CONTEXT_CHARS = a.context_chars

    admin_url, _ = db.start_embedded(ROOT / "data" / "eval-pg", database="postgres")
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS ks_eval WITH (FORCE)")
        c.execute("CREATE DATABASE ks_eval")
    url = db._embedded.get_uri("ks_eval")
    db.migrate(url)
    db.open_pool(url, max_size=2)
    blobs = BlobStore(ROOT / "data" / "eval-blobs")
    with psycopg.connect(url, row_factory=dict_row) as c:
        users = {}
        for u in ("alice", "bob", "hannah"):
            users[u] = auth.create_user(c, u, "eval-password-123")
        for col, groups in {"engineering": ["staff", "hr"], "finance": ["staff", "hr"], "hr": ["hr"]}.items():
            for g in groups:
                auth.grant(c, col, g)
        for u, g in [("alice", "staff"), ("bob", "staff"), ("hannah", "hr")]:
            auth.add_user_to_group(c, u, g)
        runtime.save(c, runtime.validate({"embed_model": a.model, "rerank_enabled": bool(a.reranker),
                                          "fusion_lexical_weight": a.lexical_weight, "graph_expansion": a.graph,
                                          **({"rerank_model": a.reranker} if a.reranker else {})}), None)
        for col in sorted(d for d in CORPUS.iterdir() if d.name != "platform-wiki"):  # wiki: graph demo only
            cid = auth.ensure_collection(c, col.name)
            for f in sorted(col.iterdir()):
                submit(c, blobs, collection_id=cid, filename=f.name, data=f.read_bytes(), user_id=None,
                       max_bytes=50_000_000)
        c.commit()
        principals = {u: auth.principal_for(c, uid, u, False) for u, uid in users.items()}
    t = time.perf_counter()
    n = Worker(blobs, 2000, 5_000_000).drain()
    ingest_s = time.perf_counter() - t

    queries = [json.loads(x) for x in (ROOT / "eval" / "queries.jsonl").read_text().splitlines() if x]
    loose = GateConfig(9, 9, 9, 99, "similarity")
    out = {"model": a.model, "reranker": a.reranker or None, "documents": n, "ingest_seconds": round(ingest_s, 2)}
    with db.pool().connection() as conn:
        keys = {str(r["id"]): r["source_key"] for r in conn.execute("SELECT id, source_key FROM documents")}
        rt = runtime.load(conn)
        recorded, lat = {}, []
        for name, branches in VARIANTS.items():
            rows = []
            for q in queries:
                resp = search(conn, principals[q["user"]], q["query"], rt, limit=10, branches=branches, gate=loose)
                conn.commit()
                res = [vars(r) for r in resp.results]
                for r in res:
                    r["doc"] = keys[r["document_id"]]
                qq = {**q, "answerable": q["category"] not in NEGATIVE,
                      "_has_idents": "query_identifier_not_found" in resp.reason_codes
                      or any(r["signals"]["identifier_matches"] for r in res)}
                rows.append((qq, res))
                if name == "full":
                    lat.append(resp.timings_ms["total"])
            recorded[name] = rows
    db.close_pool()
    out["retrieval"] = {k: retrieval_metrics([(q, r) for q, r in rows if q["relevant"]]) for k, rows in recorded.items()}
    out["latency_ms_full"] = {"p50": pct(lat, 50), "p95": pct(lat, 95)}

    def is_relevant(q, r):
        return any(rel_match(x, r)[0] for x in q["relevant"])

    full = recorded["full"]
    val = [x for x in full if x[0]["split"] == "val"]
    test = [x for x in full if x[0]["split"] == "test"]
    out["gates"] = choose(val, test, is_relevant, a.max_far)
    for fam in out["gates"].values():  # answered share per question category, test split
        if fam.get("chosen"):
            g = GateConfig(**fam["chosen"])
            cats: dict[str, list] = {}
            for q, res in test:
                cats.setdefault(q["category"], []).append(any(gate_basis(g, r["signals"], q["_has_idents"]) for r in res))
            fam["test_answered_by_category"] = {c: round(sum(v) / len(v), 2) for c, v in sorted(cats.items())}
    shipped = GateConfig.from_runtime(rt)
    out["shipped_gate_test"] = {"gate": vars(shipped), **evaluate(test, shipped, is_relevant)}
    path = ROOT / "eval" / "results" / f"fixtures-{a.model.replace('/', '_')}{'-rr' if a.reranker else ''}{'-graph' if a.graph else ''}{f'-ctx{a.context_chars}' if a.context_chars else ''}.json"
    path.write_text(json.dumps(out, indent=2))
    dump_rows(path.with_name(path.stem + ".rows.json"), full, is_relevant)
    print(json.dumps({k: v for k, v in out.items() if k not in ("gates",)}, indent=1))
    for fam, g in out["gates"].items():
        if g.get("chosen"):
            t = g["test"]
            print(f"gate {fam:22s} test: false-answer {t['false_answer_rate']:.3f} (n={t['unanswerable_n']}) "
                  f"coverage {t['answer_coverage']:.3f} (n={t['answerable_n']}) precision {t['precision_among_answered']:.3f}"
                  f" false={t['false_answers']}\n     chosen {g['chosen']}\n     by category {g['test_answered_by_category']}")
        else:
            print(f"gate {fam:22s} {g['note']}")
    print("wrote", path)


if __name__ == "__main__":
    main()
