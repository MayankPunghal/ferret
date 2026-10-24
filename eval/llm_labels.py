"""Document types written by a LOCAL language model, against the Kubernetes site's own filing of each page.

    uv run python eval/llm_labels.py --url http://127.0.0.1:8090/v1 --name gemma-4-e2b [--per-section 12] [--every 3]

Gold labels: eval/k8s_doc_types.jsonl, from where kubernetes/website files each page (content/en/docs/<section>/,
when its content_type front matter agrees): tasks and tutorials -> how-to, concepts -> concept, reference ->
reference. The prompt and reply parsing are the app's own (enrich.PROMPT, enrich.parse_reply), the page text is
the corpus file (front matter dropped), headings as the outline. Scored:
  exact        the model's type is the gold type
  compatible   also counts close types: how-to ~ runbook / troubleshooting / onboarding, concept ~ design
A stratified sample (the same pages for every model: sorted, every k-th page of each section).
"""

import argparse
import collections
import json
import re
import time
from pathlib import Path

from knowledge_search import enrich

ROOT = Path(__file__).resolve().parent.parent
CLOSE = {"how-to": {"how-to", "runbook", "troubleshooting", "onboarding"}, "concept": {"concept", "design"},
         "reference": {"reference"}}


def page(path: Path) -> tuple[str, str, str]:
    text = path.read_text(errors="replace")
    title = ""
    m = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if m:
        t = re.search(r'^title:\s*"?(.*?)"?\s*$', m.group(1), re.M)
        title = t.group(1) if t else ""
        text = text[m.end():]
    heads = [h.strip() for h in re.findall(r"^#{1,4}\s+(.+)$", text, re.M)][:40]
    outline = ("Sections: " + "; ".join(heads) + "\n") if heads else ""
    return title or path.stem, outline, text[:enrich.SOURCE_CHARS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpora" / "kubernetes-nodesc"))
    ap.add_argument("--url", default="http://127.0.0.1:8090/v1")
    ap.add_argument("--name", default="gemma-4-e2b")
    ap.add_argument("--per-section", type=int, default=12)
    ap.add_argument("--every", type=int, default=1, help="every k-th page of the standard sample (same pages as other runs)")
    a = ap.parse_args()
    gold = [json.loads(x) for x in (ROOT / "eval" / "k8s_doc_types.jsonl").read_text().splitlines() if x]
    sample = []
    for sec in ("tasks", "concepts", "reference", "tutorials"):
        rows = [g for g in gold if g["section"] == sec]
        n = min(a.per_section if sec != "tutorials" else a.per_section // 2, len(rows))
        sample += rows[:: max(1, len(rows) // n)][:n]
    sample = sample[:: a.every]
    types = "; ".join(f"{k} ({v})" for k, v in enrich.DOC_TYPES.items())
    out, confusion = [], collections.Counter()
    for g in sample:
        title, outline, text = page(Path(a.corpus) / g["file"])
        t = time.time()
        try:  # the app retries a reply it cannot read; here it counts as wrong
            reply = enrich.parse_reply(enrich.chat(a.url, "", enrich.PROMPT.format(
                title=title, title_note="", outline=outline, text=text, types=types, known="")))
        except enrich.LLMError:
            reply = {"type": "(unreadable reply)", "topics": []}
        kind = reply["type"]
        confusion[(g["type"], kind)] += 1
        out.append({**g, "model_type": kind, "topics": reply["topics"], "seconds": round(time.time() - t, 1)})
        print(f"  {g['type']:10} {kind:16} {g['file']}", flush=True)
    exact = sum(r["model_type"] == r["type"] for r in out) / len(out)
    close = sum(r["model_type"] in CLOSE[r["type"]] for r in out) / len(out)
    per = {s: round(sum(r["model_type"] in CLOSE[r["type"]] for r in out if r["section"] == s)
                    / max(1, sum(r["section"] == s for r in out)), 3) for s in ("tasks", "concepts", "reference", "tutorials")}
    res = {"llm": a.name, "pages": len(out), "exact": round(exact, 3), "compatible": round(close, 3),
           "compatible_by_section": per, "confusion": {f"{k[0]} -> {k[1]}": v for k, v in confusion.most_common()},
           "rows": out}
    (ROOT / "eval" / "results" / f"llm-labels-kubernetes-{a.name}{f'-every{a.every}' if a.every > 1 else ''}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
