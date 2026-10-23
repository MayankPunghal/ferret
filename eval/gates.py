"""Offline evidence-gate experiments shared by run_eval.py (fixtures) and scale_eval.py (BEIR).

Each query is searched once with every signal recorded (cosine, keyword coverage, IDF coverage, identifier
matches, reranker score). Gate families are then swept on the *val* split; the configuration with the highest
answer coverage whose val false-answer rate is <= the budget is applied unchanged to the held-out *test* split.
"""

import json
from itertools import product

from knowledge_search.search import GateConfig, gate_basis

SIM_HIGH = [0.78, 0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.92, 0.94]
SIM_MIN = [0.55, 0.60, 0.64, 0.68, 0.72, 0.76, 0.80, 0.84]
LEX = [0.2, 0.34, 0.5, 0.67]
ALL_TERMS = [9.0, 0.6, 0.55, 0.5, 0.45, 0.4, 0.35, 0.3, 0.2, 0.0]  # 9 = rule off; ties keep the higher floor


def _quantiles(xs: list[float], n: int = 25) -> list[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return []
    return sorted({round(xs[min(len(xs) - 1, int(i * (len(xs) - 1) / (n - 1)))], 3) for i in range(n)})


def families(rows) -> dict[str, list[GateConfig]]:
    rr = _quantiles([r["signals"]["rerank_score"] for _, res in rows for r in res[:3]], 30)
    sims = _quantiles([r["signals"]["cosine_similarity"] for _, res in rows for r in res[:3]], 12)
    out = {
        "similarity+coverage": [GateConfig(h, m, c, 0, "similarity", "coverage", f)
                                for h, m, c, f in product(SIM_HIGH, SIM_MIN, LEX, ALL_TERMS) if m < h and (f < m or f == 9.0)],
        "similarity+idf": [GateConfig(h, m, c, 0, "similarity", "idf", f)
                           for h, m, c, f in product(SIM_HIGH, SIM_MIN, LEX, ALL_TERMS) if m < h and (f < m or f == 9.0)],
    }
    if rr:
        out["rerank"] = [GateConfig(9, 9, 9, t, "rerank") for t in rr]
        out["rerank+similarity"] = [GateConfig(9, m, 9, t, "rerank_and_similarity")
                                    for t, m in product(rr, sorted(set(sims) | {0.0}))]
    return out


def evaluate(rows, gate: GateConfig, is_relevant) -> dict:
    """rows: [(query_dict, results)], query_dict has 'answerable' and '_has_idents'."""
    neg_ans = pos_ans = pos_ok = n_neg = n_pos = 0
    false_answers, missed = [], []
    for q, results in rows:
        sup = [r for r in results if gate_basis(gate, r["signals"], q["_has_idents"])]
        if not q["answerable"]:
            n_neg += 1
            if sup:
                neg_ans += 1
                false_answers.append(q["id"])
        else:
            n_pos += 1
            if sup:
                pos_ans += 1
                pos_ok += is_relevant(q, sup[0])
            else:
                missed.append(q["id"])
    return {
        "unanswerable_n": n_neg, "answerable_n": n_pos,
        "false_answer_rate": round(neg_ans / max(n_neg, 1), 3),
        "answer_coverage": round(pos_ans / max(n_pos, 1), 3),
        "precision_among_answered": round(pos_ok / max(pos_ans + neg_ans, 1), 3),
        "false_answers": false_answers, "abstained_answerable": missed,
    }


def dump_rows(path, rows, is_relevant) -> None:
    """Per-query signals for eval/pooled_gates.py (gate tuning across several labelled corpora)."""
    keep = ("id", "split", "category", "answerable", "_has_idents")
    path.write_text(json.dumps([({k: q[k] for k in keep}, [{"signals": r["signals"], "relevant": bool(is_relevant(q, r))}
                                                           for r in res]) for q, res in rows]))


def choose(val_rows, test_rows, is_relevant, max_far: float) -> dict:
    """Per gate family: best config on val under the false-answer budget, then its test metrics."""
    out = {}
    for fam, configs in families(val_rows).items():
        scored = [(g, evaluate(val_rows, g, is_relevant)) for g in configs]
        ok = [(g, m) for g, m in scored if m["false_answer_rate"] <= max_far]
        if not ok:
            out[fam] = {"chosen": None, "note": f"no config met val false-answer rate <= {max_far}"}
            continue
        g, m = max(ok, key=lambda x: (x[1]["answer_coverage"], x[1]["precision_among_answered"],
                                      -x[1]["false_answer_rate"]))
        t = evaluate(test_rows, g, is_relevant)
        out[fam] = {"chosen": vars(g), "val": {k: v for k, v in m.items() if not isinstance(v, list)},
                    "test": t}
    return out
