"""How well do implicit links (title mentions, meaning-based similarity) recover the links authors wrote?

    uv run --group eval python eval/link_eval.py            # after eval/corpus_eval.py built data/corpus-pg-<model>

Uses the Kubernetes corpus index: the pages' own Markdown links are the answer key (undirected document
pairs). For every page with at least one declared link it compares
  mentions     a passage names the other page's title (graph.mentions, what the product does today)
  similar@k    the k pages whose document vectors are closest (mean of passage vectors, cosine)
  similar>=t   every pair whose document similarity is at least t
reporting precision (share of proposed pairs the authors also linked; a LOWER bound, since authors do not
link every related page) and recall (share of declared pairs found). Writes a sample of proposed pairs the
authors did not link (eval/results/links-<model>-unlinked.txt) to judge by hand.
"""

import argparse
import json
import random
from pathlib import Path

import numpy as np

from knowledge_search import db, graph

ROOT = Path(__file__).resolve().parent.parent


def doc_vectors(conn):
    rows = conn.execute("SELECT document_id, embedding FROM chunks ORDER BY document_id, ordinal").fetchall()
    ids, vecs, cur, acc = [], [], None, []
    for r in rows + [{"document_id": None, "embedding": None}]:
        if r["document_id"] != cur and acc:
            v = np.mean(acc, axis=0)
            ids.append(cur)
            vecs.append(v / np.linalg.norm(v))
            acc = []
        cur = r["document_id"]
        if r["embedding"] is not None:
            e = r["embedding"].to_numpy().astype(np.float32)
            acc.append(e / np.linalg.norm(e))
    return ids, np.vstack(vecs)


def score(proposed: set, truth: set, linked_docs: set) -> dict:
    prop = {p for p in proposed if p[0] in linked_docs or p[1] in linked_docs}
    hit = len(prop & truth)
    return {"pairs": len(prop), "precision": round(hit / max(len(prop), 1), 3), "recall": round(hit / max(len(truth), 1), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    ap.add_argument("--db", default="", help="index directory under data/ (default corpus-pg-<model>)")
    a = ap.parse_args()
    tag = a.model.replace("/", "_")
    db.start_embedded(ROOT / "data" / (a.db or f"corpus-pg-{tag}"), database="postgres")
    db.open_pool(db._embedded.get_uri("ks_corpus"), max_size=2)
    with db.pool().connection() as conn:
        scope = [r["id"] for r in conn.execute("SELECT id FROM collections")]
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM documents")}
        declared = conn.execute(
            "SELECT DISTINCT l.document_id AS s, x.id AS t FROM doc_links l JOIN documents x ON "
            + graph._resolves("x", "l") + " WHERE x.id <> l.document_id").fetchall()
        truth = {tuple(sorted((r["s"], r["t"]))) for r in declared}
        ment = {tuple(sorted(p)) for p in graph.mentions(conn, scope, list(titles))}
        ids, vecs = doc_vectors(conn)
    db.close_pool()

    linked_docs = {d for p in truth for d in p}
    sims = vecs @ vecs.T
    np.fill_diagonal(sims, -1)
    out = {"model": a.model, "documents": len(ids), "declared_pairs": len(truth),
           "random_precision": round(len(truth) / (len(ids) * (len(ids) - 1) / 2), 4),
           "mentions": score(ment, truth, linked_docs)}
    for k in (1, 3, 5, 10):
        top = np.argsort(-sims, axis=1)[:, :k]
        out[f"similar@{k}"] = score({tuple(sorted((ids[i], ids[j]))) for i in range(len(ids)) for j in top[i]},
                                    truth, linked_docs)
    iu = np.triu_indices(len(ids), 1)
    for t in (0.95, 0.92, 0.90, 0.88, 0.85, 0.80):
        sel = sims[iu] >= t
        out[f"similar>={t}"] = score({tuple(sorted((ids[i], ids[j]))) for i, j in zip(iu[0][sel], iu[1][sel])},
                                     truth, linked_docs)
    order = np.argsort(-sims, axis=1)
    for k, t in ((3, 0.8), (3, 0.85), (5, 0.85)):
        out[f"similar@{k}>={t}"] = score({tuple(sorted((ids[i], ids[j]))) for i in range(len(ids)) for j in order[i, :k]
                                          if sims[i, j] >= t}, truth, linked_docs)
    for k in (3, 5):  # mutual: each page is among the other's k closest (no model-specific threshold)
        tk = [set(order[i, :k]) for i in range(len(ids))]
        out[f"mutual@{k}"] = score({tuple(sorted((ids[i], ids[j]))) for i in range(len(ids)) for j in tk[i] if i in tk[j]},
                                   truth, linked_docs)
    top3 = order[:, :3]
    both = ment | {tuple(sorted((ids[i], ids[j]))) for i in range(len(ids)) for j in top3[i]}
    out["mentions+similar@3"] = score(both, truth, linked_docs)
    path = ROOT / "eval" / "results" / f"links-{tag}"
    Path(str(path) + ".json").write_text(json.dumps(out, indent=2))
    rng = random.Random(7)
    idx = {d: i for i, d in enumerate(ids)}
    new = sorted({tuple(sorted((ids[i], ids[j]))) for i in range(len(ids)) for j in top3[i]} - truth)
    lines = [f"{sims[idx[s], idx[t]]:.3f}  {titles[s]}  <->  {titles[t]}" for s, t in rng.sample(new, min(40, len(new)))]
    Path(str(path) + "-unlinked.txt").write_text("\n".join(lines) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
