"""Real user questions for the Kubernetes corpus: Stack Overflow questions tagged `kubernetes` whose accepted
answer links a kubernetes.io/docs page that is in data/corpora/kubernetes. The question title is the query (how
people actually phrase things, unlike the page); the linked pages are the relevant documents.

    uv run python eval/make_so_queries.py            # -> eval/so_k8s_queries.jsonl

Stack Overflow content is CC BY-SA 4.0 (https://stackoverflow.com/help/licensing); every row keeps its
question URL as attribution. Labels are page-level and noisy (an answer may link a page that only helps), the
same for every variant compared, so differences between variants stay meaningful.
"""

import argparse
import gzip
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.stackexchange.com/2.3"
LINK = re.compile(r'href="https?://(?:[\w.-]+\.)?kubernetes\.io/(?:[a-z]{2}/)?docs/([^"#?]+)')


def get(path: str, **params) -> dict:
    url = f"{API}/{path}?" + urllib.parse.urlencode({"site": "stackoverflow", **params})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept-Encoding": "gzip"}), timeout=60) as r:
            data = r.read()
    except urllib.error.HTTPError as e:  # quota or paging limits: the body says which
        data = e.read()
    out = json.loads(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)
    if out.get("backoff"):
        time.sleep(out["backoff"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpora" / "kubernetes"))
    ap.add_argument("--pages", type=int, default=25, help="pages of 100 questions (by votes)")
    ap.add_argument("--max-links", type=int, default=3, help="skip answers that link more corpus pages")
    ap.add_argument("--out", default=str(ROOT / "eval" / "so_k8s_queries.jsonl"))
    a = ap.parse_args()
    pages = {p.stem for p in Path(a.corpus).glob("*.md")}
    questions = {}
    for page in range(1, a.pages + 1):
        res = get("questions", tagged="kubernetes", sort="votes", order="desc", pagesize=100, page=page)
        if not res.get("items"):  # the end, or the daily quota (shared per IP) is used up
            print("stopped at page", page, {k: v for k, v in res.items() if k != "items"})
            break
        for q in res["items"]:
            if q.get("accepted_answer_id"):
                questions[q["accepted_answer_id"]] = q
    rows, ids = [], list(questions)
    for i in range(0, len(ids), 100):
        res = get("answers/" + ";".join(map(str, ids[i:i + 100])), filter="withbody", pagesize=100)
        for ans in res.get("items", []):
            q = questions[ans["answer_id"]]
            hits = []
            for path in LINK.findall(ans["body"]):
                name = path.rstrip("/").split("/")[-1].removesuffix(".md")
                if name in pages and name not in hits:
                    hits.append(name)
            if 1 <= len(hits) <= a.max_links:
                rows.append({"query": html.unescape(q["title"]), "relevant": hits, "category": "so_question",
                             "source": q["link"], "score": q["score"]})
    rows.sort(key=lambda r: -r["score"])
    with open(a.out, "w") as f:
        for n, r in enumerate(rows):
            f.write(json.dumps({"id": f"so{n:03d}", **r, "split": "val" if n % 2 else "test"}) + "\n")
    print(f"{len(rows)} questions from {len(questions)} with accepted answers -> {a.out}")


if __name__ == "__main__":
    main()
