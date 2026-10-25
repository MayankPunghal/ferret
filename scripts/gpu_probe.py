"""GPU probe (docs/plan-gpu.md step 1): numbers only, changes nothing. Lists ONNX Runtime's providers and times
the embedding model on the CPU and on the GPU over the same passages, with the cosine between the two vectors of
each passage (accuracy must not change).

The GPU build of ONNX Runtime replaces the CPU one, so run this in its own environment, e.g. on Windows:

    uv venv data/gpu-env && uv pip install --python data/gpu-env fastembed
    uv pip uninstall --python data/gpu-env onnxruntime && uv pip install --python data/gpu-env onnxruntime-directml
    data/gpu-env/Scripts/python scripts/gpu_probe.py --file some-large-document.md
"""

import argparse
import re
import time

import numpy as np
import onnxruntime as ort
from fastembed import TextEmbedding

ap = argparse.ArgumentParser()
ap.add_argument("--file", required=True, help="a large Markdown or text document; its paragraphs are the passages")
ap.add_argument("--passages", type=int, default=200)
ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
ap.add_argument("--cache", default="data/models")
a = ap.parse_args()

print("onnxruntime", ort.__version__, "| providers:", ort.get_available_providers())
text = open(a.file, encoding="utf-8").read()
passages = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 200][: a.passages]
print(len(passages), "passages, median", int(np.median([len(p) for p in passages])), "characters")

gpu = next((p for p in ("DmlExecutionProvider", "OpenVINOExecutionProvider", "CUDAExecutionProvider")
            if p in ort.get_available_providers()), None)
runs = {}
for name, providers in (("cpu", ["CPUExecutionProvider"]), ("gpu", [gpu] if gpu else None)):
    if providers is None:
        print("gpu: no GPU provider in this onnxruntime build")
        continue
    t = time.perf_counter()
    m = TextEmbedding(a.model, cache_dir=a.cache, providers=providers)
    load = time.perf_counter() - t
    list(m.embed(passages[:8]))  # warm up (the GPU compiles on first use)
    t = time.perf_counter()
    runs[name] = np.array(list(m.embed(passages, batch_size=8)))
    s = time.perf_counter() - t
    print(f"{name} ({providers[0]}): load {load:.1f} s, {len(passages) / s:.1f} passages/s")
if len(runs) == 2:
    c, g = (x / np.linalg.norm(x, axis=1, keepdims=True) for x in (runs["cpu"], runs["gpu"]))
    cos = (c * g).sum(axis=1)
    print(f"cosine(cpu, gpu): min {cos.min():.6f}, mean {cos.mean():.6f}  (pass: min >= 0.999)")
