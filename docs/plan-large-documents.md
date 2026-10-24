# Plan: index very large documents faster (owner request, 2026-10-24)

Owner request: "What can we do for super large docs with a high count of passages? Can we parallelize
more?" Seen on a `db-tables.md` of 449 passages that sat at "embedding 192/449" while other documents waited.

**Status: plan.** Step 1 measured on an idle 4-core CPU (2026-10-24): **no worthwhile gain there**; steps 2–3
are the useful ones on a laptop. Everything else below is unmeasured.

## Where the time goes today

- One worker (`inprocess_workers = 1`) takes one whole document at a time (`ingest.py` `Worker.run_one`):
  read the file → cut into passages → embed in batches of 16 (sorted by length) → write everything in one
  transaction. Jobs are taken in upload order (`ORDER BY j.id`).
- **Embedding dominates.** bge-base on a 4-core CPU embeds about 7 passages per second, measured on an idle
  VM (`embedder.py` comment, HANDOFF). One ONNX session already uses every core, so "more threads" alone
  does not help.
- The time to read a large PDF is not measured separately yet. Logs → Activity records each step's time for
  every indexed document, so one large PDF there shows it.
- Limits per document: 2,000 PDF pages and 5,000,000 characters (`config.py`), about 7,000 passages at the
  default size. On 4 cores that is about 17 minutes for one maximal document, and the queue waits behind it.
- At the archive scale target (~40k multi-MB PDFs, say 300 passages each = 12M passages), 7 passages/s is
  about 20 days on 4 cores. The CPU alone cannot reach that target. The GPU plan
  ([plan-gpu.md](plan-gpu.md)) and the server's many cores are what matter there.

## What to do, in order

### 1. Several embedding sessions per document (exact, biggest gain on a CPU)

Split one document's batches across S model sessions with T threads each, instead of one session using every
core. ONNX Runtime does not scale perfectly across cores for small batches, so two half-size sessions working
side by side can do more in total.

- **Preliminary measurement** (`eval/bench_parallel.py`, 4 cores, 64 passages, **while another model was
  using the same CPU**, so it is only a signal): 1 session × all cores **6.7 passages/s**, 2 sessions × 2
  threads **11.2 passages/s**. The vectors were identical (max difference 0), so accuracy does not change.
- **Idle measurement (2026-10-24, 4-core Intel Xeon 2.1 GHz VM, bge-base, 400 passages from 8 large Kubernetes
  pages, median 774 characters, `--file`):** 1×all cores **6.9**, 2×all cores 5.4, **2×2 threads 7.3**, 4×1
  thread 7.2 passages/s; vectors identical in every layout. +6% at best, within noise and far from the 1.5×
  target: **the earlier 1.7× was an artefact of a busy CPU.** Do not build step 1 for 4-core machines; measure
  again on the server (16–32 cores), where one session may not use every core well.
- Note: the script's default input (the sample corpus) has very short passages (median 152 characters) and
  gives ~43 passages/s, which says nothing about real documents. Always pass `--file` with a large document.
- **Measure on other machines:** `uv run python eval/bench_parallel.py --passages 400 --layouts 1x0
  2x0 2x2 4x1`, on the laptop and on the server (for example 16 or 32 cores: try 4x4, 8x4). Keep the
  fastest layout whose vectors match.
- **Build:** an `EmbedderPool` of S sessions (the same model loaded S times; about 250–400 MB of RAM each,
  to be measured) that maps the batches of one document over the sessions. New setting `embed_sessions`
  (`auto` = the measured best for the core count; 1 = today's behaviour). Queries use any free session.
- **Test:** vectors from the pool equal single-session vectors for the same passages, and progress still
  counts up.

### 2. Keep small documents moving while a large one indexes

With one worker, a 2,000-page manual blocks every page behind it. Options, simplest first:

- **Two workers sharing the session pool** (`inprocess_workers = 2`): a large document and the small ones
  index side by side. This is the same total speed, but small documents stop waiting. It works today
  (workers claim with `SKIP LOCKED`), but each worker uses its own model sessions, so with step 1 both must
  share one pool.
- Optionally, take **small jobs first** when both are waiting (order the claim by file size, then id). Do this
  only if the two workers are not enough; it can starve a large document while uploads keep coming.

### 3. Re-use the vectors of unchanged passages (exact, large gain for updates)

Today, when a document is edited and re-uploaded, or a synced folder or website copy is updated, every
passage is embedded again, even if one page changed. Store a hash of each passage's embedding text with the
model name (`chunks.embed_hash`). On re-index, take the stored vector wherever the hash and model match, and
embed only the rest. The result is the same, because the same text through the same model gives the same
vector.

- Editing one page of a 449-passage document would embed only a handful of passages.
- "Reindex everything" after a change that does not alter passage text (for example a parser version bump)
  would be close to free.
- Test: a re-index with one edited section embeds only the passages that changed, and search results are
  identical.

### 4. Read the next file while the current one embeds (only if reading is slow)

If the Activity steps show that reading large PDFs takes a meaningful share (more than ~20%), parse the next
queued document in a background thread while the current one embeds. Skip this if reading is quick.

### 5. GPU, and more machines (the archive scale)

- GPU: [plan-gpu.md](plan-gpu.md). A discrete GPU is the largest single gain for the 40k-PDF target. It is
  unmeasured here (no GPU in the build container).
- More machines: the queue design allows `ferret worker` on other machines sharing the database, but the blob
  store (`data/blobs`) must then be shared storage. This is untested; check it before relying on it.

## Not recommended

- **A smaller or 8-bit (quantized) model** to go faster: it changes the vectors and has measured accuracy
  costs elsewhere. Accuracy comes first, so only with an evaluation showing no loss.
- **Splitting one document into several jobs:** atomic replacement of a document's passages, its links and
  its progress all get harder, and on one machine it gains nothing over step 1.
- **Raising the batch size:** measured slower on 4 cores (16: 7.0, 32: 6.3 against 8: 7.4 passages/s).

## Acceptance

- `eval/bench_parallel.py` results recorded (hardware, layouts, passages/s) in docs/evaluation.md.
- Indexing a 449-passage document is at least 1.5× faster on the 4-core machine, or the plan records why
  not.
- Vectors are identical to today's (test).
- While a large document indexes, a small upload becomes searchable without waiting for it.
- Re-index after a one-section edit embeds only the changed passages (test).
