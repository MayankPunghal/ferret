"""Relations between documents written by a LOCAL language model: do they bring up the pages a relationship
question needs, with fewer documents on screen than the related rows shown today?

    uv run --group eval python eval/llm_relations.py --url http://127.0.0.1:8090/v1 --name gemma-4-e2b

For each of the top 3 results of each question in eval/k8s_relational.jsonl (as eval/relational_eval.py), the
model reads the document (opening text and headings) and up to 15 candidate documents (its links, mentions and
nearest in meaning: title and opening line) and replies with the candidates it has a real relationship with:
type and a phrase from the document as evidence. A relation whose evidence is not in the document is dropped.
The model also names topics a reader needs that the document does not cover; each is looked up with search.
Scored like relational_eval.py (all needs = every page group of the question covered):
  llm relations       results + the related candidates the model chose (top 3 results)
  llm + needs         also the top document for each named need
  current related     the related rows the UI shows today (from relational_eval.py, for comparison)
Model output is cached in eval/results/relations-<corpus>-<name>.json.
"""

import argparse
import json
import re
import statistics
import time
import urllib.request
from pathlib import Path
from uuid import UUID

import numpy as np

from knowledge_search import auth, db, graph, runtime
from knowledge_search.search import GateConfig, search

ROOT = Path(__file__).resolve().parent.parent
TOP = 3
CANDIDATES = 15
SOURCE_CHARS = 3000
TYPES = ["depends on", "configures", "part of", "uses", "explains", "replaces", "related"]
PROMPT = """You map relationships between internal technical documents. Read the DOCUMENT, then the CANDIDATE
documents. Reply with JSON:
{{"relations": [{{"to": <candidate number>, "type": "<one of: {types}>",
                 "evidence": "<at most 12 words copied exactly from the DOCUMENT that show the relationship>"}}],
 "needs": ["<up to 3 topics a reader of this DOCUMENT must also understand that it does not explain itself>"]}}
Only list candidates the DOCUMENT itself is directly connected to (it relies on them, configures them, is part of
them, or explains them), at most 5, strongest first. Leave out candidates that are merely about something
similar. An empty list is fine.

DOCUMENT: {title}
{outline}
{text}

CANDIDATES:
{candidates}"""


def chat(url, prompt, max_tokens=900):
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0,
            "response_format": {"type": "json_object"}, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=900))["choices"][0]["message"]["content"]


def norm(s):
    return " ".join(re.sub(r"[^\w]+", " ", s.lower()).split())


def score(q, docs):
    hit = [any(d in g for d in docs) for g in q["needs"]]
    return sum(hit) / len(hit), all(hit)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpora" / "kubernetes-nodesc"))
    ap.add_argument("--queries", default=str(ROOT / "eval" / "k8s_relational.jsonl"))
    ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    ap.add_argument("--url", default="http://127.0.0.1:8090/v1")
    ap.add_argument("--name", default="gemma-4-e2b")
    a = ap.parse_args()
    name = Path(a.corpus).name
    tag = ("" if name == "kubernetes" else f"{name}-") + a.model.replace("/", "_")
    db.start_embedded(ROOT / "data" / f"corpus-pg-{tag}", database="postgres")
    db.migrate(db._embedded.get_uri("ks_corpus"))  # indexes built before later schema changes
    db.open_pool(db._embedded.get_uri("ks_corpus"), max_size=2)
    cache_path = ROOT / "eval" / "results" / f"relations-{name}-{a.name}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    queries = [json.loads(x) for x in Path(a.queries).read_text().splitlines() if x]
    loose = GateConfig(9, 9, 9, 99, "similarity")
    rows = []
    with db.pool().connection() as conn:
        u = conn.execute("SELECT id FROM users WHERE username = 'tester'").fetchone()
        p = auth.principal_for(conn, u["id"], "tester", False)
        rt = runtime.load(conn)
        keys = {str(r["id"]): r["source_key"].removesuffix(".md") for r in conn.execute("SELECT id, source_key FROM documents")}
        titles = {str(r["id"]): r["title"] for r in conn.execute("SELECT id, title FROM documents")}
        dvec = {str(r["id"]): np.array(r["v"], dtype=np.float32) for r in conn.execute(
            "SELECT id, embedding::real[] AS v FROM documents WHERE embedding IS NOT NULL")}
        dvec = {k: v / np.linalg.norm(v) for k, v in dvec.items()}
        order = list(dvec)
        mat = np.stack([dvec[k] for k in order])

        def text_of(d):
            rows_ = conn.execute("SELECT c.heading_path, c.text FROM chunks c JOIN documents x ON x.indexed_version_id = "
                                 "c.version_id WHERE x.id = %s ORDER BY c.ordinal", (d,)).fetchall()
            return rows_

        def relations(d):
            if d in cache:
                return cache[d]
            chunks = text_of(d)
            full = "\n".join(c["text"] for c in chunks)
            outline = "Sections: " + "; ".join(dict.fromkeys(c["heading_path"] for c in chunks if c["heading_path"]))[:600]
            links = graph.document_links(UUID(d), p, conn)
            pool = [x["document_id"] for k in ("outgoing", "incoming", "mentions", "similar") for x in links[k]]
            near = [order[i] for i in np.argsort(-(mat @ dvec[d]))[:CANDIDATES + 1]] if d in dvec else []
            cands = [x for x in dict.fromkeys(pool + near) if x != d and x in keys][:CANDIDATES]
            lines = []
            for i, c in enumerate(cands, 1):
                first = text_of(c)
                opening = " ".join((first[0]["text"] if first else "").split())[:160]
                lines.append(f"[{i}] {titles[c]}: {opening}")
            prompt = PROMPT.format(types=", ".join(TYPES), title=titles[d], outline=outline,
                                   text=full[:SOURCE_CHARS], candidates="\n".join(lines))
            t = time.time()
            raw = chat(a.url, prompt)
            try:
                out = json.loads(raw)
            except json.JSONDecodeError:  # cut off: keep the complete relation objects
                rel = [json.loads(m) for m in re.findall(r'\{\s*"to"[^{}]*\}', raw)]
                out = {"relations": rel, "needs": [], "unparsed": True}
            kept, dropped = [], []
            body = norm(full)
            for r in out.get("relations") or []:
                try:
                    c = cands[int(r.get("to")) - 1]
                except (TypeError, ValueError, IndexError):
                    continue
                ev = norm(str(r.get("evidence", "")))
                (kept if ev and ev in body else dropped).append(
                    {"to": c, "type": r.get("type"), "evidence": r.get("evidence")})
            cache[d] = {"relations": kept, "dropped": dropped, "needs": [str(x) for x in (out.get("needs") or [])][:3],
                        "candidates": cands, "seconds": round(time.time() - t, 1), "raw": raw[:1500]}
            cache_path.write_text(json.dumps(cache, indent=1))
            print(f"  {keys[d]}: {len(kept)} kept, {len(dropped)} dropped, needs {cache[d]['needs']} "
                  f"({cache[d]['seconds']} s)", flush=True)
            return cache[d]

        for q in queries:
            resp = search(conn, p, q["query"], rt, limit=10, gate=loose)
            ids = list(dict.fromkeys(r.document_id for r in resp.results))
            res = [keys[d] for d in ids]
            rel, need = [], []
            for d in ids[:TOP]:
                out = relations(d)
                rel += [r["to"] for r in out["relations"]]
                for n in out["needs"]:
                    hit = search(conn, p, n, rt, limit=3, gate=loose).results
                    if hit:
                        need.append(hit[0].document_id)
            conn.commit()
            with_rel = list(dict.fromkeys(res + [keys[x] for x in rel]))
            with_need = list(dict.fromkeys(with_rel + [keys[x] for x in need]))
            rows.append({"id": q["id"], "rel": (*score(q, with_rel), len(with_rel)),
                         "need": (*score(q, with_need), len(with_need)),
                         "missing": [g for g in q["needs"] if not any(x in g for x in with_need)]})
    db.close_pool()

    def agg(k):
        v = [r[k] for r in rows]
        return {"need_recall": round(statistics.mean(x[0] for x in v), 3),
                "all_needs": round(statistics.mean(float(x[1]) for x in v), 3),
                "shown_median": statistics.median(x[2] for x in v)}

    kept = [len(v["relations"]) for v in cache.values()]
    dropped = [len(v["dropped"]) for v in cache.values()]
    out = {"corpus": name, "llm": a.name, "documents_read": len(cache), "relations_kept": sum(kept),
           "relations_dropped_no_evidence": sum(dropped),
           "median_seconds": statistics.median(v["seconds"] for v in cache.values()),
           "llm_relations": agg("rel"), "llm_relations_and_needs": agg("need"), "rows": rows}
    (ROOT / "eval" / "results" / f"llm-relations-{name}-{a.name}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}))
    for r in rows:
        if r["missing"]:
            print(f"  {r['id']} missing {r['missing']}")


if __name__ == "__main__":
    main()
