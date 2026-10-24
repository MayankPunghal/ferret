# Research note: making search more accurate, robust and scalable

Scope: techniques that run **offline on our own hardware**, keep **permissions and deletions exact**, and
improve **accuracy first** (an extra second per query is acceptable). Literature claims below are the
sources' own numbers; nothing is adopted until it wins on our evaluations (fixtures, SciFact subset,
interlinked wiki, abstention sets) — the same rule that kept the MiniLM top-30 reranker and
equal-weight fusion out.

Available offline today (fastembed 0.9, ONNX, no new dependency): rerankers `BAAI/bge-reranker-v2-m3`
(MIT, 8k tokens), `Xenova/ms-marco-MiniLM-L-12-v2`; late interaction `answerdotai/answerai-colbert-small-v1`,
`colbert-ir/colbertv2.0`; learned sparse `prithivida/Splade_PP_en_v1`,
`opensearch-project/opensearch-neural-sparse-encoding-doc-v3-gte`; dense `BAAI/bge-large-en-v1.5`,
`mixedbread-ai/mxbai-embed-large-v1`, `snowflake/snowflake-arctic-embed-l`, `google/embeddinggemma-300m`
(Gemma licence), `Qwen/Qwen3-Embedding-0.6B` (Apache-2.0). Check each licence before shipping.

## Shortlist, in recommended order

| # | Technique | What it fixes | Evidence (source) | Cost | Plan |
|---|---|---|---|---|---|
| 1 | **Stronger reranker over more candidates** (bge-reranker-v2-m3; ColBERT as a cheaper alternative over 100–500 candidates) | ordering errors the embedding makes; weak MiniLM on technical text | cross-encoders and late interaction approach full BERT re-ranking quality [ColBERTv2; Qdrant FastEmbed docs]; larger rerankers lead BEIR tables [2026 reranker comparisons] | +0.5–3 s/query on CPU (to measure) | bake-off on all eval sets: model × candidates (10/30/50); adopt the most accurate within ~2 s |
| 2 | **Larger / newer embedding model** (bge-large, mxbai-large, arctic-l, Qwen3-Embedding-0.6B, EmbeddingGemma) | first-stage recall (what the reranker never sees, it cannot fix) | Qwen3-Embedding-0.6B ≈ 70.5 MTEB(eng v2); EmbeddingGemma ≈ 69.7 [Qwen3 report; model cards via 2026 surveys]; bge-base (ours) is older | indexing 3–10× slower on CPU (GPU fixes); vectors 768–1024 dims | screen on SciFact subset + fixtures + wiki; tune gate thresholds per model (registry) |
| 3 | **Contextual passages** — prepend a short "where this passage sits" text to each passage before embedding *and* keyword indexing | passages that are ambiguous out of context ("it", "the service", "this table") | Anthropic: −35 % retrieval failures (embeddings), −49 % with contextual BM25, −67 % with reranking [Contextual Retrieval] | the LLM version needs a local LLM at indexing (fits the planned second-brain stage); a no-LLM version — title, heading path (done), front-matter summary, first paragraph of the document — costs nothing | measure the no-LLM extension now; LLM version with the local-LLM work |
| 4 | **Learned sparse retrieval** (SPLADE++ / OpenSearch neural sparse) instead of or beside PostgreSQL full-text | keyword search misses synonyms; PG FTS ranking is weak and may be slow at millions of passages | SPLADE beats BM25, especially out of domain [Lucene dense+sparse paper; AgentIR]; ~17–150 ms per query, ~160 ms per passage on CPU [AgentIR] | indexing cost per passage; storage as pgvector `sparsevec` | compare against PG FTS on all evals and at 1 M+ passages (latency) |
| 5 | **Small-to-big context** — match small passages, rerank/show them with their surrounding section | answers split across passage boundaries; reranker sees too little context | standard pattern (sentence-window / parent-document retrieval) [Chronicles of RAG; practitioner guides]; no benchmark consensus on sizes | cheap (we already store neighbours) | rerank with ± one neighbour passage; measure |
| 6 | **Entity edges + personalised PageRank** — rare identifiers (`orders-svc`, `orders_db`, `ERR-PAY-4012`) become graph nodes linking every passage that mentions them; spread relevance from the question's seeds over links and entities | multi-hop questions across pages that do not link to each other; "complete set" recall (still low after link expansion) | HippoRAG: single-step PPR multi-hop retrieval, +3/+20 points on MuSiQue/2Wiki, 10–30× cheaper than iterative retrieval [HippoRAG] — there with LLM-extracted entities; ours would be deterministic | one SQL/numpy pass per query; no LLM | extend `eval/graph_eval.py`; adopt only if it beats link expansion without single-hop loss |
| 7 | **Automatic clusters** (community detection on links + entity edges + "similar page" edges) | users must otherwise group documents by hand; cluster view for navigation | GraphRAG uses hierarchical Leiden; its FastGraphRAG mode extracts with NLP instead of an LLM [Microsoft GraphRAG docs] | networkx Louvain (BSD) — note `leidenalg` is GPL | after #6, for the graph view and "related documents" |
| 8 | **Scale-out vector search with exact re-scoring** — pgvector 0.8 HNSW with iterative scans (keeps filtered/permission queries complete), `halfvec` (½ memory), binary quantisation for candidates, then exact re-scoring | memory/latency beyond a few million passages without losing accuracy | pgvector 0.8 iterative scans; halfvec ≈ 50 % storage with minimal recall loss; binary candidates need ~80× oversampling then rescoring [pgvector guides] | server RAM; index build hours at 10 M | scale test 1–10 M passages: recall@k vs exact, p95 latency, delete/permission correctness; Qdrant as the comparison arm |
| 9 | **Better PDF parsing and OCR** (Docling: layout, tables, OCR only on image pages) | tables and multi-column PDFs lose structure; scanned PDFs are rejected today | ~1.6 pages/s on a Xeon CPU in the Docling report [Docling technical report] — an archive of 1.6 M pages ≈ 11 CPU-days | heavy dependency (PyTorch); slow | optional parser for problem PDFs / scans, chosen per document; keep pypdf as the fast default |

Deferred / rejected for now:
- **Late chunking** (embed the whole document, pool per passage): needs long-context embedders; the
  reference models are CC BY-NC (jina v3); gains grow with document length [Late Chunking paper]. Revisit
  with an Apache-licensed long-context model.
- **Query rewriting / HyDE / multi-query**: need an LLM → with the local-LLM "second brain" stage.
- **LLM-extracted knowledge graphs (GraphRAG, LightRAG)**: slow on CPU, hard to keep permission-safe and
  fresh; deterministic links + entities first (research-graph.md).

## Reliability, not only ranking

Our measured weak spot is **abstention**: similarity (and rerankers) judge *relevance*, not whether the
passage *answers* the question (docs/evaluation.md §3–4, §6). Items 1–3 raise the chance the right passage
is retrieved; deciding that the answer is *absent* needs either calibration on real questions or an
answer-verification step (local LLM / entailment model checking each cited passage) in the answer stage.

## Sources

- Anthropic, *Introducing Contextual Retrieval* — https://www.anthropic.com/news/contextual-retrieval
- Günther et al., *Late Chunking* (arXiv 2409.04701) — https://arxiv.org/abs/2409.04701
- Qwen3 Embedding report (arXiv 2506.05176) — https://arxiv.org/pdf/2506.05176
- 2026 embedding surveys: https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models ,
  https://www.premai.io/blog/best-embedding-models-for-rag-2026-ranked-by-mteb-score-cost-and-self-hosting/
- 2026 reranker overviews: https://redis.io/blog/top-reranking-models-rag-accuracy.md ,
  https://presenc.ai/research/best-open-weight-reranker-models-2026
- ColBERTv2 (arXiv 2112.01488) — https://arxiv.org/abs/2112.01488 ; Qdrant FastEmbed ColBERT guide —
  https://qdrant.tech/documentation/fastembed/fastembed-colbert/
- Dense + sparse retrieval in Lucene (arXiv 2311.18503) — https://arxiv.org/pdf/2311.18503 ; AgentIR
  (arXiv 2605.25092) — https://arxiv.org/pdf/2605.25092
- HippoRAG (arXiv 2405.14831) — https://arxiv.org/abs/2405.14831
- Microsoft GraphRAG indexing pipeline — https://www.mintlify.com/microsoft/graphrag/concepts/indexing-pipeline
- pgvector in production (iterative scans, halfvec, quantisation) — https://bigdataboutique.com/blog/pgvector-in-production
- Docling technical report (arXiv 2408.09869) — https://arxiv.org/abs/2408.09869
- The Chronicles of RAG (arXiv 2401.07883) — https://arxiv.org/pdf/2401.07883

## Round 2 (2026-10-24): chunk context, summaries as vectors, overlap, query/index-time LLM tricks

Owner questions: summarise each passage as well as the document? chunk by headings? replace overlap with
neighbour summaries? a separate summary vector — how does it connect, how long may it be?

**What we do now** (for comparison): passages never cross a section; inside a section they are cut on
paragraph, then sentence boundaries at ~900 characters, with 150 characters of overlap; every passage is
embedded under "title — heading path" (the keyword index weights title A, heading B, text C). That is the
best-performing configuration in the most careful study we found:

- *Does Document Structure Help Dense Retrieval?* (placebo-controlled, bge-small/large, Wikipedia-FA and
  QASPER) [arXiv 2610.10170]: cutting at section boundaries was the largest gain (+0.041 / +0.019
  cov-nDCG@10 over fixed windows); real heading paths added +0.011 / +0.009 (shuffled paths did not);
  **an LLM-written context line per chunk (Anthropic-style, <=80 tokens) scored below plain section cuts**;
  **two-stage routing (pick sections first, then chunks) lost to flat search** (-0.033 / -0.015) because
  the first stage misses the right section too often (section recall 0.17-0.68). Anthropic reports large
  gains for per-chunk context with Claude on its own sets [Contextual Retrieval]; the controlled study does
  not reproduce them over structural chunking. Cost here: ~39 s per call with Gemma 4 E2B on CPU, so a
  million passages would take ~450 CPU-days.
- 36 chunkers x 6 domains x 5 embedders [arXiv 2603.06976]: paragraph-group chunking best (nDCG@5 0.459),
  fixed character windows worst (0.244); structure-preserving beats fixed size. Overlap's own effect was
  not isolated; we found no controlled evidence that overlap helps, nor any for "neighbour summaries".
- Summary-as-the-only-vector (one vector per document) is fast but less effective [arXiv 2608.16586];
  one journal paper reports gains on BEIR medical sets [Summary RAG, rstjournal 2026] (unverified).
- Long or multi-topic text in one vector blurs it; topic spread predicts the loss better than length
  [arXiv 2603.21437; DICE]. bge-base reads at most 512 tokens: anything longer is cut.
- Index-time questions per chunk (HyPE: an LLM writes the questions a chunk answers, each embedded and
  linked to the chunk): up to +42 points context precision, +45 claim recall on six sets [HyPE,
  IEEE Access 2025; arXiv 2607.29402] — authors' numbers. It targets our measured miss type: questions
  worded unlike the page ("run a job every night at 2am" -> CronJob).
- Query-time HyDE / multi-query: helps vague questions, **hurts precise and identifier questions**
  (Recall@10 0.803 -> 0.744 in one domain; invented numbers in finance) [arXiv 2609.28579, 2604.01733].
- Fusion: a tuned convex combination beats RRF in and out of domain and needs few labels; RRF is the safe
  default; fusion can fall below the better single retriever [Bruch et al., TOIS; Wang et al. PVLDB 2025].
- Small-to-big (match small passages, rerank with their parent/neighbours): gains reported on authors' own
  sets [HRR, arXiv 2503.02401]; no controlled consensus on sizes.
- Late chunking needs a long-context embedder with token outputs; open reference models are Jina v2
  (8k, Apache-2.0) or BGE-M3 (MIT); contextualised-chunk models such as voyage-context are API-only (not
  offline: excluded).

**Plan (measure each on fixtures + Kubernetes + abstention sets; keep only what wins):**

| # | Experiment | Why | Cost |
|---|---|---|---|
| 1 | **Document-summary vector as a fusion branch** (one vector per document, row carries document_id and collection_id; a hit ranks that document's passages through weighted RRF; never routes or filters) | the Kubernetes gain of the summary line without repeating it on every passage | 1 vector per document; no LLM beyond the summary we already write |
| 2 | **Index-time questions (HyPE)** per section, from the background model: 3-5 questions, each a vector linked to its passage(s) | question-vs-page wording gap, our main miss type | one LLM call per section (cheaper than per passage); background |
| 3 | Overlap 0 vs 150; passage size 600/900/1200 | overlap's value is unproven | re-index only |
| 4 | Summary and topics in the keyword index at the lowest weight (D) | "contextual BM25" was Anthropic's larger gain | re-index only |
| 5 | Rerank with +-1 neighbour passage (small-to-big) | answers split across passages | +reranker time |
| 6 | Convex score fusion vs weighted RRF | literature favours tuned convex | none |
| 7 | HyDE only as a rescue when search would abstain | recall for vague questions without hurting identifiers | +5-10 s for those queries only |

Not planned: per-passage LLM context lines (controlled evidence negative, cost prohibitive at archive
scale), section-first routing (controlled evidence negative), API-only contextual embedders (not offline).

Sources (round 2): https://arxiv.org/abs/2610.10170 · https://arxiv.org/abs/2603.06976 ·
https://arxiv.org/abs/2608.16586 · https://www.rstjournal.com/article/21.2026 · https://arxiv.org/abs/2603.21437 ·
https://arxiv.org/abs/2607.29402 · https://arxiv.org/abs/2609.28579 · https://arxiv.org/abs/2604.01733 ·
https://www.pinecone.io/research/an-analysis-of-fusion-functions-for-hybrid-retrieval/ · https://arxiv.org/abs/2503.02401 ·
https://arxiv.org/abs/2409.04701 · https://github.com/D-Star-AI/dsRAG · https://arxiv.org/abs/2504.19754
