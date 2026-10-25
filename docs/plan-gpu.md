# Plan: GPU or CPU, switchable in Settings (handoff, 2026-10-24)

Owner request: "have an ability to run the embedding, search, summarization, enrichment, reranker all run on
GPU and CPU toggleable from settings". The owner's laptop has an **Intel integrated GPU**. The cloud
container that built this app has no GPU, so **nothing below has been run on a GPU: every speed or accuracy
effect is unmeasured**. This file is the plan for a local session on that laptop.

**Status (2026-10-25): step 1 run for embeddings.** Owner's laptop, 2026-10-25: Intel Iris Xe (driver 31.0.101.4575), i5-12500H, Windows 11; bge-base, 400 passages cut by the app (`data/app-passages.json`, median 740 characters), sorted by length as the app batches them; same passages in both runs. CPU: the app's own environment (onnxruntime 1.31). GPU: `onnxruntime-directml` 1.24.4 in its own environment (`scripts/gpu_probe.py`). Passages/s at batch 8 / 32 / 64 / 128: **CPU 5.2 / 4.5 / 4.0 / 3.6, GPU (DirectML) 1.0 / 2.2 / 2.7 / out of GPU memory** (DirectML "Not enough memory resources" at batch 128). Larger batches help the GPU, but its best (2.7) is about half the CPU's best (5.2, at the app's batch 8). Parity, from an earlier run on paragraph input: cosine between CPU and GPU vectors min 0.999987, mean 0.999992, which passes the bar. So the embedding switch is not worth building for this laptop. (An earlier version of this note said CPU 3.4 vs GPU 1.2; that run used unsorted paragraphs from the docs folder, so padding slowed both sides.) Not measured: OpenVINO and the reranker.

## What runs where today (all CPU)

| Work | Engine | Code | Thread setting |
|---|---|---|---|
| Passage and query embeddings | fastembed 0.9 → ONNX Runtime 1.31 (CPU provider) | `embedder.py` `Embedder._load` (`TextEmbedding(...)`) | `embed_threads` |
| Reranker | fastembed `TextCrossEncoder` → ONNX Runtime | `runtime.py` `rerank_scores` / `_onnx_class` | none |
| Summaries, document types, topics, titles (one job: "enrichment" and "summarisation" are the same model call) | llama.cpp `llama-server` b11836, CPU build | `enrich.py` `LocalServer`, `asset_name()` | `llm_threads` |
| Vector search | numpy matrix product over the in-memory index | `vectors.py` `MemoryIndex.top_k` | — |
| Keyword search, filters, permissions | PostgreSQL | `search.py` | — |

## Three switches, not five (my recommendation)

- **Summarisation and enrichment are one job.** One model call writes the summary, type, topics and title,
  so they get one switch.
- **"Search" itself should stay on the CPU.** The parts of a query that take time are the query embedding
  (about 20 ms) and the reranker (about 0.4 s), and those get their own switches. The vector search is one
  exact matrix product: a few milliseconds for 100k passages. On an integrated GPU the data would first be
  copied into memory shared with the CPU anyway. For the server scale target (millions of passages),
  choose the index by a measured scale test (`eval/scale_eval.py`), keeping exact re-scoring, not by
  device.

| Setting | Values | What it changes |
|---|---|---|
| `embed_device` | `cpu` (default) · `gpu` | the ONNX Runtime provider for the embedding model |
| `rerank_device` | `cpu` (default) · `gpu` | the ONNX Runtime provider for the reranker |
| `llm_device` | `cpu` (default) · `gpu` | which llama.cpp build runs, and `-ngl 99` (all layers on the GPU) |

Behaviour that matters more than the switch itself:

1. **Detect, don't assume.** Settings shows "GPU: Intel(R) Iris(R) Xe Graphics (OpenVINO)" or "no
   usable GPU found". The GPU option is disabled when nothing is detected.
2. **Fall back to the CPU loudly.** If the GPU session fails to load or run, use the CPU, log it, and show
   "GPU failed, using CPU: <reason>" next to the setting. Search must never break because of a driver.
3. **Show the device actually in use** on the Index status page (it can differ from the setting after a
   fallback).
4. **Switching device does not re-index** if the parity check below passes. If it fails, the device goes
   into the vector space key (`Runtime.version_key`), so a switch triggers the existing "re-index needed"
   banner.

## Engines per platform

| Platform | Embeddings and reranker (ONNX) | Local language model (llama.cpp) |
|---|---|---|
| Windows + Intel/AMD/NVIDIA GPU | **DirectML**: package `onnxruntime-directml` (replaces `onnxruntime`; wheels cp311–cp314, Windows only), provider `DmlExecutionProvider` | **Vulkan** build: `llama-b11836-bin-win-vulkan-x64.zip` (asset exists, checked) |
| Linux + Intel GPU | **OpenVINO**: plugin package `onnxruntime-ep-openvino` (works next to the stock `onnxruntime`, wheels for Linux x86_64 and Windows) or the legacy `onnxruntime-openvino` (1.24.1, replaces `onnxruntime`) | **Vulkan** build: `llama-b11836-bin-ubuntu-vulkan-x64.tar.gz` (exists; needs the Mesa Vulkan driver) |
| macOS Apple silicon | CoreML provider (in the stock macOS wheel); try last, small gain expected | the existing macOS build already uses Metal |
| NVIDIA on a server | `onnxruntime-gpu` (CUDA) | CUDA build of llama.cpp |

Also checked: the b11836 release has `win-sycl-x64` (Intel's own backend), but no OpenVINO build of llama.cpp.
Start with Vulkan: one build for Intel, AMD and NVIDIA, and no separate Intel runtime to install. Try SYCL
only if Vulkan is slow on the Iris Xe.

Gotchas known before trying:
- **fastembed takes `providers=[...]` and `cuda=`** (checked in 0.9.0 `TextEmbedding.__init__` and
  `TextCrossEncoder.__init__`), so `DmlExecutionProvider` can be passed by name. A **plugin** execution
  provider (`onnxruntime-ep-openvino`) is attached through `SessionOptions.add_provider_for_devices`, which
  fastembed does not expose. Check whether `providers=["OpenVINOExecutionProvider"]` works after
  `ort.register_execution_provider_library(...)`. If not, use the legacy `onnxruntime-openvino` wheel
  (provider by name, `provider_options=[{"device_type": "GPU"}]`), or load the ONNX file with a plain
  `InferenceSession` for the GPU path.
- **Precision.** Integrated GPUs often run in FP16. The owner's rule is accuracy before speed: ask for FP32
  (OpenVINO `load_config` `INFERENCE_PRECISION_HINT=f32`, or the older `precision="FP32"` /
  `"ACCURACY"`), and keep FP16 only if the parity check passes with it.
- **First run compiles the model for the GPU** (seconds to a minute). Set the OpenVINO `CACHE_DIR` under
  `data/models/ov-cache` and show "preparing the GPU…" with the existing loading spinner.
- **Memory.** An integrated GPU shares RAM: Gemma 4 E2B still needs ~3–4 GB, now counted as GPU memory.
  Keep `llm_device=cpu` as the default on 8 GB machines.
- **Installer.** The packages above replace or extend `onnxruntime`, so they cannot all be in one lock
  file. Add an optional extra (`uv sync --extra gpu-windows` / `--extra gpu-intel-linux`), offered by
  `setup.ps1` / `setup.sh` only when a GPU is detected. The default install stays CPU only and stays small.

## Implementation steps (small, in this order)

1. **Probe script, numbers only:** `scripts/gpu_probe.py`. It prints `onnxruntime.get_available_providers()`,
   `ort.get_ep_devices()` when the plugin is installed, and `llama-server --list-devices` for the Vulkan
   build. It also times 200 passages with bge-base on CPU vs GPU and prints the **cosine between CPU and
   GPU vectors** (min/mean). The owner runs it locally and pastes the numbers.
2. **Embeddings:** `Embedder(..., device)` → pass `providers=[...]` to `TextEmbedding`, fall back to CPU on
   any exception, and expose `Embedder.device_in_use`. Same for `TextCrossEncoder` in `runtime.py`.
3. **llama.cpp:** `asset_name(tag, gpu)` picks the `-vulkan-` asset, and the binary is kept in a separate
   folder (`llm/bin/b11836-vulkan`). `LocalServer` adds `-ngl 99` (and `--device Vulkan0` if more than one
   device is listed). If the server fails to start or its health check fails, restart it on the CPU build.
4. **Settings and API:** add the three fields to `runtime.LIMITS` / `validate` and to `admin.py` model
   rows. In the UI, a CPU/GPU segmented control under each model selector (reuse `.seg` from the AI
   assistants page), disabled with the reason when no GPU is detected.
5. **Tests:** a fake provider list (monkeypatch `get_available_providers`) checks the selection and the
   fallback path. A fake `llama-server` that exits immediately checks the restart on the CPU build. No GPU
   needed in CI.

## How to decide it is worth it (measure on the owner's laptop)

| Check | Pass when |
|---|---|
| Embedding parity | min cosine(CPU vector, GPU vector) ≥ 0.999 over 1,000 passages; Kubernetes eval (`eval/corpus_eval.py`) hit@1 / success@5 identical to the CPU run |
| Reranker parity | the same top-10 order on the 72 Kubernetes questions (or any changed rank explained by score ties) |
| Summaries | `eval/llm_labels.py` document types and `eval/summary_quality.py` within noise of the CPU run (GPU arithmetic changes some tokens) |
| Speed | report passages/s for indexing, query p50/p95, and seconds per summary, CPU vs GPU; keep the GPU option only where it is faster **and** parity passes |

Expectations, all unmeasured: on an Iris Xe-class GPU, llama.cpp Vulkan usually speeds up prompt reading
far more than writing. Our summaries read about 1,500 tokens and write about 100, so this is the most
likely real win. Embedding gains on an integrated GPU are often small for a 110M-parameter model at
batch 8. The reranker reads only 10 passages per query, so fixed overhead per call may cancel the gain.

## Sources

- ONNX Runtime OpenVINO provider: https://onnxruntime.ai/docs/execution-providers/OpenVINO-ExecutionProvider.html
- OpenVINO plugin package: https://pypi.org/project/onnxruntime-ep-openvino/
- DirectML provider: https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider
- llama.cpp Vulkan benchmarks, including Intel integrated GPUs: https://github.com/ggml-org/llama.cpp/discussions/10879
- llama.cpp on Intel GPUs with SYCL: https://github.com/ggml-org/llama.cpp/discussions/23313
