"""Blind review of model-written summaries: does a summary say what the page says (faithful) and say enough to
tell the page from its neighbours (specific)?

    uv run python eval/blind_review.py build --key <file outside the repo>   # sheet + hidden key
    (grade the sheet into eval/results/blind-review-grades.json without opening the key)
    uv run python eval/blind_review.py score --key <same file>

Blinding: pages are drawn at random (fixed seed) from the pages every model summarised, before any summary is
read; for each page the models' summaries are shuffled into slots A..E separately; model names, timings and
lengths are not shown; the key (slot -> model) is written to a file outside the repository and opened only by
`score`, after the grades are saved. Not hidden: each model's writing style.

Grades per slot, fixed before reading (the page excerpt is exactly the text the model was given):
  faithful  2 every statement is supported by the excerpt · 1 a minor unsupported detail · 0 a wrong claim
  specific  2 a reader could tell this page from neighbouring pages · 1 right topic but generic · 0 vague/wrong
"""

import argparse
import json
import random
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODESC = ROOT / "data" / "corpora" / "kubernetes-nodesc"
MODELS = ["qwen3.5-0.8b-app", "qwen3.5-2b-app", "gemma-4-e2b-app", "qwen3.5-4b-app", "gemma-4-e4b-app"]
PAGES, MIN_CHARS, EXCERPT = 15, 2500, 3000
SEEN = {"affinity.md", "aggregation-layer.md", "annotation.md", "api-group.md", "api-resource.md",
        "admission-controller.md", "admission-policies.md"}  # summaries read before the review
SHEET = ROOT / "eval" / "results" / "blind-review-sheet.md"
GRADES = ROOT / "eval" / "results" / "blind-review-grades.json"


def body(name: str) -> str:
    return re.sub(r"(?s)^---\n.*?\n---\n", "", (NODESC / name).read_text(encoding="utf-8")).strip()


def build(key: Path):
    runs = {m: json.loads((ROOT / "eval" / "results" / f"summaries-kubernetes-nodesc-{m}.json").read_text())
            for m in MODELS}
    common = sorted(set.intersection(*(set(r) for r in runs.values())))
    pages = [p for p in common if len(body(p)) >= MIN_CHARS and p not in SEEN]
    rng = random.Random(20261023)
    pages = rng.sample(pages, PAGES)
    out, mapping = ["# Blind review sheet (summaries shuffled per page; key kept outside the repository)\n"], {}
    for p in pages:
        order = MODELS[:]
        rng.shuffle(order)
        mapping[p] = dict(zip("ABCDE", order))
        out.append(f"\n## {p}\n\n### Excerpt given to the models\n\n{body(p)[:EXCERPT]}\n\n### Summaries\n")
        for slot, m in mapping[p].items():
            s = runs[m][p]
            text = s["summary"] + ("" if not s.get("topics") else "  Topics: " + "; ".join(s["topics"]))
            out.append(f"- **{slot}**: {text}")
    SHEET.write_text("\n".join(out) + "\n", encoding="utf-8")
    key.write_text(json.dumps(mapping, indent=1))
    print(f"{len(pages)} pages -> {SHEET}; key -> {key}")


def score(key: Path):
    mapping, grades = json.loads(key.read_text()), json.loads(GRADES.read_text())
    per = {m: {"faithful": [], "specific": []} for m in MODELS}
    for p, slots in grades.items():
        for slot, (f, s) in slots.items():
            per[mapping[p][slot]]["faithful"].append(f)
            per[mapping[p][slot]]["specific"].append(s)
    result = {m: {k: round(statistics.mean(v), 2) for k, v in d.items()} | {"wrong_claims": d["faithful"].count(0)}
              for m, d in per.items()}
    (ROOT / "eval" / "results" / "blind-review.json").write_text(json.dumps({"key": mapping, "scores": result}, indent=1))
    for m, r in result.items():
        print(f"  {m:18s} faithful {r['faithful']:.2f}  specific {r['specific']:.2f}  wrong claims {r['wrong_claims']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "score"])
    ap.add_argument("--key", required=True, type=Path)
    a = ap.parse_args()
    (build if a.step == "build" else score)(a.key)
