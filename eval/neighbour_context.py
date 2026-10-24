"""Neighbour summaries instead of overlap (owner idea, 2026-10-24): passages are cut with no overlap, and the
local model writes one or two sentences about the passages just before and after each one. That context is
embedded with the passage, in place of the 150 characters each passage shares with its neighbours today.

    llama-server -m gemma-4-E2B-it-Q4_K_M.gguf -c 4096 --jinja --port 8090
    uv run --group eval python eval/neighbour_context.py http://127.0.0.1:8090/v1      # writes the contexts
    uv run --group eval python eval/tricks.py --overlap 0 --neighbour-context data/neighbour-context-k8s-sub-gemma.json

Reads the passages of the no-overlap index built by `tricks.py --overlap 0` (data/tricks-pg-<corpus>-c900-o0).
Contexts are cached in data/neighbour-context-<corpus>.json, keyed "<source_key>#<ordinal>", so a stopped run
resumes; tricks.py cuts the same passages again (same settings) and looks them up by the same key.
"""

import argparse
import json
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from knowledge_search import db, enrich

ROOT = Path(__file__).resolve().parent.parent
PROMPT = """Below is a passage from the document "{title}", with the text just before and just after it.
Write one or two sentences saying what the text before and after it is about, so that someone who reads only \
the passage knows its surroundings. Mention names, settings and commands from the surrounding text that the \
passage depends on. Do not repeat the passage. Reply with the sentences only.

<before>
{before}
</before>
<passage>
{passage}
</passage>
<after>
{after}
</after>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="local model server, e.g. http://127.0.0.1:8090/v1")
    ap.add_argument("--corpus", default="k8s-sub-gemma")
    ap.add_argument("--index", default="", help="tricks database tag (default <corpus>-c900-o0)")
    a = ap.parse_args()
    tag = a.index or f"{a.corpus}-c900-o0"
    out = ROOT / "data" / f"neighbour-context-{a.corpus}.json"
    done = json.loads(out.read_text()) if out.exists() else {}
    db.start_embedded(ROOT / "data" / f"tricks-pg-{tag}", database="postgres")
    with psycopg.connect(db._embedded.get_uri("ks_tricks"), row_factory=dict_row) as conn:
        rows = conn.execute("SELECT d.source_key, c.ordinal, c.doc_title, c.text FROM chunks c "
                            "JOIN documents d ON d.id = c.document_id ORDER BY d.source_key, c.ordinal").fetchall()
    print(f"{len(rows)} passages, {len(done)} already written", flush=True)
    t0, n = time.time(), 0
    for i, r in enumerate(rows):
        key = f"{r['source_key']}#{r['ordinal']}"
        if key in done:
            continue
        doc = r["source_key"]
        before = rows[i - 1]["text"] if i > 0 and rows[i - 1]["source_key"] == doc else "(start of the document)"
        after = rows[i + 1]["text"] if i + 1 < len(rows) and rows[i + 1]["source_key"] == doc else "(end of the document)"
        try:
            ctx = enrich.chat(a.url, "", PROMPT.format(title=r["doc_title"], before=before, passage=r["text"],
                                                       after=after), max_tokens=120, timeout=600)
        except enrich.LLMError as e:
            print(f"{key}: {e}", flush=True)
            continue
        done[key] = " ".join(ctx.split())
        n += 1
        if n % 10 == 0 or i == len(rows) - 1:
            out.write_text(json.dumps(done, indent=0))
            rate = (time.time() - t0) / n
            print(f"{len(done)}/{len(rows)} written, {rate:.1f} s each, ~{rate * (len(rows) - len(done)) / 3600:.1f} h left",
                  flush=True)
    out.write_text(json.dumps(done, indent=0))
    db._embedded._cleanup()


if __name__ == "__main__":
    main()
