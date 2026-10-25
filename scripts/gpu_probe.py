"""GPU probe (docs/plan-gpu.md step 1): numbers only, changes nothing. Times the embedding model at several batch
sizes on one device and compares its vectors with another run's (accuracy must not change).

The fair comparison is the app's own environment on the CPU against the GPU build of ONNX Runtime, which
replaces the CPU one and so needs its own environment. Same file, same passages, both runs (Windows):

    uv run python scripts/gpu_probe.py --file big.md --save data/cpu-vectors.npy
    uv venv data/gpu-env && uv pip install --python data/gpu-env fastembed
    uv pip uninstall --python data/gpu-env onnxruntime && uv pip install --python data/gpu-env onnxruntime-directml
    data/gpu-env/Scripts/python scripts/gpu_probe.py --file big.md --gpu --compare data/cpu-vectors.npy
"""

import argparse
import json
import re
import time

import numpy as np
import onnxruntime as ort
from fastembed import TextEmbedding

ap = argparse.ArgumentParser()
ap.add_argument("--file", required=True, help="a JSON list of passages cut by the app (realistic), or a document "
                "whose paragraphs are the passages")
ap.add_argument("--passages", type=int, default=400)
ap.add_argument("--batches", type=int, nargs="+", default=[8, 32, 64, 128])
ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
ap.add_argument("--cache", default="data/models")
ap.add_argument("--gpu", action="store_true", help="use the GPU provider (DirectML, OpenVINO or CUDA)")
ap.add_argument("--save", help="write this run's vectors (.npy)")
ap.add_argument("--compare", help="cosine of this run's vectors against a saved run's")
a = ap.parse_args()

provider = "CPUExecutionProvider"
if a.gpu:
    provider = next(p for p in ("DmlExecutionProvider", "OpenVINOExecutionProvider", "CUDAExecutionProvider")
                    if p in ort.get_available_providers())
text = open(a.file, encoding="utf-8").read()
if a.file.endswith(".json"):  # passages cut by the app (a list of strings), the realistic input
    passages = json.loads(text)[: a.passages]
else:
    passages = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 200][: a.passages]
passages.sort(key=len)  # as the app batches them (ingest.py): similar lengths together, less padding
print(f"onnxruntime {ort.__version__} | {provider} | {len(passages)} passages, median "
      f"{int(np.median([len(p) for p in passages]))} characters")
m = TextEmbedding(a.model, cache_dir=a.cache, providers=[provider])
list(m.embed(passages[:8]))  # warm up (the GPU compiles on first use)
vectors = None
for b in a.batches:
    t = time.perf_counter()
    v = np.array(list(m.embed(passages, batch_size=b)))
    print(f"batch {b:>3}: {len(passages) / (time.perf_counter() - t):5.1f} passages/s")
    vectors = v if vectors is None else vectors
if a.save:
    np.save(a.save, vectors)
if a.compare:
    c, g = (x / np.linalg.norm(x, axis=1, keepdims=True) for x in (np.load(a.compare), vectors))
    cos = (c * g).sum(axis=1)
    print(f"cosine against {a.compare}: min {cos.min():.6f}, mean {cos.mean():.6f}  (pass: min >= 0.999)")
