"""Vision-model trial: how well does a local model read the text in images, and the arrows in diagrams?

    llama-server -m MODEL.gguf --mmproj MMPROJ.gguf -c 8192 --port 8082 --temp 0     # any OpenAI-style server
    uv run --group eval python eval/image_ocr.py --name paddleocr-vl-1.6 --prompt "OCR:"

Images and their known text: eval/image_set.py. For every image the model is asked to transcribe it;
scored after normalising (lowercase, words of letters and digits; Markdown/HTML markup removed):
  recall     share of the true words found in the output (what search can find)
  precision  share of the output's words that are in the image (the rest is made up or misread)
  cer        character error rate (edit distance / true length)
For diagrams (org chart, flowcharts) it is also asked to list the arrows ("From -> To"), scored as edge
recall and precision, and for diagrams and slides to describe the image (kept for reading, not scored).
Writes eval/results/images-<name>.json.
"""

import argparse
import base64
import json
import re
import statistics
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "data" / "images-eval"
READ = ("Transcribe all the text in this image exactly as written, in reading order. Keep tables as rows. "
        "Output only the text.")
EDGES = ("List every arrow in this diagram, one per line, as: From -> To. Use the names written in the boxes. "
         "Output only the list.")
DESCRIBE = ("Describe this image for a search index in 2-4 sentences: what it is and what it shows, including "
            "the relationships it states. Only state what is visible.")


def ask(url: str, image: Path, prompt: str, max_tokens: int) -> tuple[str, float, dict]:
    data = base64.b64encode(image.read_bytes()).decode()
    body = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        {"type": "text", "text": prompt}]}], "temperature": 0, "max_tokens": max_tokens}
    req = urllib.request.Request(url + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=1800) as r:
        out = json.load(r)
    msg = out["choices"][0]["message"]
    return (msg.get("content") or ""), time.perf_counter() - t, out.get("usage", {})


def words(text: str) -> list[str]:
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>|[|*#`]|\\[()\[\]]", " ", text)  # Markdown / HTML / LaTeX markup
    return re.findall(r"[a-z0-9]+", text.lower())


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score_text(truth: str, out: str) -> dict:
    t, o = words(truth), words(out)
    common = sum((Counter(t) & Counter(o)).values())
    ts, os_ = " ".join(t), " ".join(o)
    return {"recall": round(common / len(t), 3) if t else 0.0, "precision": round(common / len(o), 3) if o else 0.0,
            "cer": round(levenshtein(ts, os_[: 3 * len(ts)]) / len(ts), 3) if ts else 0.0}


def score_edges(truth: list, out: str) -> dict:
    """An arrow counts when a line names both ends in the right order (by first name or first word)."""
    found = []
    for line in out.splitlines():
        parts = re.split(r"\s*(?:->|→|⟶|=>)\s*", line.strip(" -*•\t"))
        if len(parts) >= 2:
            found.append((" ".join(words(parts[0])), " ".join(words(parts[-1]))))

    def key(name):
        return words(name)[0]

    hit = sum(any(key(a) in f and key(b) in g for f, g in found) for a, b in truth)
    right = sum(any(key(a) in f and key(b) in g for a, b in truth) for f, g in found)
    return {"edge_recall": round(hit / len(truth), 3), "edge_precision": round(right / len(found), 3) if found else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--url", default="http://localhost:8082")
    ap.add_argument("--prompt", default=READ, help="transcription prompt (OCR models: 'OCR:', 'Text Recognition:')")
    ap.add_argument("--read-only", action="store_true", help="only transcription (OCR-only models)")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--only", default="", help="comma-separated image names")
    a = ap.parse_args()
    truth = json.loads((IMAGES / "truth.json").read_text(encoding="utf-8"))
    names = a.only.split(",") if a.only else list(truth)
    rows = []
    for name in names:
        t = truth[name]
        out, secs, usage = ask(a.url, IMAGES / f"{name}.png", a.prompt, a.max_tokens)
        row = {"image": name, "kind": t["kind"], "seconds": round(secs, 1), "image_tokens": usage.get("prompt_tokens"),
               **score_text(t["text"], out), "output": out}
        if t["edges"] and not a.read_only:
            edges, s2, _ = ask(a.url, IMAGES / f"{name}.png", EDGES, 512)
            row.update(score_edges(t["edges"], edges), edges_output=edges)
            row["seconds"] = round(row["seconds"] + s2, 1)
        if t["kind"] in ("orgchart", "flowchart", "slide", "table") and not a.read_only:
            row["description"], s3, _ = ask(a.url, IMAGES / f"{name}.png", DESCRIBE, 300)
            row["seconds"] = round(row["seconds"] + s3, 1)
        rows.append(row)
        print(f"{name:32} {t['kind']:9} recall {row['recall']:.2f} precision {row['precision']:.2f} "
              f"cer {row['cer']:.2f}" + (f" edges {row['edge_recall']:.2f}/{row['edge_precision']:.2f}"
                                         if "edge_recall" in row else "") + f"  {row['seconds']}s", flush=True)
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    summary = {k: {m: round(statistics.mean(r[m] for r in v), 3) for m in ("recall", "precision", "cer")}
               for k, v in by_kind.items()}
    report = {"model": a.name, "prompt": a.prompt,
              "overall": {m: round(statistics.mean(r[m] for r in rows), 3) for m in ("recall", "precision", "cer")},
              "edges": {m: round(statistics.mean(r[m] for r in rows if m in r), 3)
                        for m in ("edge_recall", "edge_precision") if any(m in r for r in rows)},
              "seconds_median": statistics.median(r["seconds"] for r in rows), "by_kind": summary, "rows": rows}
    out = ROOT / "eval" / "results" / f"images-{a.name}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("model", "overall", "edges", "seconds_median", "by_kind")}, indent=1))


if __name__ == "__main__":
    main()
