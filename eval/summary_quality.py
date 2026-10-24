"""How good are model-written document summaries? Scored against the descriptions the Kubernetes authors wrote
for the same pages (eval/make_k8s_corpus.py keeps them in data/corpora/kubernetes and drops them in -nodesc).

    uv run --group eval python eval/summary_quality.py qwen3.5-0.8b-app gemma-4-e2b-app ...

For each page with an author description that every named model summarised (same pages for all):
  agreement   meaning similarity of summary and author description (bge-base cosine), mean
  identifies  given only the summary, the page's own author description is the closest of all reference
              descriptions (1.0 = every summary points at its page and no other)
  type        document type against eval/k8s_doc_types.jsonl where the page has a gold type (exact, and
              compatible as in eval/llm_labels.py), when the run recorded types
Also scored: the fallback without a model (first paragraph of the page without its description: what the
"document summary in each passage" setting uses when no summary is written). Writes
eval/results/summary-quality.json.
"""

import json
import re
import statistics
import sys
from pathlib import Path

import numpy as np

from knowledge_search.embedder import Embedder

ROOT = Path(__file__).resolve().parent.parent
REF, NODESC = ROOT / "data" / "corpora" / "kubernetes", ROOT / "data" / "corpora" / "kubernetes-nodesc"


def body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return re.sub(r"(?s)^---\n.*?\n---\n\s*#[^\n]*\n", "", text).strip()


def first_paragraph(text: str) -> str:
    return next((p for p in text.split("\n\n") if p.strip() and not p.lstrip().startswith(("```", "|", "#"))), "")


def main():
    names = sys.argv[1:]
    refs = {}
    for f in sorted(REF.glob("*.md")):
        if f.read_bytes() != (NODESC / f.name).read_bytes():
            refs[f.name] = " ".join(first_paragraph(body(f)).split())
    runs = {n: json.loads((ROOT / "eval" / "results" / f"summaries-kubernetes-nodesc-{n}.json").read_text())
            for n in names}
    pages = [p for p in refs if all(p in r and not r[p].get("unparsed") for r in runs.values())]
    runs["no model (first paragraph)"] = {p: {"summary": " ".join(first_paragraph(body(NODESC / p)).split())}
                                          for p in pages}
    gold = {g["file"]: g["type"] for g in map(json.loads, (ROOT / "eval" / "k8s_doc_types.jsonl").read_text().splitlines())}
    from llm_labels import CLOSE
    emb = Embedder("BAAI/bge-base-en-v1.5", str(ROOT / "data" / "models"), offline=True)
    ref_names = list(refs)
    R = emb.embed_documents([refs[p] for p in ref_names])
    R /= np.linalg.norm(R, axis=1, keepdims=True)
    idx = {p: i for i, p in enumerate(ref_names)}
    out = {"pages": len(pages), "reference_descriptions": len(refs), "models": {}}
    for name, run in runs.items():
        S = emb.embed_documents([run[p]["summary"] or "-" for p in pages])
        S /= np.linalg.norm(S, axis=1, keepdims=True)
        sims = S @ R.T
        own = np.array([sims[i, idx[p]] for i, p in enumerate(pages)])
        top1 = np.array([int(np.argmax(sims[i])) == idx[p] for i, p in enumerate(pages)])
        secs = [run[p]["seconds"] for p in pages if "seconds" in run[p]]
        out["models"][name] = {"agreement": round(float(own.mean()), 3), "identifies": round(float(top1.mean()), 3),
                               "median_seconds": statistics.median(secs) if secs else None,
                               "words": round(statistics.mean(len(run[p]["summary"].split()) for p in pages), 1)}
        typed = [p for p in pages if p in gold and "type" in run[p]]
        if typed:
            out["models"][name]["type"] = {
                "pages": len(typed), "exact": round(sum(run[p]["type"] == gold[p] for p in typed) / len(typed), 3),
                "compatible": round(sum(run[p]["type"] in CLOSE[gold[p]] for p in typed) / len(typed), 3)}
    (ROOT / "eval" / "results" / "summary-quality.json").write_text(json.dumps(out, indent=1))
    print(f"{len(pages)} pages scored against {len(refs)} author descriptions")
    for n, m in out["models"].items():
        print(f"  {n:32s} agreement {m['agreement']:.3f}  identifies {m['identifies']:.3f}  "
              f"words {m['words']}  median s {m['median_seconds']}"
              + (f"  type {m['type']['exact']:.3f} / {m['type']['compatible']:.3f} (n={m['type']['pages']})" if "type" in m else ""))


if __name__ == "__main__":
    main()
