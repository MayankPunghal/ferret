"""Embedding throughput: one model session using every core, or several sessions with fewer threads each
working on different batches of the same document (docs/plan-large-documents.md, step 1).

    uv run python eval/bench_parallel.py --passages 400 --layouts 1x0 2x2 4x1
    uv run python eval/bench_parallel.py --file some-large.pdf          # passages from a real document

A layout SxT is S sessions with T ONNX threads each (T=0: the runtime default, all cores). Every layout must
give the same vectors as 1x0 (exactness is checked: same model, same inputs, so only float noise). Run on an
otherwise idle machine and record the CPU model and core count with the results.
"""

import argparse
import os
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from knowledge_search.config import Settings
from knowledge_search.embedder import Embedder
from knowledge_search.parsing import chunk_document, parse_document

ROOT = Path(__file__).resolve().parent.parent


def passages(n: int, file: str | None, size: int) -> list[str]:
    if file:
        doc = parse_document(Path(file).read_bytes(), Path(file).name, 2000, 5_000_000)
        return [c.text for c in chunk_document(doc, size, 150)][:n]
    texts = []  # the sample corpus, repeated: realistic lengths, enough to time
    for f in sorted((ROOT / "fixtures" / "corpus").rglob("*.md")):
        doc = parse_document(f.read_bytes(), f.name, 2000, 5_000_000)
        texts += [c.text for c in chunk_document(doc, size, 150)]
    return (texts * (n // max(len(texts), 1) + 1))[:n]


def run(layout: str, texts: list[str], model: str, cache: str) -> tuple[float, np.ndarray]:
    sessions, threads = (int(x) for x in layout.split("x"))
    embs = [Embedder(model, cache, threads=threads, offline=True) for _ in range(sessions)]
    for e in embs:
        e.warm()
    step = embs[0].batch_size * 2  # the worker's step (ingest.py)
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    parts = [[texts[j] for j in order[i:i + step]] for i in range(0, len(texts), step)]
    t = time.perf_counter()
    with ThreadPoolExecutor(sessions) as pool:  # ONNX Runtime releases the GIL while it computes
        out = list(pool.map(lambda ip: embs[ip[0] % sessions].embed_documents(ip[1]), enumerate(parts)))
    secs = time.perf_counter() - t
    vecs = np.empty((len(texts), embs[0].spec.dim), dtype=np.float32)
    vecs[order] = np.vstack(out)
    return secs, vecs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--passages", type=int, default=400)
    ap.add_argument("--file", help="take the passages from this document instead of the sample corpus")
    ap.add_argument("--layouts", nargs="+", default=["1x0", "2x0", "2x2", "4x1"])
    ap.add_argument("--chunk-size", type=int, default=700)
    a = ap.parse_args()
    s = Settings()
    texts = passages(a.passages, a.file, a.chunk_size)
    print(f"{platform.processor() or platform.machine()} · {os.cpu_count()} logical cores · {len(texts)} passages "
          f"· {s.embed_model}")
    base = None
    for layout in a.layouts:
        secs, vecs = run(layout, texts, s.embed_model, str(s.model_cache))
        base = vecs if base is None else base
        diff = float(np.abs(vecs - base).max())
        print(f"{layout:>5}: {len(texts) / secs:6.1f} passages/s  ({secs:5.1f} s)  max |Δ| vs first layout {diff:.1e}")


if __name__ == "__main__":
    main()
