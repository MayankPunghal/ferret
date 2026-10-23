"""Labelled stress test on a REAL technical corpus (default: 623 Kubernetes documentation pages from
eval/make_k8s_corpus.py) with eval/k8s_queries.jsonl: 56 answerable queries (keywords, questions,
identifiers such as `CrashLoopBackOff`) and 16 that must be refused (off-topic, and near misses such as
"Docker Swarm overlay network" that sound like Kubernetes but are not covered).

    uv run python eval/make_k8s_corpus.py
    uv run --group eval python eval/corpus_eval.py --model Qwen/Qwen3-Embedding-0.6B-Q --reranker BAAI/bge-reranker-base

Ingests through the real worker into an embedded PostgreSQL (data/corpus-pg-<model>; --reuse keeps it) and
runs every query through the real search function. Reports document-level retrieval (hit@1, success@5 = a
relevant page in the top 5, MRR@10), the shipped evidence gate (answered share per category, false-answer
rate on must-refuse queries, precision of the top supported result), gates re-chosen on the val half, and
latency. Writes eval/results/corpus-<name>-<model>.json and a per-query table (.txt) to inspect.
"""

import argparse
import json
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
NEGATIVE = {"negative", "near_negative"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpora" / "kubernetes"))
    ap.add_argument("--queries", default=str(ROOT / "eval" / "k8s_queries.jsonl"))
    ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    ap.add_argument("--reranker", default="", help="cross-encoder name; '' = no reranker")
    ap.add_argument("--rerank-candidates", type=int, default=None)
    ap.add_argument("--max-far", type=float, default=0.0)
    ap.add_argument("--reuse", action="store_true", help="reuse the index of a previous run with this model")
    ap.add_argument("--context-chars", type=int, default=0, help="document summary added to each passage's embedding")
    a = ap.parse_args()
    corpus, name = Path(a.corpus), Path(a.corpus).name
    parsing.CONTEXT_CHARS = a.context_chars
    tag = a.model.replace("/", "_") + (f"-ctx{a.context_chars}" if a.context_chars else "")
    admin_url, _ = db.start_embedded(ROOT / "data" / f"corpus-pg-{tag}", database="postgres")
    if not a.reuse:
        with psycopg.connect(admin_url, autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS ks_corpus WITH (FORCE)")
            c.execute("CREATE DATABASE ks_corpus")
    url = db._embedded.get_uri("ks_corpus")
    db.migrate(url)
    db.open_pool(url, max_size=2)
    out = {"corpus": name, "model": a.model, "reranker": a.reranker or None, "context_chars": a.context_chars}
    if not a.reuse:
        blobs = BlobStore(ROOT / "data" / f"corpus-blobs-{tag}")
        with psycopg.connect(url, row_factory=dict_row) as c:
            auth.create_user(c, "tester", "eval-password-123")
            auth.grant(c, name, "staff")
            auth.add_user_to_group(c, "tester", "staff")
            runtime.save(c, runtime.validate({"embed_model": a.model}), None)
            col = auth.ensure_collection(c, name)
            for f in sorted(corpus.glob("*.md")):
                submit(c, blobs, collection_id=col, filename=f.name, data=f.read_bytes(), user_id=None,
                       max_bytes=20_000_000)
            c.commit()
        t = time.perf_counter()
        out["documents"] = Worker(blobs, 2000, 20_000_000).drain()
        out["ingest_seconds"] = round(time.perf_counter() - t, 1)

    queries = [json.loads(x) for x in Path(a.queries).read_text().splitlines() if x]
    loose = GateConfig(9, 9, 9, 99, "similarity")
    rows, lat, table = [], [], []
    with db.pool().connection() as conn:
        out["passages"] = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
        u = conn.execute("SELECT id FROM users WHERE username = 'tester'").fetchone()
        p = auth.principal_for(conn, u["id"], "tester", False)
        keys = {str(r["id"]): r["source_key"].removesuffix(".md") for r in conn.execute("SELECT id, source_key FROM documents")}
        rt = runtime.load(conn)
        rt.rerank_enabled = bool(a.reranker)
        if a.reranker:
            rt.rerank_model = a.reranker
        if a.rerank_candidates:
            rt.rerank_candidates = a.rerank_candidates
        shipped = GateConfig.from_runtime(rt)
        search(conn, p, queries[0]["query"], rt, limit=10, gate=loose)  # load models before timing
        for q in queries:
            resp = search(conn, p, q["query"], rt, limit=10, gate=loose)
            conn.commit()
            lat.append(resp.timings_ms["total"])
            res = [vars(r) for r in resp.results]
            for r in res:
                r["doc"] = keys[r["document_id"]]
            qq = {**q, "answerable": q["category"] not in NEGATIVE,
                  "_has_idents": "query_identifier_not_found" in resp.reason_codes
                  or any(r["signals"]["identifier_matches"] for r in res)}
            rows.append((qq, res))
    db.close_pool()

    def is_relevant(q, r):
        return r["doc"] in q["relevant"]

    pos = [(q, r) for q, r in rows if q["answerable"]]
    ranks = [next((i for i, x in enumerate(dict.fromkeys(y["doc"] for y in r)) if x in q["relevant"]), None)
             for q, r in pos]
    out["retrieval"] = {"n": len(pos), "hit@1": round(sum(k == 0 for k in ranks) / len(pos), 3),
                        "success@5": round(sum(k is not None and k < 5 for k in ranks) / len(pos), 3),
                        "mrr@10": round(sum(1 / (k + 1) for k in ranks if k is not None and k < 10) / len(pos), 3)}
    out["latency_ms"] = {"p50": round(statistics.median(lat), 1), "p95": round(statistics.quantiles(lat, n=20)[18], 1)}
    def answered(g, q, res):
        return [r for r in res if gate_basis(g, r["signals"], q["_has_idents"])]

    cats: dict[str, list] = {}
    for q, res in rows:
        sup = answered(shipped, q, res)
        cats.setdefault(q["category"], []).append(bool(sup))
        top = res[0] if res else None
        s = top["signals"] if top else {}
        table.append(f"{q['id']} {q['category']:13s} {'ANSWER' if sup else 'abstain':7s} "
                     f"{'ok ' if (sup and is_relevant(q, sup[0])) or (not sup and not q['answerable']) else 'BAD'} "
                     f"{q['query'][:58]:58s} top={top['doc'] if top else '-':38s} sim={s.get('cosine_similarity')} "
                     f"cov={s.get('query_term_coverage')} rr={s.get('rerank_score')}")
    out["shipped_gate"] = {"gate": vars(shipped), **{k: v for k, v in evaluate(rows, shipped, is_relevant).items()},
                           "answered_by_category": {c: round(sum(v) / len(v), 2) for c, v in sorted(cats.items())}}
    val = [x for x in rows if x[0]["split"] == "val"]
    test = [x for x in rows if x[0]["split"] == "test"]
    out["gates_chosen_on_val"] = choose(val, test, is_relevant, a.max_far)
    base = str(ROOT / "eval" / "results" / f"corpus-{name}-{tag}{'-rr-' + a.reranker.split('/')[-1] if a.reranker else ''}")
    Path(base + ".json").write_text(json.dumps(out, indent=2))
    Path(base + ".txt").write_text("\n".join(table) + "\n")
    dump_rows(Path(base + ".rows.json"), rows, is_relevant)
    sg = out["shipped_gate"]
    print(json.dumps({k: out[k] for k in ("corpus", "model", "reranker", "retrieval", "latency_ms") if k in out}))
    print(f"shipped gate {sg['gate']}\n  false answers {sg['false_answer_rate']} (n={sg['unanswerable_n']}) "
          f"{sg['false_answers']}\n  answered {sg['answer_coverage']} (n={sg['answerable_n']}) precision "
          f"{sg['precision_among_answered']}\n  by category {sg['answered_by_category']}")
    for fam, g in out["gates_chosen_on_val"].items():
        if g.get("chosen"):
            t = g["test"]
            print(f"re-chosen {fam:20s} test: false {t['false_answer_rate']} answered {t['answer_coverage']} "
                  f"precision {t['precision_among_answered']} {g['chosen']}")
    print("wrote", base + ".json", "and per-query table", base + ".txt")


if __name__ == "__main__":
    main()
