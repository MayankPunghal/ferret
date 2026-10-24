"""Retrieval tricks, one at a time, through the real search function (docs/research-search.md, round 2).

    mkdir -p data/corpora/k8s-sub-gemma && for f in data/corpora/k8s-sub/*.md; do
        cp "data/corpora/kubernetes-nodesc-gemma-4-e2b/$(basename "$f")" data/corpora/k8s-sub-gemma/; done
    uv run --group eval python eval/tricks.py                       # baseline + query-time tricks
    uv run --group eval python eval/tricks.py --overlap 0           # an index-time variant (own database)
    uv run --group eval python eval/tricks.py --make-questions http://127.0.0.1:8090/v1   # then --questions

Corpus default: 171 Kubernetes pages without author descriptions; each file's front-matter description is the
Gemma 4 E2B summary (like the app's background summaries), used only where a trick says so. Queries: the
sub-corpus set (27 answerable, 16 must refuse) + real Stack Overflow questions whose answer links a page here
(eval/so_k8s_queries.jsonl). Reports document-level hit@1 / success@5 / MRR@10 per query set and the shipped
evidence gate (answered share, false answers on must-refuse queries) for each variant, with and without the
reranker. Variants that change the index get their own database (data/tricks-pg-<tag>).
"""

import argparse
import json
import time
from pathlib import Path

import psycopg
from gates import evaluate
from psycopg.rows import dict_row

from knowledge_search import auth, db, enrich, runtime
from knowledge_search import search as S
from knowledge_search.ingest import BlobStore, Worker, submit

ROOT = Path(__file__).resolve().parent.parent
NEGATIVE = {"negative", "near_negative"}
QPROMPT = """Here is a document. Write up to 8 questions that a colleague might type into a search box and that \
this document answers. Use their own words, not the headings; cover different sections. For each question give \
the section (from the list of sections) where the answer is.
Reply as JSON: {{"questions": [{{"q": "...", "section": "..."}}]}}
Title: {title}
{outline}Text:
{text}"""


def setup_tables(conn, emb, questions: dict | None, keyword_context: bool) -> None:
    key = emb.spec.version_key
    conn.execute("CREATE TABLE IF NOT EXISTS summary_vectors (document_id uuid PRIMARY KEY, collection_id bigint, "
                 "embedding_model text, embedding vector)")
    if not conn.execute("SELECT 1 FROM summary_vectors LIMIT 1").fetchone():
        docs = conn.execute("SELECT id, collection_id, title, metadata->>'description' AS s FROM documents "
                            "WHERE metadata ? 'description'").fetchall()
        vecs = emb.embed_documents([f"{d['title']}\n{d['s']}" for d in docs])
        for d, v in zip(docs, vecs, strict=True):
            conn.execute("INSERT INTO summary_vectors VALUES (%s, %s, %s, %s)", (d["id"], d["collection_id"], key, v))
    conn.execute("CREATE TABLE IF NOT EXISTS question_vectors (id bigserial PRIMARY KEY, document_id uuid, "
                 "collection_id bigint, embedding_model text, heading_path text, question text, embedding vector)")
    if questions and not conn.execute("SELECT 1 FROM question_vectors LIMIT 1").fetchone():
        for d in conn.execute("SELECT id, collection_id, source_key FROM documents").fetchall():
            qs = questions.get(d["source_key"], [])
            heads = [r["heading_path"] for r in conn.execute(
                "SELECT DISTINCT heading_path FROM chunks WHERE document_id = %s", (d["id"],))]
            for q, v in zip(qs, emb.embed_query_batch([x["q"] for x in qs]) if qs else [], strict=True):
                sec = (q.get("section") or "").lower().strip()
                head = next((h for h in heads if sec and h.lower().endswith(sec)), None) or \
                    next((h for h in heads if sec and sec in h.lower()), None)
                conn.execute("INSERT INTO question_vectors (document_id, collection_id, embedding_model, heading_path, "
                             "question, embedding) VALUES (%s, %s, %s, %s, %s, %s)",
                             (d["id"], d["collection_id"], key, head, q["q"], v))
    if keyword_context and not conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'chunks' AND column_name = 'context'").fetchone():
        conn.execute("ALTER TABLE chunks ADD COLUMN context text NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE chunks DROP COLUMN tsv")
        conn.execute("ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS ("
                     "setweight(to_tsvector('english', doc_title), 'A') || setweight(to_tsvector('english', heading_path), 'B') || "
                     "setweight(to_tsvector('english', text), 'C') || setweight(to_tsvector('english', context), 'D')) STORED")
        conn.execute("CREATE INDEX ON chunks USING gin (tsv)")
        conn.execute("UPDATE chunks c SET context = d.metadata->>'description' FROM documents d "
                     "WHERE d.id = c.document_id AND d.metadata ? 'description'")
    conn.commit()


def neighbour_vectors(conn, emb, contexts: dict) -> None:
    """Embed each passage with the local model's summary of its neighbours (eval/neighbour_context.py)."""
    rows = conn.execute("SELECT c.id, d.source_key, c.ordinal, c.doc_title, c.heading_path, c.text FROM chunks c "
                        "JOIN documents d ON d.id = c.document_id ORDER BY c.id").fetchall()
    missing = [r for r in rows if f"{r['source_key']}#{r['ordinal']}" not in contexts]
    print(f"neighbour context: {len(rows) - len(missing)} of {len(rows)} passages", flush=True)
    texts = []
    for r in rows:
        head = r["doc_title"] if not r["heading_path"] else f"{r['doc_title']} — {r['heading_path']}"
        ctx = contexts.get(f"{r['source_key']}#{r['ordinal']}", "")
        texts.append(f"{head}\n{ctx}\n{r['text']}" if ctx else f"{head}\n{r['text']}")
    for i in range(0, len(rows), 256):
        for r, v in zip(rows[i:i + 256], emb.embed_documents(texts[i:i + 256]), strict=True):
            conn.execute("UPDATE chunks SET embedding = %s WHERE id = %s", (v, r["id"]))
    conn.execute("CREATE TABLE IF NOT EXISTS neighbour_done (n int)")
    conn.execute("INSERT INTO neighbour_done VALUES (%s)", (len(rows) - len(missing),))
    conn.commit()


def make_questions(conn, url: str, path: Path) -> None:
    done = json.loads(path.read_text()) if path.exists() else {}
    docs = conn.execute("SELECT id, source_key FROM documents ORDER BY source_key").fetchall()
    for n, d in enumerate(docs):
        if d["source_key"] in done:
            continue
        title, outline, text = enrich.source_text(conn, d["id"])
        t = time.time()
        try:
            raw = enrich.chat(url, "", QPROMPT.format(title=title, outline=outline, text=text), max_tokens=600)
            qs = [q for q in json.loads(raw[raw.index("{"):]).get("questions", []) if isinstance(q, dict) and q.get("q")]
        except (enrich.LLMError, ValueError):
            qs = []
        done[d["source_key"]] = qs[:8]
        path.write_text(json.dumps(done, indent=1))
        print(f"{n + 1}/{len(docs)} {d['source_key']}: {len(qs)} questions, {time.time() - t:.0f} s", flush=True)


def doc_rank(q, results, keys):
    order = list(dict.fromkeys(keys[r.document_id] for r in results))
    return next((i for i, x in enumerate(order) if x in q["relevant"]), None)


def run(conn, p, queries, rt, keys, gate):
    rows, ranks, lat = [], [], []
    for q in queries:
        t = time.perf_counter()
        resp = S.search(conn, p, q["query"], rt, limit=10)
        conn.commit()
        lat.append(time.perf_counter() - t)
        res = [vars(r) | {"doc": keys[r.document_id]} for r in resp.results]
        rows.append(({**q, "_has_idents": "query_identifier_not_found" in resp.reason_codes
                      or any(r["signals"]["identifier_matches"] for r in res)}, res))
        ranks.append(doc_rank(q, resp.results, keys) if q["answerable"] else None)
    out = {}
    for name, keep in [("so", lambda q: q["category"] == "so_question"), ("sub", lambda q: q["category"] != "so_question")]:
        idx = [i for i, (q, _) in enumerate(rows) if keep(q)]
        pos = [i for i in idx if rows[i][0]["answerable"]]
        rk = [ranks[i] for i in pos]
        g = evaluate([rows[i] for i in idx], gate, lambda q, r: r["doc"] in q["relevant"])
        out[name] = {"n": len(pos), "hit@1": round(sum(k == 0 for k in rk) / len(pos), 3),
                     "success@5": round(sum(k is not None and k < 5 for k in rk) / len(pos), 3),
                     "mrr@10": round(sum(1 / (k + 1) for k in rk if k is not None and k < 10) / len(pos), 3),
                     "answered": g["answer_coverage"], "precision": g["precision_among_answered"],
                     **({"false_answers": f"{len(g['false_answers'])}/{g['unanswerable_n']}"} if g["unanswerable_n"] else {})}
    out["seconds_per_query"] = round(sum(lat) / len(lat), 2)
    return out, ranks


VARIANTS = {  # name: module settings in knowledge_search.search
    "baseline": {},
    "summary 0.5": {"SUMMARY_WEIGHT": 0.5}, "summary 1.0": {"SUMMARY_WEIGHT": 1.0},
    "questions 0.5": {"QUESTION_WEIGHT": 0.5}, "questions 1.0": {"QUESTION_WEIGHT": 1.0},
    "summary+questions 0.5": {"SUMMARY_WEIGHT": 0.5, "QUESTION_WEIGHT": 0.5},
    "neighbours 300": {"RERANK_NEIGHBOURS": 300}, "neighbours 900": {"RERANK_NEIGHBOURS": 900},
    "convex 0.5": {"FUSION": "convex", "CONVEX_ALPHA": 0.5}, "convex 0.7": {"FUSION": "convex", "CONVEX_ALPHA": 0.7},
    "convex 0.9": {"FUSION": "convex", "CONVEX_ALPHA": 0.9},
    "hyde rescue": {"HYDE_URL": "from --hyde-url"},
    "rrf k 20": {"RRF_K": 20}, "rrf k 120": {"RRF_K": 120},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpora" / "k8s-sub-gemma"))
    ap.add_argument("--queries", nargs="+", default=[str(ROOT / "eval" / "k8s_sub_queries.jsonl"),
                                                     str(ROOT / "eval" / "so_k8s_queries.jsonl")])
    ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    ap.add_argument("--rerankers", nargs="+", default=["", "jinaai/jina-reranker-v1-turbo-en"])
    ap.add_argument("--chunk-size", type=int, default=900)
    ap.add_argument("--overlap", type=int, default=150)
    ap.add_argument("--keyword-context", action="store_true", help="summary in the keyword index (weight D)")
    ap.add_argument("--context-chars", type=int, default=0, help="document summary added to every passage's "
                    "embedding, cut to this many characters (the app's context_chars setting)")
    ap.add_argument("--neighbour-context", default="", help="JSON from eval/neighbour_context.py: embed passages "
                    "with the summary of their neighbours (use with --overlap 0)")
    ap.add_argument("--questions", default="", help="JSON from --make-questions: enables the questions variants")
    ap.add_argument("--make-questions", default="", help="local model server URL: write questions per document")
    ap.add_argument("--hyde-url", default="")
    ap.add_argument("--variants", nargs="+", default=None)
    ap.add_argument("--name", default="")
    a = ap.parse_args()
    corpus = Path(a.corpus)
    tag = f"{corpus.name}-c{a.chunk_size}-o{a.overlap}" + ("-kw" if a.keyword_context else "") + ("-nb" if a.neighbour_context else "") + (f"-ctx{a.context_chars}" if a.context_chars else "")
    admin_url, _ = db.start_embedded(ROOT / "data" / f"tricks-pg-{tag}", database="postgres")
    with psycopg.connect(admin_url, autocommit=True) as c:
        fresh = not c.execute("SELECT 1 FROM pg_database WHERE datname = 'ks_tricks'").fetchone()
        if fresh:
            c.execute("CREATE DATABASE ks_tricks")
    url = db._embedded.get_uri("ks_tricks")
    db.migrate(url)
    db.open_pool(url, max_size=2)
    blobs = BlobStore(ROOT / "data" / f"tricks-blobs-{tag}")
    if fresh:
        with psycopg.connect(url, row_factory=dict_row) as c:
            auth.create_user(c, "tester", "eval-password-123")
            auth.grant(c, "docs", "staff")
            auth.add_user_to_group(c, "tester", "staff")
            runtime.save(c, runtime.validate({"embed_model": a.model, "chunk_size": a.chunk_size,
                                              "chunk_overlap": a.overlap, "context_chars": a.context_chars}), None)
            col = auth.ensure_collection(c, "docs")
            for f in sorted(corpus.glob("*.md")):
                submit(c, blobs, collection_id=col, filename=f.name, data=f.read_bytes(), user_id=None, max_bytes=20_000_000)
            c.commit()
    t = time.perf_counter()
    n = Worker(blobs, 2000, 20_000_000).drain()
    print(f"indexed {n} documents in {time.perf_counter() - t:.0f} s", flush=True)
    emb = runtime.embedder(a.model)
    if not hasattr(emb, "embed_query_batch"):
        emb.embed_query_batch = lambda texts: emb._embed([emb.spec.query_prefix + x for x in texts])
    with db.pool().connection() as conn:
        if a.make_questions:
            make_questions(conn, a.make_questions, ROOT / "data" / f"questions-{corpus.name}.json")
            return
        questions = json.loads(Path(a.questions).read_text()) if a.questions else None
        setup_tables(conn, emb, questions, a.keyword_context)
        if a.neighbour_context and not conn.execute("SELECT to_regclass('neighbour_done') AS t").fetchone()["t"]:
            neighbour_vectors(conn, emb, json.loads(Path(a.neighbour_context).read_text()))
        pages = {r["source_key"].removesuffix(".md") for r in conn.execute("SELECT source_key FROM documents")}
        keys = {str(r["id"]): r["source_key"].removesuffix(".md") for r in conn.execute("SELECT id, source_key FROM documents")}
        queries = []
        for f in a.queries:
            for q in (json.loads(x) for x in Path(f).read_text().splitlines() if x):
                q["answerable"] = q["category"] not in NEGATIVE
                if q["answerable"]:
                    q["relevant"] = [r for r in q["relevant"] if r in pages]
                    if not q["relevant"]:
                        continue
                queries.append(q)
        u = conn.execute("SELECT id FROM users WHERE username = 'tester'").fetchone()
        p = auth.principal_for(conn, u["id"], "tester", False)
        names = a.variants or [v for v in VARIANTS if ("questions" not in v or a.questions)
                               and ("hyde" not in v or a.hyde_url)]
        results = {"corpus": corpus.name, "index": tag, "queries": len(queries), "variants": {}}
        default = {k: getattr(S, k) for k in ("SUMMARY_WEIGHT", "QUESTION_WEIGHT", "RERANK_NEIGHBOURS", "FUSION",
                                              "CONVEX_ALPHA", "HYDE_URL", "RRF_K")}
        for rr in a.rerankers:
            rt = runtime.load(conn)
            rt.rerank_enabled, rt.rerank_model = bool(rr), rr or rt.rerank_model
            gate = S.GateConfig.from_runtime(rt)
            S.search(conn, p, "warm up", rt)
            for v in names:
                if "neighbours" in v and not rr:
                    continue
                for k, x in {**default, **VARIANTS[v]}.items():
                    setattr(S, k, a.hyde_url if x == "from --hyde-url" else x)
                label = f"{v} | {rr.split('/')[-1] if rr else 'no reranker'}"
                out, _ = run(conn, p, queries, rt, keys, gate)
                results["variants"][label] = out
                print(f"{label:48s} SO {out['so']}  SUB {out['sub']}  {out['seconds_per_query']} s/q", flush=True)
        for k, x in default.items():
            setattr(S, k, x)
    name = a.name or tag
    (ROOT / "eval" / "results" / f"tricks-{name}.json").write_text(json.dumps(results, indent=1))
    db.close_pool()


if __name__ == "__main__":
    main()
