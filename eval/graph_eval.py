"""Does following document links during search help multi-hop questions? (graph-assisted retrieval)

    uv run --group eval python eval/graph_eval.py [--model BAAI/bge-base-en-v1.5] [--reranker Xenova/ms-marco-MiniLM-L-6-v2]

Generates the FICTIONAL interlinked wiki and labelled questions (eval/make_wiki.py), ingests them through
the real worker into an embedded PostgreSQL (data/graph-pg), and runs every question through the real
search function with link expansion off and on at several fusion weights, with and without the reranker.

Metrics per question class (top 10 results, the UI default):
  evidence recall   share of the labelled passages that appear in the results
  complete          share of questions with *all* labelled passages in the results
  hit@1             top result is a labelled passage
Plus, with the shipped evidence gate: false answers on questions about things that do not exist, answer
coverage on answerable ones, and p50 latency. A restricted page linked to/from public pages checks that
expansion never surfaces passages the user cannot read. Weight is chosen on val, reported on test.
"""

import argparse
import json
import re
import statistics
import time
from pathlib import Path

import make_wiki
import psycopg
from psycopg.rows import dict_row

from knowledge_search import auth, db, graph, runtime
from knowledge_search import search as search_mod
from knowledge_search.ingest import BlobStore, Worker, submit
from knowledge_search.search import ANSWERED_OUTCOMES, search

ROOT = Path(__file__).resolve().parent.parent
K = 10
RESTRICTED = {  # readable only by group "security"; linked from and to public pages
    "vault-service.md": b"---\ntitle: Vault Service\ndepends_on: [identity-service, audit-log-service]\n---\n"
                        b"# Vault Service\n\n`vault-svc` stores secrets.\n\n## Ownership\n\n"
                        b"Owned by the Obsidian team. Primary on-call: Restricted Person.\n",
}
PUBLIC_LINKING = {  # public page that links to the restricted one
    "secrets-howto.md": b"# Secrets how-to\n\nApplications read secrets through the [[Vault Service]]; owners of the "
                        b"Identity Service and Audit Log Service review access.\n",
}


def metrics(rows) -> dict:
    by: dict[str, list] = {}
    for q, res, resp in rows:
        by.setdefault(q["class"], []).append((q, res, resp))
    out = {}
    for cls, items in sorted(by.items()):
        if cls == "none":
            out[cls] = {"n": len(items), "false_answer_rate": round(
                sum(r.outcome in ANSWERED_OUTCOMES for _, _, r in items) / len(items), 3)}
            continue
        rec = comp = hit1 = answered = 0.0
        for q, res, resp in items:
            got = {f"{r['doc']}#{r['anchor']}" for r in res[:K]}
            ev = set(q["evidence"])
            rec += len(ev & got) / len(ev)
            comp += ev <= got
            hit1 += bool(res) and f"{res[0]['doc']}#{res[0]['anchor']}" in ev
            answered += resp.outcome in ANSWERED_OUTCOMES
        n = len(items)
        out[cls] = {"n": n, "evidence_recall": round(rec / n, 3), "complete": round(comp / n, 3),
                    "hit@1": round(hit1 / n, 3), "answer_coverage": round(answered / n, 3)}
    return out


def plain(data: bytes) -> bytes:
    """The page without declared links: [[X]] → X, relation keys dropped from the front matter."""
    text = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", data.decode())
    return re.sub(r"(?m)^(depends_on|part_of|related):.*\n", "", text).encode()


def ingest(a, url: str, gen: Path):
    blobs = BlobStore(ROOT / "data" / "graph-blobs")
    with psycopg.connect(url, row_factory=dict_row) as c:
        auth.create_user(c, "dev", "eval-password-123")
        auth.grant(c, "wiki", "staff")
        auth.grant(c, "restricted", "security")
        auth.add_user_to_group(c, "dev", "staff")
        runtime.save(c, runtime.validate({"embed_model": a.model}), None)
        wiki = auth.ensure_collection(c, "wiki")
        files = {f.name: f.read_bytes() for f in sorted((gen / "wiki").iterdir())} | PUBLIC_LINKING
        if a.plain:  # no declared links: plain mentions only, like crawled pages
            files = {n: plain(d) for n, d in files.items()}
        for name, data in files.items():
            submit(c, blobs, collection_id=wiki, filename=name, data=data, user_id=None, max_bytes=5_000_000)
        rid = auth.ensure_collection(c, "restricted")
        for name, data in RESTRICTED.items():
            submit(c, blobs, collection_id=rid, filename=name, data=data, user_id=None, max_bytes=5_000_000)
        c.commit()
    t = time.perf_counter()
    n_docs = Worker(blobs, 2000, 5_000_000).drain()
    return {"documents": n_docs, "ingest_seconds": round(time.perf_counter() - t, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    ap.add_argument("--reranker", default="Xenova/ms-marco-MiniLM-L-6-v2", help="'' = no reranker arms")
    ap.add_argument("--slots", default="3,5", help="result slots reserved for linked passages, per arm")
    ap.add_argument("--reuse", action="store_true", help="reuse the index built by the previous run")
    ap.add_argument("--plain", action="store_true", help="strip declared links (mentions only, like crawled pages)")
    a = ap.parse_args()

    gen = ROOT / "data" / "graph-wiki"
    make_wiki.main(gen)
    queries = [json.loads(x) for x in (gen / "queries.jsonl").read_text().splitlines() if x]
    admin_url, _ = db.start_embedded(ROOT / "data" / f"graph-pg{'-plain' * a.plain}", database="postgres")
    if not a.reuse:
        with psycopg.connect(admin_url, autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS ks_graph WITH (FORCE)")
            c.execute("CREATE DATABASE ks_graph")
    url = db._embedded.get_uri("ks_graph")
    db.migrate(url)
    db.open_pool(url, max_size=2)
    out = {"model": a.model, "k": K, "plain": a.plain, **({"reused_index": True} if a.reuse else ingest(a, url, gen)), "arms": {}}

    neighbours0 = graph.neighbours
    arms = [("hybrid", 0, False, False)] + [(f"linked slots={n}{' +mentions' * m}{' +similar' * s}", int(n), m, s)
                                            for n in a.slots.split(",") for m, s in ((False, False), (True, False), (True, True))]
    with db.pool().connection() as conn:
        u = conn.execute("SELECT id FROM users WHERE username = 'dev'").fetchone()
        principal = auth.principal_for(conn, u["id"], "dev", False)
        rid = conn.execute("SELECT id FROM collections WHERE name = 'restricted'").fetchone()["id"]
        keys = {str(r["id"]): (r["source_key"], r["collection_id"])
                for r in conn.execute("SELECT id, source_key, collection_id FROM documents")}
        base = runtime.load(conn)
        for rr in ([""] + ([a.reranker] if a.reranker else [])):
            for name, slots, mentions, similar in arms:
                rt = runtime.load(conn)
                rt.rerank_enabled, rt.graph_expansion = bool(rr), bool(slots)
                if rr:
                    rt.rerank_model = rr
                search_mod.GRAPH_SLOTS = slots or search_mod.GRAPH_SLOTS
                graph.MENTIONS, graph.SIMILAR = mentions, similar
                search_mod.graph.neighbours = (lambda c, s, d, _n=neighbours0, _w=similar: _n(c, s, d, with_similar=_w))
                rows, lat, leaks = [], [], 0
                for q in queries:
                    resp = search(conn, principal, q["query"], rt, limit=K)
                    conn.commit()
                    res = [vars(r) for r in resp.results]
                    for r in res:
                        r["doc"], col = keys[r["document_id"]]
                        leaks += col == rid
                    rows.append((q, res, resp))
                    lat.append(resp.timings_ms["total"])
                arm = f"{name}{' + rerank' if rr else ''}"
                res_arm = {split: metrics([x for x in rows if x[0]["split"] == split]) for split in ("val", "test")}
                res_arm |= {"all": metrics(rows), "latency_p50_ms": round(statistics.median(lat), 1),
                            "restricted_results": leaks}
                out["arms"][arm] = res_arm
                m = res_arm["all"]
                print(f"{arm:26s} " + " | ".join(
                    f"{c} rec {v['evidence_recall']:.2f} all {v['complete']:.2f} h1 {v['hit@1']:.2f}"
                    for c, v in m.items() if c != "none")
                    + f" | none FAR {m['none']['false_answer_rate']:.2f} | p50 {statistics.median(lat):.0f} ms"
                    f" | leaks {leaks}")
    out["gate"] = {"dense_high": base.gate_dense_high, "dense_min": base.gate_dense_min,
                   "lex_coverage": base.gate_lex_coverage}
    db.close_pool()
    path = ROOT / "eval" / "results" / f"graph-{a.model.replace('/', '_')}{'-plain' * a.plain}.json"
    path.write_text(json.dumps(out, indent=2))
    print("wrote", path)


if __name__ == "__main__":
    main()
