# Evaluation

**Scope warning.** All numbers come from a **12-document fictional corpus** (33 chunks) and **56 labelled
queries** (28 val / 28 test). They show the pipeline behaves as designed; they do **not** validate
quality on company data, and with 12 unanswerable test queries a single query moves the false-answer
rate by 8 points. Nothing here is a calibrated probability.

Reproduce: `uv run python eval/run_eval.py --models <model…> [--rerankers <model…>]`
(raw outputs: `eval/results/*.json`, `eval/results/summary*.txt`). Hardware for all timings: dev
container, 4 vCPU Intel Xeon @ 2.1 GHz, 15 GB RAM, no GPU.

## Query set (`eval/queries.jsonl`, version 1)

| Category | n | Expected behaviour |
|---|---|---|
| exact_id (error codes, API paths, config keys, filenames, versions, restricted code name for HR user) | 10 | answer, right passage |
| conceptual / paraphrase (incl. PDF pages, HR content for HR user) | 20 | answer, right document |
| conflict (2024 vs 2025 expense policy) | 2 | `conflicting_evidence` |
| unanswerable (off-topic) | 8 | abstain |
| hard_negative (look-alike IDs, wrong subject, missing fact) | 12 | abstain |
| restricted (answer exists only in a collection the user cannot read) | 4 | abstain, no leak |

## Retrieval (answerable queries, doc-level unless noted; `full` = shipped pipeline)

bge-small-en-v1.5:

| variant | Hit@1 | Recall@5 | MRR@10 | nDCG@10 | passage Hit@1 |
|---|---|---|---|---|---|
| lexical only | 0.969 | 1.0 | 0.984 | 0.988 | 0.781 |
| dense only | 1.0 | 1.0 | 1.0 | 1.0 | 0.938 |
| lexical + dense (RRF) | 1.0 | 1.0 | 1.0 | 1.0 | 0.812 |
| full (+ exact identifiers) | 1.0 | 1.0 | 1.0 | 1.0 | 0.875 |

arctic-embed-s and granite-small-r2 give the same doc-level numbers (dense passage Hit@1 0.969). The
corpus is too small for doc-level ranking to discriminate between variants. One real signal: **fusion
lowers passage-level Hit@1 vs dense-only** (0.875 vs 0.938) — the title-weighted lexical branch favours
a document's first section. Candidate fix to test on real data: lower the title weight or rank lexical on
heading+body only.

## Abstention (shipped gate, thresholds chosen on val, reported on held-out test)

Gate: supported if the passage contains a query identifier; identifier queries need such a match;
otherwise cosine ≥ 0.88, or cosine ≥ 0.76 and ≥ 34% of query terms present.

| model | split | false-answer rate (unanswerable+hard neg+restricted) | answer coverage (answerable) | precision among answered | conflicts detected |
|---|---|---|---|---|---|
| bge-small-en-v1.5 (shipped) | val | 0/12 | 10/16 = 0.625 | 1.0 | 1/1 |
| bge-small-en-v1.5 (shipped) | **test** | **1/12 = 0.083** | **9/16 = 0.562** | 0.9 | 1/1 |
| arctic-embed-s (own val-tuned gate) | test | 2/12 = 0.167 | 0.688 | 0.846 | 0/1 |
| granite-small-r2 | val | no threshold gave 0 val false answers | — | — | — |

Restricted queries: 0/4 answered and no restricted passage ever appeared in candidates (also enforced by
integration tests). Test false answer: *"what is the rate limit of the orders API"* (the Invoices API
rate-limit section passes on semantic + lexical agreement). Typical abstentions on answerable queries are
paraphrases with low lexical overlap (e.g. *"how do I undo a bad release of the orders service"*: the
correct passage **is ranked #1** but is shown as a lower-relevance match, not as evidence).

Risk/coverage: the full val sweep is in `eval/results/BAAI_bge-small-en-v1.5.json` (`threshold_sweep_val`).

## Reranker as evidence signal (experiment, not shipped)

Same recorded candidates (top 10), gate = exact identifier OR cross-encoder score ≥ T (T chosen on val).

| reranker | test FAR | test coverage | test precision | added CPU latency (10 passages) p50 / p95 |
|---|---|---|---|---|
| none (shipped gate) | 1/12 | 0.562 | 0.9 | — |
| Xenova/ms-marco-MiniLM-L-6-v2 (Apache-2.0) | 2/12 | 0.812 | 0.867 | 106 / 147 ms |
| BAAI/bge-reranker-base (MIT) | 2/12 | 0.875 | 0.875 | 658 / 853 ms |

Rerankers answer many more answerable queries but do not reduce false answers on this set; the
difference in false answers is one query (q046, arguably ambiguous). Decision: defer; repeat on real data.

## Latency and load

In-process, warm, sequential (eval script, bge-small): total p50 10.4 ms / p95 13.3 ms; query embedding
p50 6.2 ms / p95 8.4 ms. Ingestion: 12 documents / 33 chunks in 0.64 s.

HTTP, server profile (API process + separate worker process + PostgreSQL container, client on the
same 4 vCPUs with one keep-alive connection per client thread, 200 requests per level,
`eval/bench_concurrency.py`):

| API processes | concurrency | throughput | p50 | p95 | p99 | errors |
|---|---|---|---|---|---|---|
| 1 | 1 | 60.2 req/s | 14.5 ms | 26.5 ms | 59.0 ms | 0 |
| 1 | 4 | 74.9 req/s | 48.6 ms | 77.8 ms | 110.9 ms | 0 |
| 1 | 8 | 64.8 req/s | 106.6 ms | 163.9 ms | 198.3 ms | 0 |
| 1 | 16 | 65.5 req/s | 188.4 ms | 261.0 ms | 286.3 ms | 0 |
| 3 (`serve --workers 3`) | 1 | 62.3 req/s | 14.8 ms | 21.4 ms | 34.3 ms | 0 |
| 3 | 16 | 64.2 req/s | 173.4 ms | 312.8 ms | 660.6 ms | 0 |

The machine is CPU-saturated at ~60–75 req/s (query embedding + the load generator share 4 vCPUs), so
extra API processes do not help here; on a dedicated server with a separate load generator, re-measure.
`KS_EMBED_THREADS=1` showed no gain. An earlier run that opened a new HTTP client per request measured
only ~21 req/s — that was a benchmark artifact (fixed), not server capacity.

## Not measured

Real-corpus quality; corpora beyond 33 chunks (exact KNN scaling, filtered ANN); ingestion throughput on
large PDFs; RAM per process under load; GPU anything; Docker image runtime.
