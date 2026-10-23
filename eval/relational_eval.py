"""Relationship questions: does search bring up ALL the pages a question needs, and do the related documents
shown with each result fill the gaps?

    uv run --group eval python eval/corpus_eval.py --corpus data/corpora/kubernetes-nodesc     # builds the index
    uv run --group eval python eval/relational_eval.py --corpus data/corpora/kubernetes-nodesc [--reranker R]

eval/k8s_relational.jsonl: 24 questions such as "what do I need to set up so that a pod's service account
can read secrets", each needing 2-3 pages (service accounts + RBAC + secrets); any page of a group's
alternatives satisfies it (one-paragraph glossary stubs are not accepted). Scored on distinct documents:
  results       the top-10 search results (supported or not)
  with related  the results plus the related documents the UI shows with the top 3 results (links to,
                referenced by, mentions, related in meaning: /api/documents/{id}/links; up to 4 per group)
  need recall   share of a question's groups covered; all needs = every group covered
  shown         documents on screen (more is not better: each extra one is something to read)
Reuses the index of corpus_eval.py (same --corpus/--model/--context-chars). Writes
eval/results/relational-<tag>[-rr-<reranker>].json.
"""

import argparse
import json
import statistics
from pathlib import Path
from uuid import UUID

import numpy as np

from knowledge_search import auth, db, graph, runtime
from knowledge_search.search import GateConfig, search

ROOT = Path(__file__).resolve().parent.parent
TOP_WITH_RELATED = 3  # results whose related documents are counted (they are shown with every result)
SHOWN = 4  # per group, as in the UI (RELATED_SHOWN in app.js)


def shown_related(links: dict, doc: str) -> list[str]:
    """The related documents the UI shows with one result: same groups, order and cap as relatedDocs()."""
    men = links["mentions"]
    groups = [links["outgoing"], links["incoming"] + [m for m in men if m["how"] == "mentions this"],
              [m for m in men if m["how"] == "mentioned here"], links["similar"]]
    seen, out = {doc}, []
    for g in groups:
        picked = [x["document_id"] for x in g if x["document_id"] not in seen and not seen.add(x["document_id"])]
        out += picked[:SHOWN]
    return out


def score(q, docs):
    hit = [any(d in g for d in docs) for g in q["needs"]]
    return sum(hit) / len(hit), all(hit)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpora" / "kubernetes-nodesc"))
    ap.add_argument("--queries", default=str(ROOT / "eval" / "k8s_relational.jsonl"))
    ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    ap.add_argument("--reranker", default="")
    ap.add_argument("--context-chars", type=int, default=0)
    a = ap.parse_args()
    name = Path(a.corpus).name
    tag = ("" if name == "kubernetes" else f"{name}-") + a.model.replace("/", "_") + (f"-ctx{a.context_chars}" if a.context_chars else "")
    pgdir = ROOT / "data" / f"corpus-pg-{tag}"
    if not pgdir.exists():
        raise SystemExit(f"no index at {pgdir}: run eval/corpus_eval.py with the same options first")
    db.start_embedded(pgdir, database="postgres")
    db.open_pool(db._embedded.get_uri("ks_corpus"), max_size=2)
    queries = [json.loads(x) for x in Path(a.queries).read_text().splitlines() if x]
    loose = GateConfig(9, 9, 9, 99, "similarity")
    rows = []
    with db.pool().connection() as conn:
        u = conn.execute("SELECT id FROM users WHERE username = 'tester'").fetchone()
        p = auth.principal_for(conn, u["id"], "tester", False)
        keys = {str(r["id"]): r["source_key"].removesuffix(".md") for r in conn.execute("SELECT id, source_key FROM documents")}
        rt = runtime.load(conn)
        emb = runtime.embedder(rt.embed_model)
        dvec = {str(r["id"]): np.array(r["v"], dtype=np.float32) for r in conn.execute(
            "SELECT id, embedding::real[] AS v FROM documents WHERE embedding IS NOT NULL")}
        dvec = {k: v / np.linalg.norm(v) for k, v in dvec.items()}
        rk = {2: [], 3: [], 4: []}
        more = {11: [], 13: [], 16: [], 31: []}
        rt.rerank_enabled = bool(a.reranker)
        if a.reranker:
            rt.rerank_model = a.reranker
        for q in queries:
            wide = search(conn, p, q["query"], rt, limit=200, gate=loose)  # control: just more results
            wide_docs = [keys[d] for d in dict.fromkeys(r.document_id for r in wide.results)]
            for n in more:
                more[n].append(score(q, wide_docs[:n]))
            resp = search(conn, p, q["query"], rt, limit=10, gate=loose)
            ids = list(dict.fromkeys(r.document_id for r in resp.results))
            related, pool = [], {}
            for d in ids[:TOP_WITH_RELATED]:
                links = graph.document_links(UUID(d), p, conn)
                related += shown_related(links, d)
                pool[d] = [x["document_id"] for k in ("outgoing", "incoming", "mentions", "similar") for x in links[k]]
            # ranked by the question: each result's neighbours ordered by query similarity (document vectors)
            qv = emb.embed_query(q["query"])
            ranked = {}
            for d, nb in pool.items():
                nb = [x for x in dict.fromkeys(nb) if x in dvec and x not in ids]
                ranked[d] = sorted(nb, key=lambda x: -float(dvec[x] @ qv))
            conn.commit()
            res = [keys[d] for d in ids]
            both = list(dict.fromkeys(res + [keys[d] for d in related if d in keys]))
            (r1, a1), (r2, a2) = score(q, res), score(q, both)
            for k in (2, 3, 4):
                ext = list(dict.fromkeys(res + [keys[x] for d in ranked for x in ranked[d][:k]]))
                rk[k].append((*score(q, ext), len(ext)))
            rows.append({"id": q["id"], "query": q["query"], "results": res, "added_by_related": both[len(res):],
                         "need_recall": round(r1, 3), "all_needs": a1, "need_recall_related": round(r2, 3),
                         "all_needs_related": a2, "shown": len(both),
                         "missing": [g for g in q["needs"] if not any(d in g for d in both)]})
    db.close_pool()

    def mean(k):
        return round(statistics.mean(float(r[k]) for r in rows), 3)

    out = {"corpus": name, "model": a.model, "reranker": a.reranker or None, "context_chars": a.context_chars,
           "n": len(rows), "results": {"need_recall": mean("need_recall"), "all_needs": mean("all_needs")},
           "with_related": {"need_recall": mean("need_recall_related"), "all_needs": mean("all_needs_related"),
                            "shown_median": statistics.median(r["shown"] for r in rows)},
           "ranked_by_question": {f"top{k}_per_result": {
               "need_recall": round(statistics.mean(x[0] for x in v), 3),
               "all_needs": round(statistics.mean(float(x[1]) for x in v), 3),
               "shown_median": statistics.median(x[2] for x in v)} for k, v in rk.items()},
           "control_more_results": {f"first{n}_documents": {
               "need_recall": round(statistics.mean(x[0] for x in v), 3),
               "all_needs": round(statistics.mean(float(x[1]) for x in v), 3)} for n, v in more.items()},
           "rows": rows}
    path = ROOT / "eval" / "results" / f"relational-{tag}{'-rr-' + a.reranker.split('/')[-1] if a.reranker else ''}.json"
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("corpus", "reranker", "context_chars", "results", "with_related", "ranked_by_question", "control_more_results")}))
    for r in rows:
        if r["missing"]:
            print(f"  {r['id']} missing {r['missing']}  {r['query'][:70]}")


if __name__ == "__main__":
    main()
