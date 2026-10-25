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

**Keyword searches (added 2026-10-23).** Most people type a few words ("parental leave policy", "rollback",
"per diem"), not a question. Such short queries have low cosine similarity to the right passage with every
model (0.54–0.71 measured), so the gate abstained on almost all of them although the top results were
right — the labelled set had contained almost only full questions. Added: 24 answerable keyword queries and
16 keyword queries that must be refused (`eval/queries.jsonl`, categories `keyword` / `keyword_negative`),
and a gate rule *every query term occurs in the passage (title, headings or text) and similarity ≥
`gate_all_terms_min`*. Fixture test split, now 28 answerable / 20 must-refuse (reranker used for ordering):

| Model, similarity gate chosen on val (high/min/coverage/all-terms) | False answers | Answered | Keyword answered | Question answered |
|---|---|---|---|---|
| **Qwen3-Embedding-0.6B-Q, 0.78/0.64/0.20/0.45** | **1/20** | **0.86** | 0.83 | 0.80 |
| Qwen3-Embedding-0.6B-Q, untuned defaults (what the UI used before) | 1/20 | 0.50 | — | — |
| bge-base, 0.78/0.72/0.20/0.60 | 3/20 | 0.68 | 0.83 | 0.30 |
| bge-base + MiniLM reranker-score gate | 3/20 | 0.82 | 0.83 | 0.70 |

The one Qwen3 false answer ("how do I roll back a database migration") retrieves the runbook section on
database migrations, which does discuss reversing them — arguably a labelling issue. Qwen3 query latency
p50 0.96 s with bge-reranker-base (contended) vs 0.17 s for bge-base + MiniLM. Small set: one query moves a
rate by 4–5 points; see §7 for a real 623-page corpus.

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

**Bake-off on technical Q&A (2026-10-23):** CQADupStack *unix* (StackExchange; 5,000-post subset, 200
seeded test queries), bge-base first stage, each reranker reorders the top 10 or 30 (`eval/beir_screen.py`,
`eval/results/beir-cqadupstack-unix-rerank-bakeoff.json`; latencies contended with an indexing job):

| Reranker | Licence | nDCG@10 top-10 / top-30 | Recall@10 top-10 / top-30 | Rerank p50 top-10 / top-30 |
|---|---|---|---|---|
| none (bge-base) | — | **0.597** | **0.737** | — |
| ms-marco-MiniLM-L-6-v2 (default) | Apache-2.0 | 0.560 / 0.525 | 0.737 / 0.665 | 1.3 s / 3.7 s |
| ms-marco-MiniLM-L-12-v2 | Apache-2.0 | 0.567 / 0.539 | 0.737 / 0.675 | 2.7 s / 7.7 s |
| jina-reranker-v1-turbo-en | Apache-2.0 | 0.572 / 0.543 | 0.737 / 0.690 | 1.8 s / 5.2 s |
| bge-reranker-base | MIT | 0.570 / 0.530 | 0.737 / 0.669 | 2.5 s / 7.7 s |
| answerai-colbert-small-v1 (20-query smoke) | Apache-2.0 | 0.613 vs 0.677 base / 0.593 | — | 2.9 s / 7.7 s |
| bge-reranker-v2-m3 (full precision) | Apache-2.0 | killed: ~14 GB RAM on this 16 GB machine | | |

**Every reranker lowered quality, and more candidates lowered it further**, at 1–8 s per query. Caveat: here
the "documents" are other users' questions and relevance means "duplicate question", which MS MARCO-trained
cross-encoders were not trained for; SciFact (above) showed small gains at top 10. The decisive test is the
real documentation corpus (§7). The full bge-reranker-v2-m3 does not fit a laptop CPU; only its int8 variant
remains a candidate.

## 5. Load (earlier run, bge-small, single API process, client on the same 4 vCPUs)

| Concurrency | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|
| 1 | 60 req/s | 14.5 ms | 26.5 ms | 59 ms | 0 |
| 16 | 65 req/s | 188 ms | 261 ms | 286 ms | 0 |

Not re-measured with bge-base (expect query embedding ~3–4× slower).

## 6. Link-aware search (graph-assisted retrieval)

`eval/graph_eval.py` generates a FICTIONAL interlinked platform wiki (`eval/make_wiki.py`: 36 service pages
with Dependencies / Ownership / Runtime / Failure sections, 8 workflow pages, front-matter `depends_on` /
`part_of`, `[[wiki links]]`; every page has the same section types, so they are hard negatives for each
other) and 81 labelled questions whose label is the **set** of passages needed. A restricted page is linked
to and from public pages to test leakage. bge-base, top 10 results, shipped gate.

| Arm | single-hop recall | dependencies ("services X depends on") | workflow ("every service in W") | impact ("services that depend on X") | false answers (15 about non-existent things) | p50 |
|---|---|---|---|---|---|---|
| hybrid (before) | 0.96 | 0.24 | 0.36 | 0.23 | 0 | 22 ms |
| + links, 3 slots | 0.96 | 0.48 | 0.49 | 0.32 | 0 | 39 ms |
| **+ links, 5 slots (default)** | **0.96** | **0.60** | **0.57** | **0.40** | 0 | 38 ms |
| + links, 7 slots | 0.83 | 0.69 | 0.79 | 0.46 | 0 | 37 ms |

Values = evidence recall (share of the labelled passages in the top 10), all 81 questions; slots chosen on
val (largest without single-hop loss), held-out test: dependencies 0.26 → 0.70, workflow 0.41 → 0.68, impact
0.23 → 0.36–0.38, single-hop 0.92 → 0.92. Restricted passages returned: 0 in every arm. The reranker does
not change recall@10 (it reorders the same 10) but lowers hit@1 on dependency questions (0.42 → 0.29) while
raising it on single-hop (0.54 → 0.75). Fixture eval (§3–4) is identical with link expansion on and off.

How it works (`search._linked`): seeds = documents whose title/file name the question contains (else the
top result); linked passages are scored against the question **with the seed names removed** (+3–5 points);
one passage per linked document first; they fill up to 5 of the 10 result slots. Tried and rejected: adding
linked passages as a weighted RRF branch (single-hop recall fell to 0.38–0.79 because similar sections of
neighbours were counted twice); preferring outgoing links (+0.01 dependencies, −0.08 impact).

**Pages without declared links (mentions).** Crawled intranet pages and exported documents rarely carry
`[[links]]` or `depends_on`. `eval/graph_eval.py --plain` strips every declared link (wiki links become
plain text, relation keys are removed from front matter), so pages are connected only because a passage
names another page's title ("… called the Inventory Service to check stock"). `graph.mentions` turns that into
an edge at query time (stemmed phrase match of titles with ≥ 2 words, keyword index, readable documents only):

| Wiki variant, arm (5 slots, no reranker, all 81 questions) | single-hop | dependencies | workflow | impact | false answers | leaks |
|---|---|---|---|---|---|---|
| declared links, hybrid only | 0.96 | 0.24 | 0.36 | 0.23 | 0 | 0 |
| declared links, links | 0.96 | 0.60 | 0.57 | 0.40 | 0 | 0 |
| declared links, links + mentions | 0.96 | 0.60 | 0.57 | 0.40 | 0 | 0 |
| **no declared links**, hybrid only | 0.96 | 0.25 | 0.36 | 0.25 | 0 | 0 |
| **no declared links**, mentions | 0.96 | **0.64** | **0.57** | **0.40** | 0 | 0 |

Mentions recover all of the gain of declared links on this wiki and add nothing (no harm) where links exist.
Caveat: here every page names its neighbours by their exact titles; real pages also say "the inventory API"
or `inv-svc`, which a title match misses (next: aliases / identifiers, research-graph.md).

**Caveats.** Synthetic, templated pages (similar to generated wikis, but not a real corpus). Getting *all*
the needed passages into the top 10 is still rare for questions needing 4–6 passages (complete-set rate
0.29 for dependencies, 0 for workflows) — following links step by step (UI Connections, `/links`, a future
agent) remains the tool for those. The evidence gate abstains on most correct short-section answers here
(similarity 0.6–0.7 < 0.72): thresholds tuned on longer prose do not transfer to terse wiki sections —
re-tune on the real corpus.

## 7. Real technical corpus: 623 Kubernetes documentation pages (`eval/corpus_eval.py`)

`eval/make_k8s_corpus.py` converts the Kubernetes docs (concepts, tasks, tutorials, setup, glossary, parts of
reference; CC BY 4.0, pinned commit) into 623 Markdown pages (~8,000 passages) with their own cross-links.
`eval/k8s_queries.jsonl`: 56 answerable queries (24 keyword, 21 questions, 11 identifiers such as
`CrashLoopBackOff`, `concurrencyPolicy`) labelled with the pages that answer them, and 16 that must be
refused — 8 off-topic, 8 near misses that sound like Kubernetes but are not covered ("Docker Swarm overlay
network", "ArgoCD sync waves", "Kubernetes 2.0 release date"). A per-query table
(`eval/results/corpus-*.txt`) shows every answer/abstention with its scores.

bge-base + MiniLM reranker (top 10), shipped gate, parser parse-v3 (3 pages failed on front matter):

| Metric | Value |
|---|---|
| a labelled page in the top 5 / at rank 1 / MRR@10 | **56/56** / 0.86 / 0.92 |
| must-refuse queries answered (false answers) | **1/16** — "Kubernetes 2.0 release date" |
| answerable queries answered | 0.875 (keyword 1.00, identifier 0.67, question 0.71) |
| top supported result is a labelled page | 0.86 (lower bound: labels list the main pages; e.g. *hello-minikube* for "expose a deployment outside the cluster" does show `kubectl expose`) |
| query latency p50 / p95 | 1.2 s / 1.7 s (contended with a reranker benchmark) |

Problems this corpus exposed, fixed in parse-v4 (re-measured below):
1. The false answer came from treating "2.0" as an exact identifier (an exact identifier match counts as
   evidence by itself). Bare `x.y` numbers are no longer identifiers; `v3.8.2` and `1.29.3` still are.
2. Hugo/Pandoc custom heading anchors (`## Title {#id}`) leaked into heading paths; now stripped and used
   as the anchor.
3. lowerCamelCase keys (`concurrencyPolicy`) got no keyword credit: PostgreSQL reads
   `.spec.concurrencyPolicy` as one token. They are now identifiers, also inside dotted paths.
4. Hand-written front matter that is not strict YAML (`title: Example: Deploying X`) rejected the whole
   page; it is now read as `key: value` lines.

**parse-v4, final labels** (six queries gained a second page that also answers them, found by auditing the
per-query tables; 8,524 passages, all 623 pages indexed). Same shipped gate for every row:

| Reranker (top 10) | rank 1 | MRR@10 | top 5 | false answers | answered (keyword / identifier / question) | precision | p50 / p95 |
|---|---|---|---|---|---|---|---|
| none | 0.946 | 0.973 | 56/56 | **0/16** | 0.893 (1.00 / 0.83 / 0.71) | 0.98 | **0.24 s** / 0.38 s |
| ms-marco-MiniLM-L-6 | 0.946 | 0.973 | 56/56 | 0/16 | 0.893 | 0.98 | 1.2 s / 1.7 s |
| jina-reranker-v1-turbo | **0.964** | 0.979 | 56/56 | 0/16 | 0.893 | 0.98 | 1.3 s / 1.8 s |
| bge-reranker-base | **0.964** | 0.978 | 56/56 | 0/16 | 0.893 | 0.98 | 4.3 s / 5.8 s |

Reading: on real technical docs the hybrid retrieval already finds a right page in the top 5 for every
query; a reranker moves one more query to rank 1 (1 of 56 — within noise) and costs 1–4 s. All four refuse
every off-topic and near-miss query. The remaining misses are *abstentions*, not wrong answers: natural
questions whose wording differs from the page ("how do I run a job every night at 2am" → CronJob page;
"where should I store a database password" → Secrets), plus `OOMKilled` (passage similarity 0.59). Gates
re-chosen on the val half (n=28 each split, so indicative only): similarity 0.78 / 0.55 / coverage 0.67 →
test answered 0.93, false answers 0, precision 1.0 — slightly looser than shipped, not adopted on this n.
One wrong answer: "taints and tolerations" answers from *device-taints* (it does cover taints) ahead of the
main *taint-and-toleration* page, which is ranked 2.

**Contextual passages** (research item 3: the document's description, else its first paragraph, up to 300
characters, added to every passage's *embedding* text; `--context-chars 300`; bge-base, parse-v4):

| Corpus | Setting | rank 1 | MRR@10 | passage at rank 1 | false answers | answered |
|---|---|---|---|---|---|---|
| Kubernetes (56 / 16) | off → on, no reranker | 0.946 → **0.982** | 0.973 → 0.991 | — | 0/16 → 0/16 | 0.893 → 0.893 |
| Kubernetes | off → on, MiniLM | 0.946 → **1.000** | 0.973 → 1.000 | — | 0/16 → 0/16 | 0.893 → 0.893 |
| Fixtures (56 / 20, test half for the gate) | off → on, no reranker | 1.000 → 0.982 | 1.000 → 0.991 | 0.982 → 0.946 | 3/20 → 3/20 | 0.679 → 0.571 |

Not adopted as the default yet: it helps the real corpus (whose pages carry curated one-line descriptions)
and slightly hurts the small fixture corpus, where one short summary dominates every passage of a short
document and blurs which passage answers; the gate thresholds were tuned without it (answered share fell on
fixtures).

**Gate tuned on both label sets pooled** (`eval/pooled_gates.py`; 84 val / 84 test queries; rows files in
`eval/results/*.rows.json` so this re-runs without models). For bge-base the sweep on the pooled val half
picks **exactly the shipped gate** (0.78 / 0.72 / coverage 0.20 / all-terms 0.60):

| Passages | pooled test: false answers | answered | precision | fixtures test (false / answered) | Kubernetes test |
|---|---|---|---|---|---|
| plain (shipped) | 3/28 (0.107) | **0.768** | **0.935** | 3/20 / 0.679 | 0/8 / 0.857 |
| + document summary | 3/28 (0.107) | 0.732 | 0.909 | 3/20 / 0.571 | 0/8 / 0.893 |

So the shipped gate stands, and contextual passages stay off: with the best gate for them they answer less
and less precisely overall, despite the better ranking on Kubernetes. The 3 false answers are fixture
near-topic questions whose subject exists but whose specific fact does not ("rate limit of the orders API",
"roll back a database migration", "orders api rate limits") — the hard case for any similarity gate; a
reader model that checks the passage states the fact is the planned fix (second-brain step).

**Embedding model × reranker on the same corpus** (623 pages without author descriptions, 8.5k passages,
56 answerable / 16 must-refuse queries, each model's shipped gate; 2026-10-24):

| Embedding + reranker | Right page first | Right page in top 5 | False answers | Answered | Precision | Search p50 |
|---|---|---|---|---|---|---|
| **bge-base, no reranker** | **0.964** | **1.000** | 0/16 | **0.911** | **0.961** | **0.27 s** |
| bge-base + MiniLM (default until 2026-10-24) | 0.946 | 1.000 | 0/16 | 0.911 | 0.961 | 1.26 s |
| **bge-base + jina-reranker-v1-turbo (default now)** | **0.964** | **1.000** | 0/16 | **0.911** | **0.961** | 0.55 s |
| bge-base + bge-reranker-base | 0.946 | 1.000 | 0/16 | 0.911 | 0.941 | 1.76 s |
| Qwen3-Embedding-0.6B, no reranker | 0.893 | 0.982 | 0/16 | 0.875 | 0.939 | 1.01 s |
| Qwen3-Embedding-0.6B + bge-reranker-base | 0.929 | 0.982 | 0/16 | 0.875 | 0.959 | 2.65 s |

Qwen3 is worse here on every measure and 4–10× slower; it was ahead only on the 12-document fixtures (§3).
Indexing with Qwen3 took ~2 h for 623 pages on 4 cores (bge-base: minutes). Rerankers move at most one
query on this corpus. Default reranker changed to jina-turbo: equal or better than MiniLM on all three corpora
where both ran (Kubernetes with and without descriptions, CQADupStack); not measured on SciFact.

## 8. Search tricks (owner's "7 tricks" and more, 2026-10-24, `eval/tricks.py`)

Bench: 171 Kubernetes pages with Gemma 4 E2B summaries; 219 queries = 176 real Stack Overflow questions
("so", the passage that answers them labelled) + 27 answerable sub-corpus questions + 16 that must be refused.
bge-base. One query is 0.006 of hit@1 on "so", so differences under ~0.02 are noise. Result files:
`eval/results/tricks-*.json`.

| Variant (no reranker / with jina-turbo) | so hit@1 | so success@5 | so MRR@10 | so answered | sub hit@1 | false answers |
|---|---|---|---|---|---|---|
| **Shipped: 900-char passages, 150 overlap, RRF k 60** | 0.364 / 0.369 | 0.625 / 0.625 | 0.482 / 0.475 | 0.693 | 1.00 / 1.00 | 0/16 |
| Overlap 0 | 0.324 / 0.358 | 0.619 / 0.597 | 0.459 / 0.468 | 0.699 | 1.00 | 0/16 |
| Passages 600 / 1200 characters | 0.324 / 0.347 (no rr) | 0.608 / 0.648 | 0.452 / 0.477 | 0.688 / 0.682 | 1.00 | 0/16 |
| Summary in the keyword index (lowest weight) | 0.364 / 0.364 | 0.625 / 0.625 | 0.484 / 0.473 | 0.699 | 1.00 | 0/16 |
| Summary as context line, 150 / 300 / 600 chars | 0.347–0.330 / 0.375 | 0.608–0.614 / 0.597–0.619 | ≤0.481 | **0.648–0.653** | 0.96–1.00 | 0/16 |
| Summary vector as a fusion branch (weight 0.5 / 1.0) | 0.273 / 0.250 | 0.557 / 0.540 | 0.395 / 0.376 | 0.653 / 0.648 | 0.93 | 0/16 |
| Index-time questions (HyPE) as vectors (0.5 / 1.0) | 0.324 / 0.278 | 0.597 / 0.574 | 0.451 / 0.418 | 0.648 / 0.642 | 0.89 | 0/16 |
| Rerank with ±1 neighbour passage (300 / 900 chars) | 0.358 / 0.364 (rr) | 0.625 / 0.614 | 0.472 | 0.693 | 1.00 / 0.96 | 0/16 |
| Convex fusion 0.5 / 0.7 / 0.9 instead of RRF | 0.312–0.358 | 0.58–0.631 | ≤0.469 | 0.688–0.699 | 0.85–1.00 | 0/16 |
| RRF k 20 / 120 | 0.358 / 0.358 | 0.614 / 0.636 | 0.480 / 0.482 | 0.693 / 0.699 | 1.00 | 0/16 |
| HyDE only when search would abstain | 0.369 / 0.381 | 0.631 | 0.492 / 0.485 | 0.693 | 1.00 | 0/16 |

**Result: nothing beat the shipped setup by more than noise.** HyDE-as-rescue is +1–2 queries at +5–10 s for
those queries (within noise; not shipped). Every summary or question vector made results worse, and the
summary context line made the gate answer fewer real questions. **Not measured:** "neighbour summaries
instead of overlap" (an LLM summary of the passages either side in place of the 150-character overlap): its
run was lost when the container restarted, and the closest measured variants (overlap 0, neighbour passages
at rerank, summary context) gave nothing, so it was not re-run. Caveat: one corpus (public technical docs);
the owner's own documents may differ.

## 9. Reading pictures: GLM-4.6V-Flash 9B (2026-10-24, `eval/image_ocr.py`)

20 generated pictures with known text (`eval/image_set.py`: documentation pages, app screens, tables, an org
chart, two flowcharts, slides, half-size screenshots, a scanned page; public or made-up content). GLM-4.6V-Flash
Q4_K_M with its Q8_0 image projector, llama.cpp, thinking off, 4-core CPU. Two container restarts cut the run:
**19 of 20 pictures measured** (the scanned page is missing). Results: `eval/results/images-glm-4.6v-flash-part1.txt`
and `-part2.txt`.

| Kind | Pictures | Word recall | Word precision | Arrows (recall / precision) |
|---|---|---|---|---|
| Documentation pages | 6 | 1.00 | 0.99–1.00 | |
| App screens | 4 | 1.00 | 1.00 | |
| Tables (calendar, banner) | 2 | 1.00 | 1.00 | |
| Org chart, 2 flowcharts | 3 | 1.00 | 1.00 | **1.00 / 1.00** on all three |
| Slides | 2 | 0.88, 1.00 | 1.00 | |
| Half-size screenshots | 2 | 0.99, 0.93 | 1.00, 0.93 | |

**Time: median 126 s per picture, 52–961 s** (the two tables took 960 s each). Character error is high on the
slides (0.10–0.41) only because the reading order differs; the words are right.

**Compared with Gemma 4 E2B:** not on the same set yet (its run was lost to the restarts). The earlier,
smaller check of E2B (HANDOFF §4k) read text accurately at 20–56 s per picture but got about half of a
diagram's arrows wrong. So GLM is the one that reads diagrams correctly, but several times slower (median 126 s against 20–56 s). For
background indexing of pictures, where time matters less than accuracy, GLM is the stronger candidate;
**to confirm:** run E2B on the same 20 pictures, and GLM on the scanned page.

## 10. Several embedding sessions for one document (2026-10-24, `eval/bench_parallel.py`)

Idle 4-core VM (Intel Xeon 2.1 GHz), bge-base, 400 passages from 8 large Kubernetes pages (median 774
characters). Passages per second, by sessions × threads: **1 × all cores 6.9**, 2 × all cores 5.4,
**2 × 2 threads 7.3**, 4 × 1 thread 7.2. Vectors identical in all layouts. No worthwhile gain on 4 cores; an
earlier 1.7× reading was taken on a busy CPU. Not measured: machines with more cores, GPU.

Owner's laptop (2026-10-25): Intel Core i5-12500H, 12 cores / 16 threads, Windows 11, bge-base, 400 passages from the docs folder: **1 × all threads 6.5**, 2 × all 4.0, 2 × 4 threads 5.9, **4 × 4 threads 6.9**, 4 × 2 5.9, 8 × 2 6.5 passages/s; vectors identical. Again no worthwhile gain (+6% at best), so step 1 stays unbuilt; a 16–32-core server is still unmeasured (TODO 3).

## 11. Embeddings on the integrated GPU (2026-10-25, `scripts/gpu_probe.py`)

Owner's laptop, 2026-10-25: Intel Iris Xe (driver 31.0.101.4575), i5-12500H, Windows 11; bge-base, 400 passages cut by the app (`data/app-passages.json`, median 740 characters), sorted by length as the app batches them; same passages in both runs. CPU: the app's own environment (onnxruntime 1.31). GPU: `onnxruntime-directml` 1.24.4 in its own environment (`scripts/gpu_probe.py`). Passages/s at batch 8 / 32 / 64 / 128: **CPU 5.2 / 4.5 / 4.0 / 3.6, GPU (DirectML) 1.0 / 2.2 / 2.7 / out of GPU memory** (DirectML "Not enough memory resources" at batch 128). Larger batches help the GPU, but its best (2.7) is about half the CPU's best (5.2, at the app's batch 8). Parity, from an earlier run on paragraph input: cosine between CPU and GPU vectors min 0.999987, mean 0.999992, which passes the bar. So the embedding switch is not worth building for this laptop. (An earlier version of this note said CPU 3.4 vs GPU 1.2; that run used unsorted paragraphs from the docs folder, so padding slowed both sides.) Not measured: OpenVINO and the reranker.

**Summaries on the GPU (plan-gpu step 3), same laptop, 2026-10-25.** llama.cpp b11836, Gemma 4 E2B Q4_K_M,
the app's server flags (`-c 8192 -np 1 -t 8 --poll 0 --jinja`), CPU build against the Vulkan build with
`-ngl 99`, one after the other on the same pages (each: `eval/llm_labels.py`, then `eval/llm_summaries.py --app-prompt --parallel 1 --limit 60`; results in
`eval/results/*-gemma-4-e2b-laptop-{cpu,vulkan}.json`, `summary-quality-laptop-cpu-vs-vulkan.json`):

| | CPU build | Vulkan build (Iris Xe) |
|---|---|---|
| Document types, 42 Kubernetes pages (`eval/llm_labels.py`): exact | 0.643 | 0.619 (same type on 40 of 42 pages) |
| Same run, seconds per page, median | 21.0 | 13.5 |
| Summaries, 60 pages without descriptions (`eval/llm_summaries.py --app-prompt`): median per request / wall per page | 11.9 s / 14.1 s | 10.8 s / 11.5 s |
| Summary quality (`eval/summary_quality.py`): agreement / identifies the page | 0.786 / 0.733 | 0.786 / 0.750 |

The GPU is 10–20% faster on the summaries run, and quality is the same within noise (one page apart on types
and on "identifies"). The 21.0 s CPU figure on types was slower than the CPU's summaries run, so other work
on the laptop may have slowed it; the summaries run is the cleaner comparison. Part of the Vulkan summaries
run overlapped a test run on the CPU, which can only have slowed the GPU side. Built as the Settings switch
**Run it on: CPU / GPU** (default CPU). Embeddings and the reranker stay on the CPU (owner decision,
2026-10-25: the CPU is faster for them on this laptop; revisit with a dedicated high-memory GPU).

## Not measured

Real company corpus; corpora beyond ~8.5k chunks; large embedding models (bge-large); GPU; Windows performance; Docker
image; multilingual content.

## Reproduce

```bash
uv run --group eval python eval/run_eval.py --model BAAI/bge-base-en-v1.5 --lexical-weight 0.1
uv run --group eval python eval/beir_screen.py --max-docs 2000 --models BAAI/bge-small-en-v1.5 BAAI/bge-base-en-v1.5
uv run --group eval python eval/scale_eval.py --model BAAI/bge-base-en-v1.5 --reranker "" --lexical-weight 0.1
uv run --group eval python eval/scale_eval.py --model BAAI/bge-base-en-v1.5 --reuse --reranker BAAI/bge-reranker-base --rerank-candidates 10
uv run --group eval python eval/graph_eval.py --slots 3,5,7          # link-aware search
```
