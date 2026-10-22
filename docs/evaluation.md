# Evaluation

All numbers below were produced by the scripts in `eval/` on the development machine: **4 vCPU Intel
Xeon @ 2.1 GHz, 16 GB RAM, no GPU**. Several runs shared the CPU with other jobs; latencies marked
"contended" are pessimistic. Raw outputs: `eval/results/`.

Two datasets, for two different questions:

| Dataset | Size | Purpose | Caveat |
|---|---|---|---|
| **Fixtures** (`fixtures/corpus`, FICTIONAL technical docs) | 12 docs, 56 labelled queries (28 val / 28 test) incl. exact IDs, paraphrases, PDFs, conflicts, permission-restricted, unanswerable, hard negatives | behaviour on technical content (identifiers, runbooks, API docs); threshold tuning | tiny: one query moves a rate by 6–8 points |
| **BEIR SciFact** (public, human-labelled) | 2,000-abstract subset (all relevant + random distractors), 300 queries | model comparison; scale and abstention | scientific claims, not IT docs; subset is easier than the full 5,183-doc corpus |

For abstention at scale (`eval/scale_eval.py`), half of the SciFact queries have **all their relevant
abstracts removed** from the index → 122 realistic *unanswerable* queries whose topic is still covered by
other papers (hard negatives), plus 150 answerable ones; thresholds are chosen on val (138) and reported
on test (134).

## 1. Embedding models (BEIR SciFact subset, exact search, `eval/beir_screen.py`)

| Model | Licence | Dim | nDCG@10 | Recall@10 | Recall@100 | Index docs/s | Query embed p50 |
|---|---|---|---|---|---|---|---|
| BAAI/bge-small-en-v1.5 | MIT | 384 | 0.779 | 0.884 | 0.980 | 8.3 | 6.6 ms |
| **BAAI/bge-base-en-v1.5** (default) | MIT | 768 | **0.818** | **0.924** | 0.980 | 3.3 | 23 ms |
| snowflake/snowflake-arctic-embed-m | Apache-2.0 | 768 | 0.814 | 0.922 | 0.980 | 3.7 | 22 ms |
| nomic-ai/nomic-embed-text-v1.5 | Apache-2.0 | 768 | — | — | — | — | killed (out of memory before batch/token caps were added; not re-run) |
| Qwen/Qwen3-Embedding-0.6B (int8) | Apache-2.0 | 1024 | not measured (estimated >1 h to index 2,000 docs on this CPU) | | | | |

Abstracts average ~1,500 characters; product chunks are ~900, so indexing is faster in practice.

## 2. Retrieval branches and fusion (real pipeline, bge-base, 1,873 docs indexed, 150 answerable queries)

| Branches | Keyword weight | nDCG@10 | Recall@10 |
|---|---|---|---|
| dense only | — | 0.815 | 0.894 |
| keyword + identifier only | — | 0.593 | 0.700 |
| hybrid | 1.0 (old equal-weight RRF) | 0.746 | 0.841 |
| hybrid | 0.5 | 0.766 | 0.871 |
| hybrid | 0.25 | 0.780 | 0.892 |
| **hybrid (default)** | **0.1** | **0.794** | 0.887 |
| hybrid | 0 (dense + identifiers) | 0.804 | 0.900 |

Fixtures (bge-base): document Hit@1 = 1.0 for every weight; passage Hit@1 0.969 at weights 0–0.25 vs
0.875 at 1.0. Weight 0.1 keeps keyword-only matches as a fallback (rare internal terms) at a ~1-point cost.
Ingestion through the pipeline: 1,873 docs / 3,573 chunks in 25 min (1.24 docs/s, contended).
Query latency p50 ~180–190 ms, p95 ~310 ms (contended; query embedding ~90 ms of it).

## 3. Abstention (evidence gate)

**Fixtures, held-out test (12 unanswerable incl. hard negatives and permission-restricted, 16 answerable):**

| Model / gate (chosen on val) | False-answer rate | Answer coverage | Precision among answered |
|---|---|---|---|
| bge-small, similarity 0.88/0.76/0.34 | 1/12 | 0.56–0.63 | 0.90 |
| **bge-base, similarity 0.78/0.72/0.20 (default)** | 1/12 | 0.56 | 0.90 |
| bge-small + MiniLM reranker gate | 2/12 | 0.81 | 0.87 |

Permission-restricted queries: 0 answered, no restricted passage in any candidate list (also enforced by
tests). IDF-weighted keyword coverage gave no improvement over plain coverage on either dataset.

**SciFact at scale (122 unanswerable hard negatives / 150 answerable), similarity gates, bge-base:**

| Gate | False-answer rate | Coverage | Precision |
|---|---|---|---|
| 0.78 / 0.72 / 0.20 (shipped) | 0.205 | 0.560 | 0.670 |
| 0.88 / 0.76 / 0.34 | 0.164 | 0.427 | 0.655 |
| 0.84 / 0.80 / 0.34 | 0.131 | 0.360 | 0.657 |
| 0.88 / 0.84 / 0.50 | 0.115 | 0.260 | 0.585 |

No similarity threshold reached ≤ 5% false answers on val. **Conclusion:** embedding similarity tells
whether a passage is *on topic*, not whether it *answers* the question; when closely related documents
exist, it cannot reliably detect that the specific answer is missing. The UI therefore says "Relevant
passages found" (not "evidence found"), and a stronger judge is needed for trustworthy abstention:
a cross-encoder (§4) or, in the planned answer-generation mode, an LLM/entailment check.

## 4. Rerankers (offline cross-encoders)

Real pipeline (bge-base, keyword weight 0.1, 1,873 docs), rerank the top **10** fused passages:

| Reranker | Licence | Download | SciFact nDCG@10 | Recall@10 | Rerank p50 / p95 | Gate at scale (best, ≤20% val budget) |
|---|---|---|---|---|---|---|
| none | — | — | 0.795 | 0.887 | — | FAR 0.148 / coverage 0.466 |
| **Xenova/ms-marco-MiniLM-L-6-v2 (default)** | Apache-2.0 | ~80 MB | **0.809** | 0.887 | 373 ms / — (contended) | FAR 0.148 / coverage 0.507 |
| BAAI/bge-reranker-base | MIT | ~1 GB | 0.812 | 0.887 | 1.9 s / 2.4 s (contended) | FAR 0.213 / coverage 0.562 |

Screen-only comparison on full abstracts with the top **30**: MiniLM *hurt* (0.818 → 0.775) — with many
candidates it promotes wrong passages; hence the default of 10 candidates.

Fixtures with the default (bge-base + MiniLM top-10): document Hit@1 1.0, passage Hit@1 0.969, evidence
gate unchanged (1/12 false answers, 56% coverage, precision 0.90), query latency p50 176 ms / p95 233 ms.

**Finding:** neither reranker makes abstention trustworthy on hard negatives — cross-encoders also judge
*relevance*. Distinguishing "related paper" from "the paper that supports this claim" is a verification
(entailment) problem; it belongs to the answer-generation stage (LLM/NLI check of each cited claim).
Rerankers are therefore used for **ordering** (default), with the similarity gate deciding evidence.

## 5. Load (earlier run, bge-small, single API process, client on the same 4 vCPUs)

| Concurrency | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|
| 1 | 60 req/s | 14.5 ms | 26.5 ms | 59 ms | 0 |
| 16 | 65 req/s | 188 ms | 261 ms | 286 ms | 0 |

Not re-measured with bge-base (expect query embedding ~3–4× slower).

## Not measured

Real company corpus; corpora beyond ~4k chunks; Qwen3/large models; GPU; Windows performance; Docker
image; multilingual content.

## Reproduce

```bash
uv run --group eval python eval/run_eval.py --model BAAI/bge-base-en-v1.5 --lexical-weight 0.1
uv run --group eval python eval/beir_screen.py --max-docs 2000 --models BAAI/bge-small-en-v1.5 BAAI/bge-base-en-v1.5
uv run --group eval python eval/scale_eval.py --model BAAI/bge-base-en-v1.5 --reranker "" --lexical-weight 0.1
uv run --group eval python eval/scale_eval.py --model BAAI/bge-base-en-v1.5 --reuse --reranker BAAI/bge-reranker-base --rerank-candidates 10
```
