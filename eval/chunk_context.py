"""Passage context written by a local model ("chunk enrichment", Anthropic's contextual retrieval): for every
passage the model writes 1-2 sentences that situate it in its document, added to the passage before
embedding. Does it beat what ships (title + heading path, optionally the document summary)?

    uv run --group eval python eval/chunk_context.py subset                 # data/corpora/k8s-sub
    llama-server -m <model.gguf> -c 8192 -np 2 --jinja --port 8081
    uv run --group eval python eval/chunk_context.py generate --name qwen3.5-0.8b
    uv run --group eval python eval/chunk_context.py index --name qwen3.5-0.8b [--reranker R]
    # baselines on the same pages:
    uv run --group eval python eval/corpus_eval.py --corpus data/corpora/k8s-sub [--context-chars 300]
    # relationship questions on any of these indexes: eval/relational_eval.py --corpus <same corpus dir>

Subset (to keep the per-passage model cost to hours): every page a Kubernetes question needs, plus 150
other pages (fixed seed), from data/corpora/kubernetes-nodesc (no author descriptions: like intranet pages).
Contexts are cached in eval/results/chunk-context-<name>.json, keyed by document and passage text.
"""

import argparse
import hashlib
import json
import random
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "corpora" / "kubernetes-nodesc"
SUB = ROOT / "data" / "corpora" / "k8s-sub"
OTHERS = 150
DOC_CHARS = 6000
PROMPT = """<document>
{doc}
</document>
Here is a passage from that document:
<passage>
{chunk}
</passage>
Write one or two sentences that situate this passage within the document, to improve search retrieval of
the passage: name the system, component or task it is about if the passage does not say so itself. Answer
only with those sentences."""


def key(title: str, text: str) -> str:
    return hashlib.sha1(f"{title}\n{text}".encode()).hexdigest()


def subset():
    need = set()
    for line in (ROOT / "eval" / "k8s_queries.jsonl").read_text().splitlines():
        if line:
            need |= set(json.loads(line)["relevant"])
    for line in (ROOT / "eval" / "k8s_relational.jsonl").read_text().splitlines():
        if line:
            need |= {d for g in json.loads(line)["needs"] for d in g}
    pages = sorted(p.stem for p in SRC.glob("*.md"))
    need &= set(pages)
    others = random.Random(0).sample(sorted(set(pages) - need), OTHERS)
    shutil.rmtree(SUB, ignore_errors=True)
    SUB.mkdir(parents=True)
    for p in sorted(need | set(others)):
        shutil.copy(SRC / f"{p}.md", SUB / f"{p}.md")
    print(f"{len(need)} needed + {len(others)} other pages -> {SUB}")


def chat(url, prompt):
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 120, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": True}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=900))["choices"][0]["message"]["content"].strip()


def generate(a):
    from knowledge_search.parsing import chunk_document, parse_document

    cache = ROOT / "eval" / "results" / f"chunk-context-{a.name}.json"
    done = json.loads(cache.read_text()) if cache.exists() else {}
    docs = []
    for f in sorted(SUB.glob("*.md")):
        parsed = parse_document(f.read_bytes(), f.name)
        text = "\n\n".join(s.text for s in parsed.sections)[:DOC_CHARS]
        todo = [c for c in chunk_document(parsed, 900, 150) if key(parsed.title, c.text) not in done]
        if todo:
            docs.append((parsed.title, text, todo))
    total = sum(len(d[2]) for d in docs)
    print(f"{total} passages to situate in {len(docs)} documents", flush=True)
    t0, n = time.perf_counter(), 0

    def one(d):  # one document's passages in order: the server reuses the cached document prefix
        title, text, chunks = d
        out = {}
        for c in chunks:
            out[key(title, c.text)] = " ".join(chat(a.url, PROMPT.format(doc=text, chunk=c.text)).split())
        return out

    with ThreadPoolExecutor(a.parallel) as pool:
        for res in pool.map(one, docs):
            done.update(res)
            n += len(res)
            cache.write_text(json.dumps(done, indent=0))
            print(f"{n}/{total} passages, {(time.perf_counter() - t0) / n:.2f} s/passage wall", flush=True)


def index(a):
    from knowledge_search import ingest, parsing

    contexts = json.loads((ROOT / "eval" / "results" / f"chunk-context-{a.name}.json").read_text())
    corpus = SUB.with_name(f"k8s-sub-cc-{a.name}".lower().replace(".", "-"))
    if not corpus.exists():
        shutil.copytree(SUB, corpus)
    original = parsing.embedding_text
    missing = []

    def with_context(doc_title, chunk, summary=""):
        ctx = contexts.get(key(doc_title, chunk.text))
        if ctx is None:
            missing.append(doc_title)
        base = original(doc_title, chunk, summary)
        head, _, text = base.partition("\n")
        return f"{head}\n{ctx}\n{text}" if ctx else base

    ingest.embedding_text = with_context
    import corpus_eval

    sys.argv = ["corpus_eval.py", "--corpus", str(corpus)] + (["--reranker", a.reranker] if a.reranker else []) \
        + (["--reuse"] if a.reranker else [])
    corpus_eval.main()
    if missing:
        print(f"WARNING: {len(missing)} passages had no context (e.g. {missing[0]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["subset", "generate", "index"])
    ap.add_argument("--name", default="qwen3.5-0.8b")
    ap.add_argument("--url", default="http://localhost:8081/v1")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--reranker", default="")
    a = ap.parse_args()
    {"subset": lambda: subset(), "generate": lambda: generate(a), "index": lambda: index(a)}[a.step]()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
