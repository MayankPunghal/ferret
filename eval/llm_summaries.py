"""Write a summary (and the topics it relates to) for every page of a corpus with a LOCAL chat model, then
build a copy of the corpus that carries the summary as front-matter `description` (what the document-summary
setting puts into passage embeddings).

    llama-server -m gemma-4-E2B-it-Q4_K_M.gguf -c 8192 -np 2 --jinja --port 8081     # or Ollama / LM Studio
    uv run --group eval python eval/llm_summaries.py --corpus data/corpora/kubernetes-nodesc --name gemma-4-E2B
    uv run --group eval python eval/corpus_eval.py --corpus data/corpora/kubernetes-nodesc-gemma-4-e2b --context-chars 300

Any OpenAI-compatible endpoint works (--url). Results are cached in eval/results/summaries-<corpus>-<name>.json
(resumable) with per-page seconds, so speed and output can be inspected. No data leaves the machine unless
--url points elsewhere.
"""

import argparse
import json
import re
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = """You index internal technical documentation for search. Read the page below and reply with JSON:
{{"title": "<the page title; if it is vague or generic (Overview, Introduction, Home, Page 1), a short specific one>",
 "summary": "<2 sentences, at most 50 words: what the page is about and what it explains>",
 "topics": ["<up to 6 systems, services, components or concepts the page relies on or relates to>"]}}

Page title: {title}

{text}"""


def chat(url: str, prompt: str, max_tokens: int) -> str:
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0,
            "response_format": {"type": "json_object"}, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=900))["choices"][0]["message"]["content"]


def split(md: str) -> tuple[str, str, str]:
    """(front matter, title, body) of a converted page."""
    m = re.match(r"---\n(.*?)\n---\n", md, re.DOTALL)
    fm, body = (m.group(1), md[m.end():]) if m else ("", md)
    t = re.search(r'(?m)^title:\s*"?(.*?)"?\s*$', fm)
    return fm, (json.loads(f'"{t.group(1)}"') if t else ""), body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpora" / "kubernetes-nodesc"))
    ap.add_argument("--name", required=True, help="label for the model, e.g. gemma-4-E2B")
    ap.add_argument("--url", default="http://localhost:8081/v1")
    ap.add_argument("--chars", type=int, default=3000, help="page characters given to the model")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0, help="only the first N pages (speed test)")
    a = ap.parse_args()
    corpus = Path(a.corpus)
    cache = ROOT / "eval" / "results" / f"summaries-{corpus.name}-{a.name}.json"
    done = json.loads(cache.read_text()) if cache.exists() else {}
    files = sorted(corpus.glob("*.md"))[: a.limit or None]
    todo = [f for f in files if f.name not in done]

    def one(f: Path):
        _, title, body = split(f.read_text(encoding="utf-8"))
        t = time.perf_counter()
        raw = chat(a.url, PROMPT.format(title=title, text=body.strip()[: a.chars]), 200)
        try:
            out = json.loads(raw)
            out = {"title": " ".join(str(out.get("title", "")).split())[:200],
                   "summary": " ".join(str(out.get("summary", "")).split()),
                   "topics": [str(x) for x in out.get("topics", [])][:6]}
        except (json.JSONDecodeError, AttributeError):
            out = {"summary": " ".join(raw.split())[:400], "topics": [], "unparsed": True}
        return f.name, {**out, "seconds": round(time.perf_counter() - t, 1)}

    t0 = time.perf_counter()
    with ThreadPoolExecutor(a.parallel) as pool:
        for i, (name, res) in enumerate(pool.map(one, todo), 1):
            done[name] = res
            if i % 10 == 0 or i == len(todo):
                cache.write_text(json.dumps(done, indent=1))
                print(f"{len(done)}/{len(files)} pages, {(time.perf_counter() - t0) / i:.1f} s/page wall", flush=True)
    cache.write_text(json.dumps(done, indent=1))
    secs = [v["seconds"] for v in done.values()]
    print(f"{len(done)} summaries, median {statistics.median(secs):.1f} s per request, "
          f"{sum(v.get('unparsed', False) for v in done.values())} unparsed -> {cache}")
    if a.limit:
        return
    out_dir = corpus.with_name(f"{corpus.name}-{a.name}".lower())  # collection names are lowercase
    out_dir.mkdir(exist_ok=True)
    for f in files:
        fm, _, body = split(f.read_text(encoding="utf-8"))
        fm = "\n".join(x for x in fm.splitlines() if not x.startswith("description:"))
        (out_dir / f.name).write_text(f"---\n{fm}\ndescription: {json.dumps(done[f.name]['summary'])}\n---\n{body}",
                                      encoding="utf-8")
    print("corpus with summaries ->", out_dir)


if __name__ == "__main__":
    main()
