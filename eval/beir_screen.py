"""Screen embedding models and rerankers on a public BEIR dataset (default: SciFact, 5,183 abstracts,
300 test queries with human relevance labels). Model-only screen: exact cosine search in numpy, which
is mathematically identical to the app's exact pgvector KNN.

    uv run --group eval python eval/beir_screen.py --models BAAI/bge-small-en-v1.5 BAAI/bge-base-en-v1.5 \
        --rerankers Xenova/ms-marco-MiniLM-L-6-v2 --rerank-base BAAI/bge-base-en-v1.5

Data is downloaded to data/beir/ (SciFact is CC-BY-SA-4.0; not committed). Results: eval/results/beir-*.json.
Reports nDCG@10, Recall@10/@100, MRR@10, corpus embedding throughput, single-query embedding latency,
and reranking quality/latency on a fixed candidate pool (top-N of --rerank-base).
"""

import argparse
import json
import math
import statistics
import time
import urllib.request
from pathlib import Path

import numpy as np

from knowledge_search.embedder import MODELS, Embedder

ROOT = Path(__file__).resolve().parent.parent
HF = "https://huggingface.co/datasets/BeIR"


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, dest)
    return dest


def load(dataset: str, max_docs: int | None = None, seed: int = 0):
    import pyarrow.parquet as pq

    d = ROOT / "data" / "beir" / dataset
    if "/" in dataset:  # CQADupStack forum, e.g. cqadupstack/unix (technical Q&A)
        repo, sub = dataset.split("/")
        base, qrels_url = f"{HF}/{repo}/resolve/main/{sub}", f"{HF}/{repo}-qrels/resolve/main/{sub}/test.tsv"
    else:
        base, qrels_url = f"{HF}/{dataset}/resolve/main", f"{HF}/{dataset}-qrels/resolve/main/test.tsv"
    corpus = pq.read_table(fetch(f"{base}/corpus/corpus-00000-of-00001.parquet", d / "corpus.parquet")).to_pylist()
    queries = pq.read_table(fetch(f"{base}/queries/queries-00000-of-00001.parquet", d / "queries.parquet")).to_pylist()
    qrels: dict[str, dict[str, int]] = {}
    for line in fetch(qrels_url, d / "test.tsv").read_text().splitlines()[1:]:
        qid, did, score = line.split("\t")
        if int(score) > 0:
            qrels.setdefault(qid, {})[did] = int(score)
    docs = [(str(c["_id"]), (c.get("title") or "") + "\n" + (c.get("text") or "")) for c in corpus]
    qs = [(str(q["_id"]), q["text"]) for q in queries if str(q["_id"]) in qrels]
    if max_docs and len(docs) > max_docs:
        # Same subset for every model: all documents relevant to a test query + seeded random distractors.
        import random

        relevant = {d for rel in qrels.values() for d in rel}
        keep = [d for d in docs if d[0] in relevant]
        others = [d for d in docs if d[0] not in relevant]
        random.Random(seed).shuffle(others)
        docs = keep + others[: max(0, max_docs - len(keep))]
    return docs, qs, qrels


def metrics(ranked: dict[str, list[str]], qrels) -> dict:
    ndcg = r10 = r100 = mrr = 0.0
    for qid, ids in ranked.items():
        rel = qrels[qid]
        dcg = sum(rel.get(d, 0) / math.log2(i + 2) for i, d in enumerate(ids[:10]))
        ideal = sorted(rel.values(), reverse=True)[:10]
        ndcg += dcg / sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
        r10 += len(set(ids[:10]) & rel.keys()) / len(rel)
        r100 += len(set(ids[:100]) & rel.keys()) / len(rel)
        mrr += next((1 / (i + 1) for i, d in enumerate(ids[:10]) if d in rel), 0)
    n = len(ranked)
    return {"ndcg@10": round(ndcg / n, 4), "recall@10": round(r10 / n, 4), "recall@100": round(r100 / n, 4),
            "mrr@10": round(mrr / n, 4)}


def pct(xs, p):
    return round(statistics.quantiles(xs, n=100)[p - 1], 1)


def screen_model(name, docs, qs, qrels, cache_dir, out_dir):
    e = Embedder(name, cache_dir)
    t = time.perf_counter()
    e.warm()
    load_s = time.perf_counter() - t
    cache = out_dir / f"{name.replace('/', '_')}.npy"
    if cache.exists():
        dv, emb_s = np.load(cache), None
    else:
        t = time.perf_counter()
        dv = e.embed_documents([d for _, d in docs])
        emb_s = time.perf_counter() - t
        np.save(cache, dv)
    lat = []
    qv = []
    for _, q in qs:
        t = time.perf_counter()
        qv.append(e.embed_query(q))
        lat.append((time.perf_counter() - t) * 1000)
    sims = np.stack(qv) @ dv.T
    top = np.argsort(-sims, axis=1)[:, :100]
    ranked = {qid: [docs[j][0] for j in top[i]] for i, (qid, _) in enumerate(qs)}
    res = {"model": name, "license": MODELS[name].license, "dim": MODELS[name].dim, "model_load_s": round(load_s, 1),
           **metrics(ranked, qrels), "query_embed_ms_p50": pct(lat, 50), "query_embed_ms_p95": pct(lat, 95),
           "corpus_embed_s": round(emb_s, 1) if emb_s else "cached",
           "docs_per_s": round(len(docs) / emb_s, 1) if emb_s else "cached"}
    return res, ranked


def _scorer(name, cache_dir):
    """score(query, texts) for a cross-encoder or, for late-interaction (ColBERT) models, MaxSim computed on
    the fly (an index would store the passages' token vectors instead)."""
    from fastembed import LateInteractionTextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    if name in {m["model"] for m in LateInteractionTextEmbedding.list_supported_models()}:
        li = LateInteractionTextEmbedding(name, cache_dir=cache_dir)

        def score(q, texts):
            qv = next(iter(li.query_embed(q)))
            return [float((qv @ dv.T).max(axis=1).sum()) for dv in li.embed(texts)]
        return score
    ce = TextCrossEncoder(name, cache_dir=cache_dir)
    return lambda q, texts: list(ce.rerank(q, texts))


def screen_reranker(name, base_ranked, docs, qs, qrels, cache_dir, ns, timing_queries=25):
    """Scores the top max(ns) candidates once per query (pointwise scores, so the top-n ordering for smaller n
    is the same as reranking only n); latency is timed separately per n on the first queries."""
    text = dict(docs)
    score = _scorer(name, cache_dir)
    score("warm", ["up"])
    big = max(ns)
    t = time.perf_counter()
    all_scores = {qid: score(q, [text[c] for c in base_ranked[qid][:big]]) for qid, q in qs}
    total_s = time.perf_counter() - t
    out = []
    for n in ns:
        ranked = {}
        for qid, _ in qs:
            cands, sc = base_ranked[qid][:n], all_scores[qid][:n]
            order = sorted(range(len(cands)), key=lambda i: -sc[i])
            ranked[qid] = [cands[i] for i in order] + base_ranked[qid][n:]
        lat = []
        for qid, q in qs[:timing_queries]:
            t = time.perf_counter()
            score(q, [text[c] for c in base_ranked[qid][:n]])
            lat.append((time.perf_counter() - t) * 1000)
        m = metrics(ranked, qrels)
        out.append({"reranker": name, "candidates": n, **{k: m[k] for k in ("ndcg@10", "recall@10", "mrr@10")},
                    "rerank_ms_p50": pct(lat, 50), "rerank_ms_p95": pct(lat, 95)})
    print(f"{name}: scored {len(qs)} x {big} in {total_s:.0f} s", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="scifact")
    ap.add_argument("--models", nargs="+", default=["BAAI/bge-small-en-v1.5"])
    ap.add_argument("--rerankers", nargs="*", default=[])
    ap.add_argument("--rerank-base", default=None)
    ap.add_argument("--rerank-candidates", type=int, nargs="+", default=[30])
    ap.add_argument("--max-queries", type=int, default=None, help="seeded subsample of the test queries")
    ap.add_argument("--model-cache", default=str(ROOT / "data" / "models"))
    ap.add_argument("--max-docs", type=int, default=None, help="subsample: all relevant docs + random distractors")
    a = ap.parse_args()
    docs, qs, qrels = load(a.dataset, a.max_docs)
    if a.max_queries and len(qs) > a.max_queries:
        import random

        qs = sorted(random.Random(0).sample(qs, a.max_queries))
        qrels = {q: qrels[q] for q, _ in qs}
    out_dir = ROOT / "data" / "beir" / a.dataset / f"vectors-{len(docs)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{a.dataset}: {len(docs)} documents, {len(qs)} labelled test queries", flush=True)
    results = {"dataset": a.dataset, "documents": len(docs), "subsampled": bool(a.max_docs), "queries": len(qs), "models": [], "rerankers": []}
    rankings = {}
    for m in a.models:
        r, rankings[m] = screen_model(m, docs, qs, qrels, a.model_cache, out_dir)
        results["models"].append(r)
        print(json.dumps(r), flush=True)
    base = a.rerank_base or a.models[0]
    if a.rerankers and base not in rankings:
        _, rankings[base] = screen_model(base, docs, qs, qrels, a.model_cache, out_dir)
    for rr in a.rerankers:
        for r in screen_reranker(rr, rankings[base], docs, qs, qrels, a.model_cache, a.rerank_candidates):
            r["base"] = base
            results["rerankers"].append(r)
            print(json.dumps(r), flush=True)
    out = ROOT / "eval" / "results" / f"beir-{a.dataset.replace('/', '-')}-{int(time.time())}.json"
    out.write_text(json.dumps(results, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main()
