"""Choose the evidence gate on several labelled corpora at once (more queries, less noise than either alone).

    uv run --group eval python eval/pooled_gates.py eval/results/fixtures-BAAI_bge-base-en-v1.5.rows.json \
        eval/results/corpus-kubernetes-BAAI_bge-base-en-v1.5.rows.json

Each *.rows.json is written by run_eval.py / corpus_eval.py (per-query signals and relevance). Gates are
swept on the pooled val halves and reported on the pooled test halves and per corpus, next to the shipped
gate of the same model (passed with --shipped as dense_high,dense_min,lex_coverage,all_terms_min).
"""

import argparse
import json
from pathlib import Path

from gates import choose, evaluate

from knowledge_search.search import GateConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rows", nargs="+")
    ap.add_argument("--max-far", type=float, default=0.0)
    ap.add_argument("--shipped", default="", help="dense_high,dense_min,lex_coverage,all_terms_min")
    a = ap.parse_args()
    data = {Path(f).name.removesuffix(".rows.json"): [(q, res) for q, res in json.loads(Path(f).read_text())]
            for f in a.rows}

    def is_relevant(q, r):
        return r["relevant"]

    rows = [x for v in data.values() for x in v]
    val, test = [x for x in rows if x[0]["split"] == "val"], [x for x in rows if x[0]["split"] == "test"]
    print(f"pooled: val {len(val)} / test {len(test)} queries from {len(data)} corpora")

    def report(label, g):
        print(f"  {label}")
        for name, part in [("pooled test", test), *[(k, [x for x in v if x[0]["split"] == "test"]) for k, v in data.items()],
                           ("pooled all", rows)]:
            m = evaluate(part, g, is_relevant)
            print(f"    {name:55s} false {m['false_answer_rate']:.3f} (n={m['unanswerable_n']}) answered "
                  f"{m['answer_coverage']:.3f} (n={m['answerable_n']}) precision {m['precision_among_answered']:.3f}")

    if a.shipped:
        h, m, c, f = map(float, a.shipped.split(","))
        report(f"shipped {a.shipped}", GateConfig(h, m, c, 0, "similarity", "coverage", f))
    for fam, g in choose(val, test, is_relevant, a.max_far).items():
        if g.get("chosen"):
            report(f"{fam} chosen on pooled val: {g['chosen']}", GateConfig(**g["chosen"]))
        else:
            print(f"  {fam}: {g['note']}")


if __name__ == "__main__":
    main()
