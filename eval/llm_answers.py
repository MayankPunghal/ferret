"""Can a LOCAL chat model answer from the retrieved passages, refuse when they do not contain the answer,
and pick the passage that does (a check on the ranking)?

    llama-server -m <model.gguf> -c 8192 -np 2 --jinja --port 8081      # or Ollama / LM Studio (--url)
    uv run --group eval python eval/llm_answers.py --name gemma-4-E2B \
        eval/results/corpus-kubernetes-BAAI_bge-base-en-v1.5.rows.json eval/results/fixtures-BAAI_bge-base-en-v1.5.rows.json

Reads the top 5 passages per labelled query from *.rows.json (written by corpus_eval.py / run_eval.py with
the same search the product runs), asks the model for {"answer": ..., "sources": [...]} or {"answer": null},
and reports per corpus:
  model alone      false answers on must-refuse queries, answered share, first cited passage relevant
  gate + model     answer only when the shipped evidence gate passes AND the model answers
  gate alone       the shipped gate, for comparison (same queries)
  cited@1          first cited passage is from a labelled page, vs. the search's own rank-1 passage
Per-query outputs are cached (resumable) in eval/results/answers-<name>.json.
"""

import argparse
import json
import re
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gates import evaluate

from knowledge_search.search import GateConfig, gate_basis

ROOT = Path(__file__).resolve().parent.parent
K = 5
PROMPT = """Answer the question using ONLY the numbered passages from the company documentation below.
Do not use outside knowledge. A passage about a different system or topic does not answer the question.
Reply with JSON only:
{{"found": true, "answer": "<short answer>", "sources": [<numbers of the passages that state the answer>]}}
or, if the passages do not contain the answer:
{{"found": false}}

Question: {query}

{passages}"""


def chat(url: str, prompt: str, max_tokens: int, think: bool) -> tuple[str, float]:
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": think}}
    if not think:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t = time.perf_counter()
    r = json.load(urllib.request.urlopen(req, timeout=3600))
    return r["choices"][0]["message"]["content"], time.perf_counter() - t


def parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)  # thinking models may wrap the JSON in text
    try:
        out = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {"answer": None, "sources": [], "unparsed": raw[-300:]}
    src = [int(x) for x in out.get("sources", []) or [] if str(x).isdigit()]
    ans = out.get("answer")
    if out.get("found") is not True or not src or not ans:  # an answer must say found and cite a passage
        return {"answer": None, "sources": [], **({"said": str(ans)[:200]} if ans else {})}
    return {"answer": str(ans), "sources": src}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rows", nargs="+")
    ap.add_argument("--name", required=True)
    ap.add_argument("--url", default="http://localhost:8081/v1")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--think", action="store_true", help="let reasoning models think (slower)")
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--subset", default="", help="only these query categories, comma-separated")
    ap.add_argument("--shipped", default="0.78,0.72,0.2,0.6", help="gate: dense_high,dense_min,lex_coverage,all_terms_min")
    a = ap.parse_args()
    cache = ROOT / "eval" / "results" / f"answers-{a.name}.json"
    done = json.loads(cache.read_text()) if cache.exists() else {}
    h, m, c, f = map(float, a.shipped.split(","))
    gate = GateConfig(h, m, c, 0, "similarity", "coverage", f)
    data = {Path(p).name.removesuffix(".rows.json"): json.loads(Path(p).read_text()) for p in a.rows}
    cats = set(a.subset.split(",")) if a.subset else None
    jobs = [(src, q, res) for src, rows in data.items() for q, res in rows
            if (not cats or q["category"] in cats) and f"{src}/{q['id']}" not in done]

    def one(job):
        src, q, res = job
        passages = "\n\n".join(f"[{i}] {r['title']} — {r['heading_path']}\n{r['text']}" for i, r in enumerate(res[:K], 1))
        raw, secs = chat(a.url, PROMPT.format(query=q["query"], passages=passages), a.max_tokens, a.think)
        return f"{src}/{q['id']}", {**parse(raw), "seconds": round(secs, 1)}

    t0 = time.perf_counter()
    with ThreadPoolExecutor(a.parallel) as pool:
        for i, (key, out) in enumerate(pool.map(one, jobs), 1):
            done[key] = out
            if i % 10 == 0 or i == len(jobs):
                cache.write_text(json.dumps(done, indent=1))
                print(f"{i}/{len(jobs)} {(time.perf_counter() - t0) / i:.1f} s/query wall", flush=True)
    cache.write_text(json.dumps(done, indent=1))

    report = {"model": a.name, "median_seconds": statistics.median(v["seconds"] for v in done.values())}
    for src, rows in data.items():
        rows = [(q, res) for q, res in rows if f"{src}/{q['id']}" in done]
        if not rows:
            continue
        neg = [(q, res) for q, res in rows if not q["answerable"]]
        pos = [(q, res) for q, res in rows if q["answerable"]]

        def llm(q):
            return done[f"{src}/{q['id']}"]

        def cited_ok(q, res):
            s = [x for x in llm(q)["sources"] if 1 <= x <= min(K, len(res))]
            return bool(s) and res[s[0] - 1]["relevant"]

        def gated(q, res):
            return any(gate_basis(gate, r["signals"], q["_has_idents"]) for r in res)

        g = evaluate(rows, gate, lambda q, r: r["relevant"])
        report[src] = {
            "queries": len(rows), "must_refuse": len(neg), "answerable": len(pos),
            "model_alone": {"false_answers": sum(llm(q)["answer"] is not None for q, _ in neg),
                            "answered": round(sum(llm(q)["answer"] is not None for q, _ in pos) / max(len(pos), 1), 3),
                            "cited_relevant": round(sum(cited_ok(q, r) for q, r in pos if llm(q)["answer"]) /
                                                    max(sum(1 for q, _ in pos if llm(q)["answer"]), 1), 3)},
            "gate_and_model": {"false_answers": sum(gated(q, r) and llm(q)["answer"] is not None for q, r in neg),
                               "answered": round(sum(gated(q, r) and llm(q)["answer"] is not None for q, r in pos)
                                                 / max(len(pos), 1), 3)},
            "gate_alone": {"false_answers": len(g["false_answers"]), "answered": g["answer_coverage"]},
            "search_rank1_relevant": round(sum(bool(r) and r[0]["relevant"] for _, r in pos) / max(len(pos), 1), 3),
            "model_cited@1_relevant": round(sum(cited_ok(q, r) for q, r in pos) / max(len(pos), 1), 3),
            "false_answer_ids": [q["id"] for q, _ in neg if llm(q)["answer"] is not None],
            "missed_ids": [q["id"] for q, _ in pos if llm(q)["answer"] is None],
        }
    (ROOT / "eval" / "results" / f"answers-{a.name}-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
