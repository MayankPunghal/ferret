"""Run: python eval/topic_links_eval.py qwen3-5-0-8b gemma-4-E2B

Offline check: do documents sharing model-written topics with a top result add the pages a relationship
question needs? Uses the stored relational_eval rows (results per question) and the summary files."""
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

R = json.loads(Path("eval/results/relational-kubernetes-nodesc-BAAI_bge-base-en-v1.5.json").read_text())
Q = {json.loads(l)["id"]: json.loads(l) for l in Path("eval/k8s_relational.jsonl").read_text().splitlines() if l.strip()}
def norm(t): return re.sub(r"s$", "", " ".join(re.sub(r"[^a-z0-9 ]", " ", t.lower()).split()))
for name in sys.argv[1:]:
    S = json.loads(Path(f"eval/results/summaries-kubernetes-nodesc-{name}.json").read_text())
    topics = {k.removesuffix(".md"): {norm(t) for t in v.get("topics", []) if norm(t)} for k, v in S.items() if isinstance(v, dict)}
    df = Counter(t for ts in topics.values() for t in ts); N = len(topics)
    for per in (2, 4):
        hits, shown = [], []
        for row in R["rows"]:
            q = Q[row["id"]]; res = row["results"]; extra = []
            for d in res[:3]:
                mine = topics.get(d, set())
                sc = {o: sum(math.log(N / df[t]) for t in mine & ts) for o, ts in topics.items() if o != d and o not in res and mine & ts}
                extra += [o for o, _ in sorted(sc.items(), key=lambda x: -x[1])[:per]]
            docs = list(dict.fromkeys(res + extra))
            hits.append(all(any(x in g for x in docs) for g in q["needs"])); shown.append(len(docs))
        print(f"{name} topic links top{per}/result: all needs {statistics.mean(hits):.3f}  shown median {statistics.median(shown)}")
print("baseline results only:", R["results"]["all_needs"], "| current related rows:", R["with_related"])
print("ranked_by_question:", {k: v["all_needs"] for k, v in R["ranked_by_question"].items()}, "| more results:", {k: v["all_needs"] for k, v in R["control_more_results"].items()})
