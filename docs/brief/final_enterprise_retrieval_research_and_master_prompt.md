# Enterprise Local-First Retrieval / RAG Platform
## Research brief, open-source reference map, architecture recommendations, and Claude Opus 5.5 master prompt

**Research snapshot:** 22 October 2026. Links below point to primary repositories, model cards, paper pages, or official documentation whenever available. Project activity, versions, model rankings, and licenses change; re-check them before adopting anything.

## Executive recommendation

Do not begin by choosing a GraphRAG framework. Build a reliable **enterprise search and evidence-retrieval platform** first, then add optional graph retrieval and optional local answer generation. Graphs are valuable when the query depends on relationships, entity resolution, temporal facts, or multi-hop reasoning; they are not automatically an improvement for every query.

A strong default research hypothesis—not a final technology decision—is:

- **Frontend:** React + TypeScript, with an intentional information-dense enterprise search UI.
- **Control plane and business API:** ASP.NET Core on a currently supported .NET release, matching the team's existing strength and keeping authentication, RBAC, administration, auditing, and business workflows in a mature application stack.
- **ML/data plane:** a small Python service for embedding, reranking, document parsing, and optional graph/LLM processing, because the strongest open ML tooling is in Python. Keep stable HTTP/gRPC/OpenAPI contracts between the .NET and Python boundaries.
- **Metadata, users, document versions, permissions, jobs, provenance, and audit:** PostgreSQL.
- **Search engine:** evaluate **Qdrant**, **OpenSearch**, and **PostgreSQL + pgvector/full-text search** on the target corpus and workload. Also examine **Vespa** for advanced relevance and scale, **Milvus** for very large vector workloads, and **FAISS/USearch** for embedded/local index use cases. Do not deploy multiple search stores without a measured reason.
- **Graph:** begin with a graph abstraction and source-linked relationship schema. Compare PostgreSQL adjacency/relationship tables with **Neo4j** or **FalkorDB** only if graph-specific evaluation improves quality enough to justify another datastore.
- **Document parsing:** compare **Docling**, **MinerU**, **Marker**, **Apache Tika**, **Microsoft MarkItDown**, and **Unstructured** against the company's real files. Parsing is a first-class quality component, not a commodity afterthought.
- **Embedding and ranking inference:** benchmark current models through supported local runtimes such as **Hugging Face Text Embeddings Inference (TEI)**, **Infinity**, **Sentence Transformers**, **FastEmbed/ONNX where compatible**, and **llama.cpp** for suitable quantized models and local generation. Verify model/runtime compatibility instead of assuming every model is supported by every runtime.
- **Optional generation:** local-only model adapter, disabled by default and not required by retrieval-only mode. No silent cloud fallback.

The best production retrieval pipeline is usually a **measured, adaptive combination** of semantic retrieval, lexical retrieval, fusion, reranking, structured filters, contextual expansion, and (only for suitable queries) graph or multi-hop retrieval. Avoid a fixed, expensive pipeline for every query.

**Build broadly researched, narrowly implemented.** Research many options; adopt very few. The default is a modular monolith with clear internal boundaries, one primary persistence/search path, and one deployment unit. Add another database, service, orchestration framework, agent runtime, queue, cache, graph engine, or generic plugin framework only when a concrete requirement or benchmark justifies its ongoing operational cost. Do not confuse an interface for a future possibility with a requirement to implement a whole subsystem now.

## 1. Open-source application references

Use these repositories to study complete product workflows, information architecture, administrative functions, document management, connectors, deployment, and permission UX. They are not all equivalent in licensing, maturity, or core retrieval quality.

| Project | Primary reason to inspect | Reference |
|---|---|---|
| **Onyx** | Enterprise knowledge search; connectors; identity/group syncing; RBAC; full product operations | https://github.com/onyx-dot-app/onyx |
| **RAGFlow** | End-to-end document-centric RAG; document parsing and chunking; retrieval UX | https://github.com/infiniflow/ragflow |
| **AnythingLLM** | Local-first deployment, workspaces, local models, document ingestion, multi-user app | https://github.com/Mintplex-Labs/anything-llm |
| **Dify** | Visual workflows, knowledge pipelines, app configuration, model management and observability | https://github.com/langgenius/dify |
| **Kotaemon** | Document-focused QA UI, hybrid retrieval, reranking, rich citations and PDF source preview | https://github.com/Cinnamon/kotaemon |
| **FastGPT** | Knowledge-base product flows and RAG/workflow features | https://github.com/labring/FastGPT |
| **Open WebUI** | Local model integration and a polished self-hostable AI interface; inspect knowledge/RAG boundaries | https://github.com/open-webui/open-webui |
| **Quivr** | Knowledge-base application patterns; verify current project status and licensing | https://github.com/QuivrHQ/quivr |

**How to inspect:** run the project with a sample dataset, then trace actual ingestion → parse → chunk → index → query → rank → citation behavior. Read the authorization and deletion paths. Do not assume a polished demo implies that document-level permissions, incremental reindexing, or retrieval metrics are production-ready.

## 2. Retrieval frameworks and orchestration

| Project | What to study | Reference |
|---|---|---|
| **Haystack** | Explicit composable pipelines, async execution, retrievers, rankers, routing, and component testing | https://github.com/deepset-ai/haystack |
| **LlamaIndex** | Ingestion integrations; recursive/hierarchical retrievers; query fusion; metadata-aware retrieval; knowledge graph approaches | https://github.com/run-llama/llama_index |
| **LangGraph** | Durable, stateful workflows and human-in-the-loop control for genuinely multi-stage agentic tasks | https://github.com/langchain-ai/langgraph |
| **DSPy** | Optimizing model-driven pipeline components against programmatic metrics rather than hand-tuning prompts alone | https://github.com/stanfordnlp/dspy |
| **R2R** | Retrieval-oriented API, document management, hybrid search, graph features, access controls and deployment | https://github.com/SciPhi-AI/R2R |
| **txtai** | Dense/sparse search, relational data, graphs and workflows in one embedding database/framework | https://github.com/neuml/txtai |
| **FlashRAG** | Research implementations and reproducible experimentation across RAG methods | https://github.com/RUC-NLPIR/FlashRAG |
| **AutoRAG** | Pipeline evaluation/optimization patterns; verify fit with your own deployment needs | https://github.com/Marker-Inc-Korea/AutoRAG |

**Architectural rule:** pick at most one primary pipeline/orchestration framework unless a measured requirement justifies another. Keep core domain logic and retrieval contracts independent of it; do not let framework-specific objects become your entire data model.

## 3. Graph retrieval and knowledge-graph references

| Project / research | Why it matters | Reference |
|---|---|---|
| **Microsoft GraphRAG** | Community summaries, entity/relation extraction, graph indexing, global/local search; repository now says largely maintenance mode, so treat as research/design reference rather than assuming active product evolution | https://github.com/microsoft/graphrag |
| **LightRAG** | Dual-level retrieval, graph-assisted retrieval and incremental update design; published at EMNLP 2025 | https://github.com/HKUDS/LightRAG · https://arxiv.org/abs/2410.05779 |
| **Neo4j GraphRAG for Python** | First-party graph, hybrid, vector and graph traversal retrievers; source-backed graph integration | https://github.com/neo4j/neo4j-graphrag-python |
| **Neo4j LLM Graph Builder** | Document-to-knowledge-graph ingestion and graph inspection UI | https://github.com/neo4j-labs/llm-graph-builder |
| **FalkorDB GraphRAG-SDK** | A dedicated GraphRAG toolkit; review source, API, benchmark method, and fit with local self-hosting | https://github.com/FalkorDB/GraphRAG-SDK |
| **Graphiti** | Incremental temporal knowledge graphs, validity windows, provenance, hybrid retrieval; originally focused on agent context, but concepts may map to enterprise knowledge | https://github.com/getzep/graphiti |
| **Cognee** | Graph + vector memory/indexing, small-model/local execution options, deletion and recall abstractions | https://github.com/topoteretes/cognee |
| **HippoRAG** | Knowledge graphs plus Personalized PageRank for multi-hop retrieval; paper claims gains on multi-hop QA, so reproduce claims on your domain | https://github.com/OSU-NLP-Group/HippoRAG · https://arxiv.org/abs/2405.14831 |
| **FastGraphRAG** | Adaptive graph retrieval patterns; verify implementation and maintenance | Search the project via the GraphRAG landscape lists below |
| **nano-graphrag** | A small, hackable implementation useful for understanding core mechanics; not automatically a production architecture | https://github.com/gusye1234/nano-graphrag |
| **PathRAG** | Relational-path retrieval and redundancy pruning; AAAI 2026 paper | https://arxiv.org/abs/2502.14902 |
| **Awesome-GraphRAG survey list** | Broad index of graph-RAG papers, implementations, benchmarks and newer candidates | https://github.com/DEEP-PolyU/Awesome-GraphRAG |

Also research **GraphRAG-Bench**, **LinearRAG**, **Graphiti**, and graph-specific code retrieval projects such as **Code-Graph-RAG** if source code and service dependencies will be in the corpus. For code and architecture repositories, use parser-derived symbol/call/dependency graphs (for example Tree-sitter-based structure) rather than forcing every relation through free-form LLM extraction.

### Graph design guidance

- Preserve provenance for every entity and edge: source document/version, chunk/page/section, extraction method, extraction model/version, timestamp, and confidence/evidence text.
- Model temporal facts and contradictions instead of overwriting facts blindly. Distinguish “the source states X” from “X is verified true.”
- Add entity resolution and alias handling; naive string matching produces duplicated entities, while unconstrained fuzzy merges create false links.
- Use deterministic structured-source relations where possible. Use an optional local LLM for unstructured entity/relation extraction when justified; put extraction behind a queue and version its output.
- Graph queries must be bounded, permission-filtered, provenance-preserving and protected from runaway traversal. Apply ACLs during graph expansion, not after unrestricted nodes have already been selected.
- Compare graph-enabled retrieval with a graph-free baseline on query sets labelled for multi-hop/relationship needs. Do not pay the graph construction/query complexity tax if gains are not measurable.

## 4. Search engines and vector indexes

| Engine/library | Best reason to evaluate | Watch-outs / fit |
|---|---|---|
| **Qdrant** — https://github.com/qdrant/qdrant | Strong candidate for dedicated local/self-hosted vector search, payload filtering, dense+sparse hybrid search, multivector and multi-stage retrieval | Validate filtered recall, collection/tenant layout, payload indexes, backup/restore and operational behavior at target scale. Docs: https://qdrant.tech/documentation/search/hybrid-queries/ |
| **PostgreSQL + pgvector** — https://github.com/pgvector/pgvector | Minimize systems; transactional metadata plus vectors and full-text search for moderate-sized corpora | Understand HNSW/IVFFlat trade-offs and post-filtering. ACL/selectivity can reduce approximate-search recall; test iterative scans, tenant partitioning, exact search for small filtered subsets, and recall against exact ground truth. |
| **OpenSearch** — https://github.com/opensearch-project/OpenSearch | Production search engine with BM25, vector/semantic search, hybrid score normalization/RRF and reranking pipelines | More operational surface area than a single PostgreSQL deployment; benchmark memory/storage and filtered ANN behavior. Docs: https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/ |
| **Vespa** — https://github.com/vespa-engine/vespa | Full AI search platform with structured ranking, tensors, BM25, nearest-neighbor and phased reranking; strong candidate when relevance logic is sophisticated | More specialized modeling and operations; worth a serious study when advanced ranking/scale matters. |
| **Milvus** — https://github.com/milvus-io/milvus | Large-scale, dedicated vector workloads, sparse+dense retrieval and ANN/index options | May be excessive for a small company deployment; validate operational needs and filtered hybrid behavior. |
| **Weaviate** — https://github.com/weaviate/weaviate | Integrated object/vector search, hybrid queries, reranking and multi-tenancy | Re-check current licensing and which modules/features belong to which edition before adopting. |
| **FAISS** — https://github.com/facebookresearch/faiss | High-performance ANN libraries, CPU/GPU indexes, exact/approximate benchmarking | A library rather than a full enterprise document/search control plane; you must implement metadata filters, lifecycle and permissions around it. |
| **USearch** — https://github.com/unum-cloud/usearch | Compact fast ANN indexes, multiple language bindings, disk-backed/memory-mapped index options | Verify filter/deletion/concurrency and serving needs for the selected binding/version. |
| **LanceDB** — https://github.com/lancedb/lancedb | Embedded/local analytics-oriented vector store and multimodal data patterns | Compare against operational requirements, permissions, concurrency, incremental update and hybrid search needs. |
| **Chroma** — https://github.com/chroma-core/chroma | Fast experiments and lightweight local projects | Do not select solely for easy setup; evaluate production lifecycle, filtered recall, backups and concurrency. |
| **NVIDIA cuVS** — https://github.com/nvidia/cuvs | GPU vector search algorithms when the corpus and hardware justify GPU ANN | GPU ANN acceleration is separate from GPU embedding inference; do not assume it improves every query path. |

### Search-specific design points

- Use **dense vector search + BM25/full-text** as the initial baseline. Technical identifiers, version numbers, file paths, API symbols, error codes and acronyms often need lexical retrieval.
- Prefer **Reciprocal Rank Fusion (RRF)** as a safe baseline when dense and keyword scores are not calibrated. A weighted score blend can work, but score normalization/weights need evidence and validation. Qdrant explicitly documents RRF as a safe default when score priors and evaluation labels are absent.
- Query separate indexed fields where useful: title, headings, body, code/symbols, tags, and exact identifiers; apply boosts learned or tuned from evaluation rather than intuition alone.
- Evaluate sparse neural retrieval (e.g. BGE-M3 sparse output or SPLADE) and late-interaction retrieval (ColBERT) only if they improve your dataset enough to offset storage/serving complexity. They can be particularly useful for term-heavy and reasoning-intensive retrieval.
- Over-retrieval then rerank: fetch a reasonable candidate set from each retriever in parallel, fuse/deduplicate, rerank top N, then select a small final evidence set. Tune N and K with measured recall/latency.
- For filtered approximate nearest-neighbor search, test the actual selectivity distribution. Do not assume `top_k=10` returns ten authorized/relevant results after filters. pgvector documents that HNSW filters are applied after the approximate scan and can reduce result counts; iterative scans/partitioning/index strategy can address this.
- Store normalized metadata and ACLs outside untrusted document text. Filter each retrieval branch, graph edge expansion, caches, citation endpoints and downloads against the authenticated principal.

## 5. Embedding and reranking candidates

Do not call a model “frontier-equivalent” because it has a high average benchmark score. MTEB itself found that no one model dominated all embedding tasks. Use the official current MTEB/MMTEB leaderboard as a discovery tool, but compare retrieval metrics and latency on your own company-like corpus.

### Candidates to benchmark, with CPU and GPU deployment separated

| Candidate | Why test it | Primary link |
|---|---|---|
| **Qwen3-Embedding-0.6B** | Strong candidate for a local multilingual/instruction-aware baseline; Apache-2.0 model card; configurable output dimension | https://huggingface.co/Qwen/Qwen3-Embedding-0.6B |
| **Qwen3-Embedding-4B / 8B** | Higher-capacity, instruction-aware alternatives for GPU-enabled offline indexing/query embedding; Apache-2.0 model cards | https://huggingface.co/Qwen/Qwen3-Embedding-4B · https://huggingface.co/Qwen/Qwen3-Embedding-8B |
| **Qwen3-Reranker-0.6B / 4B / 8B** | Matching family options for cross-encoder ranking; evaluate query-time latency, not just ranking quality | https://huggingface.co/Qwen/Qwen3-Reranker-0.6B |
| **BGE-M3** | MIT-licensed, multilingual, up to 8192 tokens, dense + lexical sparse + multi-vector/ColBERT-style representations | https://huggingface.co/BAAI/bge-m3 · https://github.com/FlagOpen/FlagEmbedding |
| **BGE reranker family** | Mature reranking family; include multilingual models and efficient variants where relevant | https://github.com/FlagOpen/FlagEmbedding |
| **Snowflake Arctic Embed v2** | Apache-2.0 model family and strong enterprise-retrieval candidate | https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0 |
| **Nomic Embed v2 MoE** | Apache-2.0 candidate with multilingual support; verify implementation/runtime and performance under CPU constraints | https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe |
| **GTE ModernBERT base / reranker** | Smaller dense retrieval/reranking pair, long context and published retrieval/code-retrieval results | https://huggingface.co/Alibaba-NLP/gte-modernbert-base |
| **IBM Granite Embedding English R2** | Apache-2.0 English candidate for comparison | https://huggingface.co/ibm-granite/granite-embedding-english-r2 |
| **Google EmbeddingGemma** | Compact on-device candidate; review Gemma terms and commercial constraints rather than assuming Apache/MIT | Search the official Google model card and license |
| **Current 2026 candidates such as Harrier-OSS, newer Jina, Nemotron, and KaLM variants** | The model field moves quickly; check official cards, exact license and current MTEB model/task results before adding them | https://leaderboard.mteb.org/models |

**Critical licensing warning:** open weights are not the same as unrestricted open-source licensing. Some widely ranked embedding/retrieval models have non-commercial or custom licenses. Verify both the repository license and the model-weight/data terms for every candidate; do not make a commercial-use decision from third-party leaderboard tables.

### Model selection must consider

1. Retrieval Recall@K, nDCG@K, MRR and hard query buckets on internal-style data.
2. CPU query-embedding latency p50/p95; document embedding throughput; model RAM and cold-start time.
3. GPU VRAM, batch throughput, concurrency and latency when GPU is present.
4. Long inputs vs. the actual chunk size; English and any relevant non-English languages.
5. Exact identifiers, source code, logs, stack traces and technical documentation.
6. Instruction prefix/task configuration, query/document asymmetry, truncation/pooling, normalization and dimension choices.
7. Inference-runtime support, quantization quality loss, offline provisioning and model license.
8. Whether a different model requires re-embedding; index/model/dimension compatibility must be explicit.

Do **not** pick the 8B model as the default simply because it ranks well. It may be suitable for scheduled offline indexing on GPU, but the query must also be embedded at runtime and a large query encoder can harm CPU p95. Benchmark a small reliable CPU model and a larger GPU profile separately.

## 6. Local model serving runtimes

| Runtime | Use to investigate | Reference |
|---|---|---|
| **Hugging Face Text Embeddings Inference (TEI)** | Serving embeddings and supported rerankers; documented CPU and supported GPU routes, with air-gapped deployment guidance | https://github.com/huggingface/text-embeddings-inference |
| **Infinity** | OpenAI-compatible API serving for embeddings and reranking; dynamic batching and multiple backends/hardware options | https://github.com/michaelfeil/infinity |
| **Sentence Transformers** | Simple direct model implementation, CrossEncoder reranking, local experiments and batch embedding | https://github.com/UKPLab/sentence-transformers |
| **FastEmbed** | Lightweight ONNX-oriented embedding inference for supported models | https://github.com/qdrant/fastembed |
| **llama.cpp server** | Quantized local text-generation and supported embedding/reranking routes for compatible models | https://github.com/ggml-org/llama.cpp |
| **vLLM** | GPU-oriented local LLM serving and throughput/concurrency experiments where compatible with the target model/task | https://github.com/vllm-project/vllm |
| **ONNX Runtime** | Optimized CPU inference for supported exported models; must verify output parity and quality | https://github.com/microsoft/onnxruntime |

Use a stable internal interface for `Embed(texts, task, model_version)`, `Rerank(query, candidates, model_version)` and optional `Generate(...)`; keep hardware/runtime configuration behind the adapter.

## 7. Document parsing and ingestion references

Document extraction quality affects retrieval quality. Comparing embeddings on corrupted, missing, or badly ordered text does not fix the problem.

| Project | What to test | Reference |
|---|---|---|
| **Docling (IBM Research / DS4SD)** | Rich PDF layout and reading order, tables, figures, many office/web formats; unified document representation; MIT repo license | https://github.com/docling-project/docling |
| **MinerU** | Multiple parsing tiers, OCR/layout-aware parsing, broad file formats and structured output | https://github.com/opendatalab/MinerU |
| **Marker** | PDF/image to Markdown/JSON/HTML; CPU/GPU/MPS routes and optional LLM-based quality improvement | https://github.com/datalab-to/marker |
| **Apache Tika** | Broad MIME/document extraction and metadata; process isolation matters for hostile files; current 4.x has Markdown output by default | https://github.com/apache/tika |
| **Microsoft MarkItDown** | Lightweight conversion of many formats to Markdown; inspect its own security note about running with caller privileges | https://github.com/microsoft/markitdown |
| **Unstructured** | Partitioning and connectors for many document types; distinguish open-source parser capability from hosted/platform features | https://github.com/Unstructured-IO/unstructured |
| **NVIDIA NeMo Retriever** | Enterprise-oriented extraction workflows for text, tables, charts and images; check the split between open code, model requirements, NIM services and licensing | https://github.com/NVIDIA/NeMo-Retriever |

Implement a parser interface. Route by file type and need: fast native parsing for normal Office/text files, high-quality layout extraction for complex PDFs, OCR only when necessary, and deterministic fallbacks. Maintain parser versions and extraction diagnostics. Save the normalized structured representation and source anchors before chunking.

## 8. Agent harnesses, model-provider portability, plugin architecture and trajectories

The RAG product should be useful on its own and also expose secure retrieval capabilities to external agents. Research the following systems for ideas, while carefully separating an agent harness from a model-provider gateway, a tool protocol, and a retrieval engine.

| Reference | Patterns worth studying | Caveats / role in this project |
|---|---|---|
| **DeepSeek Harness / Cordis** — https://github.com/deepseek-ai/deepseek-harness · https://www.deepseek.com/harness/en/ · https://deepseek-harness.github.io/deepseek-harness/en/reference/ | Everything-as-a-plugin architecture; services and typed events; model, tools, sessions, storage, agent loops and UI as swappable capabilities; append-only session events; trajectory inspect/resume/fork/replay; composable profiles; tool approvals and multiple execution modes | Public preview and evolving APIs. Study deeply as a moddability/traceability reference, but verify license, stability, security and compatibility before adopting its packages. |
| **OpenCode** — https://github.com/anomalyco/opencode · https://dev.opencode.ai/docs/plugins/ | Multiple model providers, local models, plugin hooks, tools, sessions, session events, terminal/desktop/IDE interface and extension points | Best treated as a provider-agnostic coding-agent/harness reference; do not assume its internal agent loop is the right application runtime. Verify the current repository/license and exact adapter contracts. |
| **OpenHands / Agent Canvas** — https://github.com/OpenHands/OpenHands · https://docs.all-hands.dev/ | Agent-server abstraction, pluggable agent backends, remote/local workspaces, conversation/event APIs, task execution and automation | Useful if the product grows into an agent workbench. Avoid taking on a general agent platform if secure retrieval APIs and focused workflows meet the need. |
| **OpenAI Agents SDK** — https://github.com/openai/openai-agents-python | Tool contracts, handoffs, guardrails, sessions, trace/span hierarchy and evaluable agent workflows | Useful as a concept and possible adapter; it is not a provider-neutral mandate and does not replace local-first/offline guarantees. |
| **LiteLLM** — https://github.com/BerriAI/litellm | Provider gateway, normalized APIs, model routing/fallback policy, spend/rate tracking and proxy mode | Evaluate as a replaceable gateway, not a hard dependency. Review feature/licensing boundaries, data handling, provider-specific capability differences, and whether a proxy would become a critical operational dependency. |
| **Model Context Protocol (MCP)** — https://modelcontextprotocol.io/ · https://github.com/modelcontextprotocol | Standardized tools/resources/prompts so other agent harnesses can discover/use this product; optionally use MCP client integrations for approved external tools | MCP is a tool/context protocol, not a model API, agent runtime, authorization system or guarantee of safe execution. Implement authentication, authorization, timeouts and approval policy separately. |
| **OpenTelemetry GenAI conventions** — https://opentelemetry.io/docs/specs/semconv/gen-ai/ | Provider/model metadata, spans, tokens, tools and end-to-end traces across retrieval and agent calls | Verify current semantic-convention status and sensitive-data implications; avoid capturing full prompt/document payloads by default. |
| **Jev / TypeSafe router references** — https://jev.guru/ · https://jev.works/agents/tool-router · https://github.com/TypeSafeAI/typesafe-playground · https://jev-agent.com/jev-harness | Tool-menu selection, routing decisions, clarification/stop paths, risk gates, context filtering, verifier stages and side-by-side tool-routing experiments | Treat as an exploratory candidate, not a proven core dependency. The public examples found during research describe independent/community or demo flows, synthetic fixtures and/or simulated execution. Determine exactly which Jev product/API is intended, whether a supported self-hostable/open-source implementation exists, its terms, pricing, telemetry, security and operational guarantees, and whether it improves measured outcomes. |
| **Agent2Agent (A2A)** — https://a2a-protocol.org/ · https://github.com/a2aproject/A2A | Interoperability when agents themselves must delegate work to other agents | Only add A2A if agent-to-agent collaboration is a real requirement. Do not confuse it with MCP's tool/data integration role. |

### Design principles for provider and agent extensibility

- Define stable internal contracts for **chat/generation, embeddings, reranking, tool calling, structured output, streaming, cancellation, usage metadata, and health checks**. Capabilities vary by provider; expose a capability matrix instead of pretending every provider supports every feature equally.
- Support a practical first set of adapters: a configurable OpenAI-compatible endpoint; selected native provider adapters where justified; and local endpoints such as Ollama, llama.cpp server, vLLM or another supported local runtime. Allow user-defined provider plugins rather than hard-coding a provider list as the architecture.
- Consider LiteLLM or another provider gateway as an optional deployment profile. Keep application code behind a provider interface so users can instead connect directly to compatible endpoints. Document which configuration paths can send data outside the network.
- Provide an authenticated **MCP server** exposing minimal, permission-aware tools such as `search_documents`, `fetch_document_passage`, `get_document_metadata`, and optionally `answer_with_citations`. Enforce the caller's identity and ACLs in the service; never trust the agent to filter content. Consider an MCP client only for explicitly enabled integrations.
- Keep retrieval independent from orchestration. The agent harness may call retrieval as a tool, but deterministic search should not require an agent loop. Conversely, a user's own agent (OpenCode, DeepSeek Harness, Claude Code or a custom MCP-compatible agent) should be able to use the retrieval platform without adopting its UI.
- Design extension points around typed contracts, events and lifecycle/dependency management. Support versioned plugin manifests, compatibility declarations, health checks, explicit capabilities, scoped permissions, secret isolation, allowlisted network access and a clear install/upgrade/uninstall path. Treat third-party plugins as untrusted; use out-of-process isolation or sandboxing for plugins that execute code.
- A plugin can add a retriever, parser, provider, UI panel or tool, but cannot bypass ACL enforcement, fabricate trace events, read other plugins' secrets, or silently change global security policy.

### Session trajectory and observability UX

Implement an append-only event stream (or equivalent durable trace/event architecture) with queryable projections. Record enough to reconstruct **what happened and why a result was selected** without storing hidden chain-of-thought. At minimum, show:

1. User query ID, timestamp, actor/authorization scope and active index/model versions.
2. Normalized query and any generated query variants, with the strategy and reason code that selected them.
3. Retriever branches invoked; candidate IDs; rank/score features; fusion method; deduplication; reranker order; parent expansion; graph paths and source provenance when used.
4. ACL/filter decisions and counts in a way that never leaks restricted titles, snippets or existence to unauthorized users.
5. Evidence sufficiency/abstention decision, policy/version, cited chunk IDs, conflicting-source flags and concise machine-readable reason codes.
6. Optional model/provider request metadata, tool calls/results, retries, timeouts, usage/cost fields when supplied by the provider, approval gates, agent handoffs, plugin versions and stage durations.
7. Final answer and citation map, plus user feedback or human evaluation when captured.

Do **not** log private model chain-of-thought, secrets, raw documents or full prompts by default. Keep sensitive payload capture opt-in, redacted, role-restricted and retention-controlled. Make clear which events can be replayed deterministically and which require a fresh provider call. Replay must be safe by default: no repeated external writes/tool side effects unless an authorized user explicitly approves them.

A trace/trajectory UI should allow users and admins to expand retrieval stages, inspect the reason code for abstention, compare two runs, rerun a query against a named index/model/config, export redacted diagnostics, and follow a citation back to its source. Avoid claiming deterministic replay where model/provider responses or indexes have changed.

### Alternatives to GraphRAG: what to evaluate for which query class

There is no universal winner. Treat each item as a retrieval strategy behind one stable contract, and route selectively only when evaluation demonstrates value.

| Strategy | Best-fit query patterns | Strengths | Main risks/costs | Baseline experiment |
|---|---|---|---|---|
| **Lexical + dense hybrid with fusion + reranker** | Broad enterprise search, exact terms mixed with natural language | Strong default; keyword precision complements semantic recall; independently debuggable | More indexing and ranking stages; false positives if fusion/reranker is poorly tuned | BM25-only vs dense-only vs hybrid; add reranker; measure nDCG, precision, recall and latency |
| **Neural sparse retrieval (e.g. SPLADE / sparse representations in BGE-M3)** | Exact concepts, vocabulary mismatch, terms with strong token-level associations | Inverted-index serving shape; can complement dense retrieval | Model-specific licensing; indexing costs; sparse expansion can create noise | Add as a third candidate branch and compare unique relevant hits per millisecond and storage overhead |
| **Late interaction / multi-vector search (e.g. ColBERT)** | Fine-grained technical passages, longer queries, passages where token-level matching matters | Preserves token-level interaction instead of compressing all text into one vector | Larger index, more complex serving, reranking latency and hardware sensitivity | Same candidate corpus, compare recall/nDCG and total index/storage/query cost against dense+rereank |
| **Hierarchical or summary retrieval (e.g. RAPTOR; parent/child retrieval)** | Questions requiring a whole section, long documents, theme-level or cross-section synthesis | Finds context at multiple abstraction levels; can reduce lost section-level context | Summary errors, extra derived artifacts, stale summaries after updates | Chunk-only vs parent expansion vs hierarchical summary retrieval with citation/summary-fidelity checks |
| **Contextualized chunk retrieval** | Chunks whose meaning depends on title, product, project, section or surrounding context | Makes isolated chunks more self-describing; can improve recall | Preprocessing/model cost and derived-text maintenance; contextual prefix can distort source | Compare heading/metadata prefixes, parent text and optional generated contextual descriptions |
| **Structured retrieval (SQL, metadata, APIs, code symbols)** | Exact state, IDs, ownership, deployment versions, dates, structured tables, repositories and dependency queries | More reliable than semantic similarity for exact/authoritative facts; clear schemas and provenance | Requires governed source connectors and query policy; unrestricted SQL/tool execution is dangerous | Route structured-intent queries to typed read-only query tools and compare exact accuracy to RAG |
| **Federated search across sources** | Company-wide search across files, wiki, issue trackers, Git, tickets, SQL or approved APIs | Respects each source's native expertise/currentness and avoids unnecessary copying | Connector complexity, ranking across heterogeneous sources, permissions and freshness | Compare centralized ingest with federated query-time access for freshness-sensitive sources |
| **Adaptive/corrective RAG** | Difficult, ambiguous or low-evidence questions | Can retry, change retrieval strategy, decompose query or request clarification when initial evidence is weak | Extra model calls, loops, unpredictable latency and false correction decisions | Compare a bounded adaptive path with fixed hybrid retrieval; set hard latency/step budgets |
| **Agentic retrieval / tool planning** | Multi-step questions that require deciding among search, code search, SQL, graph, web or internal tools | Flexible composition of specialized tools and multi-hop plans | Most complexity: slower, harder to test, prompt-injection exposure and failure variance | Compare deterministic query routing with agent planner on a labeled set; require evidence that the planner adds value |
| **Graph-assisted / GraphRAG** | Relationship questions, multi-hop entity paths, temporal facts, group-level synthesis | Makes relationships explicit and can connect evidence scattered across documents | Graph extraction errors, entity resolution, stale/contradictory edges, higher indexing/storage/operations cost | Graph-off vs graph-on; label relation queries separately; measure evidence support and error rates, not answer fluency |
| **Long-context / retrieve fewer richer passages** | Small corpora or questions that need broad surrounding context | Simpler source handling for some cases; fewer chunk-boundary errors | Token cost, latency, noisy context, model lost-in-the-middle and provider dependency | Compare parent-context/section packs to many small chunks under equal latency/token budgets |

Recommended strategy router: **start with deterministic cheap decisions** (exact identifiers, metadata filters, query shape, corpus capabilities), then use hybrid retrieval. Escalate to structured tools, hierarchy, late interaction, graph or bounded agentic search only when the query type or low-evidence signal justifies it. Keep a safe, fast path that always works without a generation model.

## 9. Papers, surveys and benchmark references

### Core retrieval and RAG

- **RAG survey:** *Retrieval-Augmented Generation for NLP: A Survey* — https://arxiv.org/abs/2407.13193
- **MTEB:** *Massive Text Embedding Benchmark* — https://aclanthology.org/2023.eacl-main.148/ and https://github.com/embeddings-benchmark/mteb
- **MMTEB / current model and task comparison:** https://leaderboard.mteb.org/models
- **BEIR:** heterogeneous out-of-domain IR benchmark — https://github.com/beir-cellar/beir
- **BRIGHT:** reasoning-intensive retrieval benchmark (ICLR 2025) — https://arxiv.org/abs/2407.12883 · https://brightbenchmark.github.io/
- **ColBERT / ColBERTv2:** late interaction with token-level representations; candidate for difficult precision-heavy retrieval — https://github.com/stanford-futuredata/ColBERT
- **SPLADE:** learned sparse lexical expansion; note the specific model/license—some released weights use non-commercial terms — https://github.com/naver/splade
- **HyDE:** Hypothetical Document Embeddings for zero-shot retrieval; requires a generator, so it belongs in an optional generation-assisted path, not mandatory retrieval-only — https://aclanthology.org/2023.acl-long.99/
- **RAPTOR:** recursive summaries and hierarchical retrieval across multiple abstraction levels — https://arxiv.org/abs/2401.18059
- **Adaptive-RAG:** adapt retrieval strategy to query complexity rather than running the most expensive strategy on everything — https://aclanthology.org/2024.naacl-long.389/
- **Agentic RAG survey:** taxonomy of agentic retrieval designs and trade-offs; use as a map, then verify claims against primary systems and papers — https://arxiv.org/abs/2501.09136
- **Speculative RAG (ICLR 2025):** optional generation-latency strategy using small-model drafts and a larger verifier; only relevant if it improves a local generation deployment — https://arxiv.org/abs/2407.08223 · https://research.google/pubs/speculative-rag-enhancing-retrieval-augmented-generation-through-drafting-2/
- **Self-RAG:** learned selective retrieval and self-reflection; research reference for adaptive retrieval/generation, not a compulsory production architecture — https://proceedings.iclr.cc/paper_files/paper/2024/hash/25f7be9694d7b32d5cc670927b8091e1-Abstract-Conference.html
- **Corrective RAG (CRAG):** evaluate the quality of retrieved evidence and take corrective action when retrieval is poor — https://arxiv.org/abs/2401.15884
- **Anthropic Contextual Retrieval:** add concise context to chunks, combine BM25 and dense retrieval, use rank fusion and reranking. LLM-generated contextual prefixes can improve ambiguous isolated chunks but cost tokens/compute, must be stored/versioned, and are optional; test a cheaper heading/parent-context baseline first — https://www.anthropic.com/research/contextual-retrieval

### Graph RAG and multi-hop

- **GraphRAG survey:** https://arxiv.org/abs/2501.00309 and https://github.com/DEEP-PolyU/Awesome-GraphRAG
- **LightRAG:** https://arxiv.org/abs/2410.05779
- **HippoRAG:** https://arxiv.org/abs/2405.14831
- **PathRAG (AAAI 2026):** relational path pruning to reduce redundant graph evidence — https://ojs.aaai.org/index.php/AAAI/article/view/40268
- **GraphRAG-Bench:** benchmark and leaderboard — https://graphrag-bench.github.io/
- **Cognee paper:** optimizing the interface between knowledge graphs and LLMs for complex reasoning — https://arxiv.org/abs/2505.24478

### Evaluation and diagnostics

- **RAGBench:** 100k examples, industry-oriented data and explainable labels — https://arxiv.org/abs/2407.11005
- **ARES:** automated RAG evaluation using synthetic data, lightweight judges and prediction-powered inference — https://github.com/stanford-futuredata/ARES
- **BEIR:** test retrievers on heterogeneous information-retrieval datasets and out-of-domain tasks — https://github.com/beir-cellar/beir
- **RAGTruth:** hallucination corpus for RAG responses; useful for understanding generation failure modes — https://github.com/ParticleMedia/RAGTruth
- **RAGChecker (NeurIPS 2024):** fine-grained diagnosis of retrieval and generation — https://proceedings.neurips.cc/paper_files/paper/2024/hash/27245589131d17368cccdfa990cbf16e-Abstract.html · https://github.com/amazon-science/RAGChecker
- **Ragas:** reference-free / reference-based RAG metrics — https://github.com/explodinggradients/ragas
- **DeepEval:** test-style LLM/RAG evaluation — https://github.com/confident-ai/deepeval
- **Arize Phoenix:** open-source tracing, datasets, experiments and evaluations — https://github.com/Arize-ai/phoenix
- **MTEB/BEIR/BRIGHT:** use for generalization and reasoning-intensive evaluation; don't use any one leaderboard as the sole decision rule.

### Security and operational guidance

- **OWASP RAG Security Cheat Sheet:** threats/controls from ingestion through retrieval, context assembly, generation and tools — https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html
- **OWASP GenAI Security Project:** https://genai.owasp.org/
- **OWASP MCP Security Cheat Sheet:** least privilege, tool schema review/pinning, sandboxing, token audience/Origin checks and approval for consequential actions — https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html
- **pgvector index/filtered recall documentation:** https://github.com/pgvector/pgvector
- **Qdrant hybrid/multistage search guidance:** https://qdrant.tech/documentation/search/hybrid-queries/
- **OpenSearch hybrid and reranking docs:** https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/ · https://docs.opensearch.org/latest/search-plugins/search-relevance/reranking-search-results/
- **Microsoft RAG architecture/relevance guidance:** https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview?tabs=docs · https://learn.microsoft.com/en-us/azure/search/search-relevance-overview
- **Anthropic Opus 5.5 prompting guide:** https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5
- **Anthropic general prompting best practices:** https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices

## 10. An engineering playbook for accuracy, latency and trust

### Retrieval defaults to establish as hypotheses

1. **A retrieval-only vertical slice first:** indexing, search, ranked passages, source navigation, exact search, and permissions all work with no generative model installed.
2. **Hybrid recall:** parallel BM25/full text + dense semantic search. Add sparse neural retrieval if testing proves value. Use RRF initially when raw scores aren't calibrated.
3. **Candidate generation + reranking:** collect enough candidates from each branch to retain recall; deduplicate; rerank only a bounded set; then apply a tested final selection/context packing policy.
4. **Structure-aware chunks:** retain title, headings, page/section/anchor, table rows/header relationships, code symbol boundaries, and parent-child chunk links. Use recursive/semantic chunking as a candidate, not dogma. Benchmark several chunk sizes/strategies.
5. **Parent-child retrieval:** retrieve precise child chunks but display/pack the relevant parent section or nearby context when needed. Do not always inflate result text.
6. **Document-aware parsing:** table and reading-order fidelity, OCR detection, headers/footers, repeated boilerplate, page boundaries, malformed document isolation and parser-quality telemetry.
7. **Metadata-first filtering:** source type, collection, owning team, business domain, dates, lifecycle, document version, identifiers, language, and ACLs. Avoid metadata explosions and unindexed filters.
8. **Task-aware embedding:** use documented query/document prefixes/instructions required by the chosen model. Version this exact behavior, including tokenizer, pooling, normalization, dimensions and truncation. Add tests for it.
9. **Query rewriting only when useful:** deterministic normalization and exact-identifier extraction by default; optional local-LLM rewrite, multi-query/RRF, HyDE, question decomposition or agentic retrieval for ambiguity or hard queries. Never make the optional LLM a hard dependency of search.
10. **Adaptive complexity:** fast single-pass retrieval for straightforward questions; deeper candidates, query decomposition, graph traversal or iterative retrieval for relationship/multi-hop queries. Use deterministic heuristics initially; add a model-based router only if evals justify it.
11. **Graph as a targeted retriever:** traverse a bounded, permission-aware graph and return source-linked evidence. Do not use a knowledge graph as a replacement for source text, lexical retrieval or citation provenance.
12. **Calibrated abstention:** similarity/reranker scores are ranking signals, not probabilities. Tune thresholds with labeled data; consider evidence coverage, disagreement between retrieval branches, source quality and contradiction signals. Display “no strong evidence found” instead of fabricating certainty.
13. **Versioned indexes:** model ID/revision, preprocessing/chunker version, embedding dimensions, field mappings, source revision and corpus snapshot must be recorded. Build a replacement index, validate it, then atomically promote it; never combine vectors from incompatible spaces.
14. **Idempotent ingestion:** content checksum, source identity, versions, job idempotency keys, retry policy, dead-letter/error state, checkpointing, and deletion propagation across chunks, vectors, graph entities/edges, caches and search indexes.
15. **Secure caches and citations:** cache keys must include authorization scope and active index/model version. Every source-open, preview, download and citation path performs authorization again. Prevent permissions from leaking via snippets, graph neighbors, aggregates, error text or cached results.
16. **Observability:** distributed traces or structured spans for parse, chunk, embed, sparse index, dense query, lexical query, fusion, rerank, graph expansion, authorization, and generation. Track p50/p95/p99, candidate counts, zero-result rate, stage failures, model/runtime, CPU/RAM/VRAM, queue age and ingest throughput without logging confidential text.
17. **Real evaluation before tuning:** fixed benchmark set with source-grounded relevance labels and query categories. Establish a simple baseline, then run controlled ablations. Keep a final holdout untouched. Version datasets and judgments; avoid using generated evaluation questions as the only test.
18. **Ablations:** BM25 only; dense only; hybrid; hybrid + reranker; title/metadata boosts; parent expansion; sparse neural; ColBERT; graph; rewrite/multi-query; each optional strategy with and without CPU/GPU inference.
19. **Measure latency end-to-end and by stage:** query embedding often matters on CPU, not just offline document embedding. Measure cold/warm query embedding separately from ANN, lexical retrieval, reranking, graph traversal, and answer generation.
20. **Test security adversarially:** malicious/hidden instructions in documents, Unicode obfuscation, document poisoning, cross-user/cross-collection ACL leakage, revoked permissions, deletion/reindex races, oversized files, archive bombs, malformed parsers, indirect prompt injection and generated citations to inaccessible documents.

21. **Do not assume GraphRAG is the best answer:** compare it against a strong non-graph hybrid baseline, hierarchical/summary retrieval, late interaction (e.g. ColBERT), neural sparse retrieval, structured/database search, and agentic/adaptive retrieval. Graph is one route selected for relational or multi-hop questions, not a universal replacement for information retrieval.
22. **Use native retrieval for structured questions:** exact metadata filters, SQL queries over approved structured stores, API/tool lookups, code/symbol search and version/status queries can outperform semantic chunk search. Define safe query interfaces and avoid giving an LLM unrestricted SQL access.
23. **Build answerability as a measured decision:** evaluate query answerability separately from ranking. Use labeled answerable/unanswerable query pairs, hard negatives, near-miss documents, outdated/conflicting sources, and empty/partial indexes. Do not rely solely on a generator asking itself if it is confident.
24. **Use explicit result states:** distinguish `supported_answer`, `insufficient_evidence`, `no_relevant_evidence_found`, `index_coverage_incomplete`, `restricted_scope`, `conflicting_sources`, and `retrieval_or_provider_failure`. Never tell the user that information does not exist in the database when the evidence only shows that adequate evidence was not retrieved.
25. **Calibrate for the user's risk preference:** prioritize low false-positive rates even if this means abstaining more often. Tune thresholds on held-out judgments. Report answerable-query coverage alongside selective error/risk, abstention precision/recall, false-answer rate on unanswerable queries, and precision@K. If probability estimates are exposed, assess calibration (e.g. reliability curves, Brier score/ECE where statistically appropriate); otherwise label the number as a ranking/evidence score, not a probability.
26. **Verify grounded claims:** map each material answer claim to source passages, verify that citations exist and are accessible, and reject or soften claims not supported by the citations. Detect conflicts and source versions. An LLM-as-judge or entailment model is fallible; evaluate its false accept/reject rates on labeled examples.
27. **Surface corpus health alongside absence:** show the user's allowed source scope and freshness/ingestion status. If configured sources have failed, stale syncs or pending jobs, state that coverage is incomplete. Only make a stronger corpus-level absence claim when the relevant source scope is known to be complete and searchable.
28. **Compare orchestration methods:** deterministic pipeline, rules/thresholds, classifier-based strategy routing, an agentic plan/tool loop, and optional Jev/tool-router pilot. Agentic planning must have budgets, bounded steps, loop detection, timeouts, cancellation and an exit/abstain path. Do not add model calls without evidence of a quality benefit.

### Evaluation design

Create a versioned evaluation set containing real or realistically representative company-query patterns:

- Exact lookup: project IDs, endpoint paths, ticket/error IDs, symbols, version strings and filenames.
- Paraphrase / conceptual lookup.
- Name/alias disambiguation.
- Queries spanning several pages or tables.
- Contradictory documents and stale versions.
- Relationship/multi-hop queries that should benefit from graph retrieval.
- Low-confidence/unanswerable queries.
- Permission-restricted evidence and queries by users with different scopes.
- Long/ambiguous/underspecified queries.

Use at least Recall@K, Hit Rate@K, MRR and nDCG@K. Add answer-level faithfulness, citation correctness, supported-claim coverage and abstention precision/recall only for generation mode. Report confidence intervals or repeated-run variation where relevant. Measure latency p50/p95 and resource use with a defined corpus/concurrency setup. Have humans review LLM-as-judge disagreements; do not treat LLM judges as truth.

Use MTEB/MMTEB, BEIR and BRIGHT to screen candidate models/patterns, but construct the internal test corpus from actual document types and query patterns. **A local benchmark beats a leaderboard when selecting the final architecture.**

### Practical performance and deployment profiles

Treat laptop development and server hosting as explicit supported use cases, not just a statement that the code is portable. Keep one codebase and use configuration, runtime capabilities and deployment profiles rather than separate implementations.

- **Laptop development profile:** runnable on a developer laptop using documented commands and a lightweight local stack (Docker Compose and/or documented native processes). It must support CPU-only embedding and retrieval, retrieval-only mode without a generator, modest bounded concurrency, configurable limits for background jobs, local model caching, and a compact model option. Do not require Kubernetes, a discrete GPU, a graph database, or a fleet of services simply to try the application. Allow optional GPU use when compatible hardware exists. Provide a clearly isolated local database/index and sample corpus, plus a safe way to connect the local UI/API to a non-production shared development server when the laptop should not process a large corpus. Never sync confidential company data to a laptop by default.
- **Dedicated CPU server profile:** a supported, persistent, multi-user on-premises deployment with dedicated CPU/RAM/storage, identity/authentication, TLS behind a documented reverse proxy, durable background jobs, shared persistent metadata/indexes, backups, retention and recovery procedures. It must work without a GPU. Prioritize query-path resources over bulk ingestion, use bounded worker concurrency and queue backpressure, and document capacity assumptions for corpus size and concurrent users.
- **GPU-enabled server profile:** same product and APIs, but with optional accelerator-backed embedding and/or reranking and, if configured, local generation. Define independent device and memory limits, model placement, batching, concurrent request control, out-of-memory recovery and graceful CPU fallback behavior. CPU fallback must not change models silently or mix incompatible embedding spaces; explicitly report the selected runtime/model.
- **Scale-up path:** first support a coherent single-server deployment. Then allow separately scaling API instances, ingestion workers, embedding/reranking workers, and retrieval storage when measurements justify it. Use a durable queue/lease approach for background jobs; prevent duplicate processing. Kubernetes is optional and must not be a prerequisite for laptop or single-server operation.
- **Promotion and consistency:** document how a developer's local changes and configuration are promoted to a server. Use the same schema migrations, API contracts, versioned OCI/container images where useful, configuration schema, health checks and test suite. Store embedding model ID/revision, dimensions, normalization, preprocessing settings and index version; never combine incompatible vectors. A local index may be rebuilt on the server rather than copied if model/hardware/index compatibility differs. Keep environment-specific secrets and data separate.
- **Resource profiles and evidence:** publish a measured resource matrix for minimum-supported and recommended laptop/server configurations once model choices are made. Include RAM, CPU cores, disk/cache, model download/storage size, corpus size, index size, concurrency, startup/cold-start, ingest throughput, query embedding, retrieval, reranking, and p50/p95 latency. If hardware is unavailable, give provisional targets and reproducible benchmark scripts—not fabricated measurements. Explain trade-offs for constrained-memory laptops and how to disable optional expensive stages.
- **Avoid unnecessary infrastructure:** start with PostgreSQL plus the simplest suitable vector/full-text implementation, or another justified single-store baseline. Do not force a dedicated vector database, graph DB, cache, message broker, generation server or orchestrator into every profile. Add each only when it delivers measured value.

---

## How to use this research brief

Sections 1–10 above are a **research map and set of hypotheses**, not a shopping list or a mandate to implement every capability. Repository status, versions, licences, pricing, and model/runtime support can change; verify decision-critical facts in primary sources before adoption. The implementation instructions below are authoritative for scope, work order, acceptance gates, and stopping rules.


## 11. Final implementation prompt for Claude Opus 5.5

> **Authority rule:** This section controls execution, first-release scope, and completion claims. Sections 1–10 are technical research/reference material. If anything in the research catalogue appears to require every listed technology, treat that interpretation as incorrect. Preserve the core product outcomes and choose the smallest design that passes the acceptance gates.

### Role and mission

Act as the principal engineer for enterprise knowledge retrieval, search relevance, local ML inference, application security, and full-stack architecture. Design and build a maintainable, enterprise-oriented knowledge-search application that is genuinely useful without a generative LLM. It must run on a developer laptop using CPU inference and be deployable from the same codebase to a dedicated CPU/RAM/storage server. GPU acceleration, generation, graph retrieval, advanced agent integrations, and extra infrastructure are optional extensions whose value must be demonstrated.

Use the judgment of an experienced technical lead. Be skeptical of fashionable architectures, paper benchmarks that do not match the corpus, and polished demos that hide operational gaps. **Research broadly; investigate the strongest few candidates deeply; implement narrowly.**

Do not expose private chain-of-thought. Provide concise rationales, source links, trade-offs, acceptance checks, and test results instead.

### Absolute priorities, in order

1. **Trustworthy evidence and permission enforcement.** Do not leak unauthorized material. Prefer abstaining to unsupported positive answers. Make every returned passage traceable to a real source location.
2. **A complete retrieval-only product.** Search must work with no generative LLM, no cloud inference, and no external agent harness.
3. **Laptop-to-server deployability.** The laptop profile must work on CPU. A dedicated CPU server must support persistent, authenticated multi-user use. A GPU is an optional accelerator, not a separate product.
4. **Measured relevance and honest failure states.** Evaluate what is retrieved, what is missed, when the system abstains, and why a query fails.
5. **Operational simplicity.** Choose the fewest components that satisfy proven requirements. Additional databases, processes, queues, caches, frameworks, abstractions, and agent loops must earn their place.
6. **Extensibility.** Make future provider, agent, graph, and generation integrations possible at real boundaries, but do not build the whole roadmap before the core workflow works.

These priorities override breadth of features and number of technologies. Do not equate “enterprise-grade” with microservices, Kubernetes, a graph database, a plugin marketplace, or a large dashboard.

### Operating rules for this session

1. **Inspect first.** Examine the actual repository, project instructions, OS/runtime, available commands/tools, existing code, and test state before deciding what to change. Do not assume the repository is empty or that a command/service is available.
2. **Research with decision value.** Use primary repositories, papers, official docs, licenses and current release information. Use the research catalogue as a starting point, not an exhaustive checklist. For each material choice, compare no more than two or three credible candidates in depth. Stop researching a category once more research is unlikely to change the decision. Record the important sources and unresolved questions in one concise source register.
3. **Make a decision and proceed.** Produce a short plan and a few architecture decisions, then begin implementation. Do not spend the entire session writing a report or ask for approval unless a genuinely blocking decision cannot safely be assumed.
4. **One durable checklist.** Track items as `not started`, `in progress`, `blocked`, or `verified`; link to code/tests where useful. Give brief updates at meaningful milestones. Save useful state in repository docs before context compaction or ending the session.
5. **Prove each gate.** A feature is not done because its UI exists, a type compiles, a test file was written, or documentation describes it. Run the actual command, inspect the output, fix material failures, and rerun the relevant tests.
6. **Control complexity.** Begin with a modular monolith and one principal persistence/search path. Create only the module boundaries required by the current milestone. Introduce an interface when it isolates an actual dependency, improves testing, supports an actual second implementation, or enforces a security boundary—not to speculate about every imaginable future extension.
7. **Use subagents selectively.** If the environment supports them, delegate independent research or an isolated adversarial review with a narrow question and requested evidence. Do not delegate trivial or interdependent code edits. Treat delegate findings as unverified until checked.
8. **Security and privacy by default.** Do not send company documents or queries to external AI services. Local-only mode must not silently fall back to cloud inference. Make outbound integrations explicit, opt-in, and visible in configuration. Do not log secrets, raw documents, private chain-of-thought, or protected content by default.
9. **Do not fabricate evidence.** Never invent benchmarks, successful tests, model capabilities, licences, system requirements, security guarantees, or implementation status. Mark unmeasured targets and unsupported assumptions plainly.
10. **Review the actual UI.** When browser/screenshot tools are available, run the application and verify important screens visually and functionally. If such verification is unavailable, state that limitation.
11. **Use High effort for better decisions, not more architecture.** Opus 5.5 is running at High effort. Keep scope bounded by gates; do not add components just because more analysis time is available. If the approach becomes overbuilt, pause, inspect the diff, and remove unnecessary scope while preserving verified behavior.

### Architecture decision discipline

Before coding, briefly decide the smallest sensible architecture for the available environment. Compare no more than two or three candidates for each major decision:

- Build on an existing product/framework versus a focused custom app.
- The simplest suitable local-first search/storage engine.
- The parser(s) needed for the first supported document types.
- A CPU-capable embedding model and inference runtime.
- Whether inference needs a separate Python process/service alongside the primary application.

React + TypeScript, ASP.NET Core, and Python ML tooling are candidates, not mandates. Account for the user's .NET context, ML library compatibility, laptop setup burden, server operations, and maintainability. A Python process is justified only by a concrete benefit such as model/runtime support, isolation, or independent resource controls. Choose one primary search/storage path; do not automatically combine PostgreSQL, Qdrant, OpenSearch, and Neo4j.

Prefer one codebase and compatible schemas/contracts for laptop and server profiles. A different local storage implementation is acceptable only if it materially simplifies local operation and the migration/compatibility cost is documented. The CPU laptop setup must have clear start commands and must not require a GPU, generator, cloud model, Kubernetes, or an agent runtime.

For each nontrivial dependency, record briefly: requirement served, simpler option considered, actual/expected benefit, operational cost, and why it is retained. If the benefit is speculative, defer it.

### Required product behavior

#### 1. Search-only mode — first-class and always available

- Ingest a supported document and preserve its original identity, version, metadata, permission scope, and available page/section/anchor.
- Produce locally generated embeddings using CPU inference; optionally support GPU acceleration through configuration if the selected runtime supports it.
- Include lexical/full-text retrieval and evaluate a dense+lexical hybrid baseline with a simple rank-fusion method where practical. A simpler baseline may be retained when measurements or platform constraints justify it.
- Return ranked passages, useful snippets, source references, and a way to open the original source or its available location.
- Do not require a generator, provider gateway, cloud API, graph store, or agent loop.
- Make the active index/model version and indexing health observable to an administrator without exposing restricted content.

#### 2. Evidence-first abstention

The product should optimize for justified answers, not answer rate. False-positive answers are more costly than abstention for this use case.

Distinguish at least these internal outcomes using clear, stable reason codes:

- Adequate evidence found.
- No adequate supporting evidence found in the currently searchable, authorized corpus.
- Indexing incomplete, stale, or failed, so absence cannot be established.
- Conflicting or version-inconsistent evidence.
- Retrieval or a dependency failed.
- Access restrictions prevent use of required evidence, without disclosing restricted document existence to an unauthorized user.

The user-facing message should be honest about scope. Prefer wording such as “I couldn't find adequate supporting evidence in the indexed sources available to you.” Do not claim “this information is not in the database” unless corpus coverage and indexing completeness for the relevant scope are actually known. Do not expose document titles, snippets, counts, graph relations, cache entries, or existence signals from restricted sources.

A similarity score, vector distance, reranker score, or LLM statement of confidence is **not** a calibrated probability by itself. In retrieval-only mode, implement a conservative evidence gate using measurable signals such as lexical and semantic rank, rank gaps, agreement between retrieval branches, duplicate/source diversity, source freshness, and index health only where appropriate. Do not invent numeric confidence percentages.

#### 3. Evaluation of false positives and abstention

Create a repeatable, versioned evaluation set. If company documents are not supplied, use clearly labelled fixtures; do not claim they validate company-domain quality. Include:

- Answerable questions with known relevant documents/passages.
- Unanswerable queries for which the corpus contains no adequate evidence.
- Hard negatives with similar terms but the wrong subject or meaning.
- Exact IDs, project/service names, API paths, filenames, symbols, and error codes.
- Ambiguous queries; stale and conflicting versions; tables/structured content where supported.
- Empty, partially indexed, failed-ingestion, deleted-document, and different-permission-scope scenarios.

Measure applicable retrieval metrics such as Recall@K, Hit Rate@K, Precision@K, MRR and nDCG. For answerability/abstention, measure at least:

- **False-answer rate on unanswerable queries:** the fraction of unanswerable queries for which the system returns an unsupported substantive answer.
- **Answer coverage:** the fraction of queries the system chooses to answer.
- **Precision among answered queries:** the fraction of returned answers/results that satisfy predefined evidence-support criteria.
- **Retrieval recall:** separately measure whether the relevant evidence was retrieved, so retrieval failure is not confused with an abstention decision.
- **Citation support** for generation mode: cited passages actually support the claims attributed to them.
- **Risk/coverage trade-off:** how unsupported-answer risk changes as the system answers more queries.

Separate validation and test data when tuning any evidence threshold. Keep the test set held out. When data is too small to calibrate reliably, say so and use conservative, explainable behavior; do not claim that false-positive risk is low based on a handful of examples. Avoid exposing a numeric probability unless calibration has been validated. Track corpus coverage and indexing health independently from relevance.

#### 4. Optional local RAG generation

Generation is a later capability, not a prerequisite for search. When enabled and explicitly configured, it may synthesize an answer from retrieved evidence. It must cite real, authorized source passages, distinguish evidence from inference, disclose material conflicts, and abstain when evidence is insufficient. Disabling or removing the generation model must not break ingestion or search. Never silently switch to a remote model.

#### 5. Alternatives to GraphRAG

Do not assume GraphRAG is best. Consider the query types actually present and compare a strong simpler baseline with only relevant alternatives:

- Lexical/BM25 + dense hybrid retrieval with rank fusion and (if justified) reranking.
- Neural sparse retrieval and late-interaction/multi-vector methods such as ColBERT.
- Structure-aware chunking, parent/child context and hierarchical/summary retrieval.
- Structured retrieval from approved read-only SQL, metadata, code symbols, or APIs.
- Federated retrieval from sources where freshness or native authorization favors query-time access.
- Adaptive/corrective retrieval, bounded agentic tool planning, and graph-assisted/multi-hop retrieval.
- Longer source sections/context packs where this is simpler and performs better.

Run ablations where feasible. Add graph extraction/storage/traversal only if graph-on materially improves the right query classes and its new failure modes, data-maintenance burden, ACL behavior, latency, and operational cost are justified. Build graph edges with provenance, version/freshness semantics, bounded traversal, and tests against permission leakage if graph is later adopted.

#### 6. Documents and ingestion

Start with the formats required by files actually supplied or, if none are available, a small and documented initial set. Do not treat every format, OCR, and every listed parser as MVP scope. Compare parsers against representative fixtures for extraction fidelity, tables, pages/anchors, runtime, licensing and local CPU requirements.

The first-release ingestion path should validate files, extract text/structure, preserve provenance and access metadata, split content into sensible structure-aware chunks, embed locally, index, and show real progress/errors. It must not reprocess unchanged files unnecessarily. Add idempotent retries, document updates/deletions, stale index detection, and failure recovery as part of first-release reliability. Keep expensive graph extraction or LLM-based contextualization optional.

#### 7. Laptop and server profiles

Support one product with three documented deployment profiles:

1. **Developer laptop:** resource-aware CPU inference and local persistent data; straightforward, tested start commands. No mandatory GPU, generation, cloud AI, Kubernetes, or full agent harness.
2. **Dedicated CPU server:** same core behavior/contracts, persistent data, authenticated multi-user access, permission-aware retrieval, bounded background work, configuration, backup/restore guidance, health/diagnostics, and a repeatable concurrency/performance test.
3. **Optional GPU server:** accelerate compatible embedding/reranking/generation stages without changing application business logic. Document drivers/runtime/model compatibility, memory needs, batching, concurrency, and failure behavior. CPU profiles remain supported.

For each profile, report what has actually been tested, hardware and corpus details, setup friction, model downloads/storage requirements and offline runtime behavior. Distinguish offline runtime from air-gapped installation; document how models and dependencies are provisioned. Do not claim a GPU profile has been verified if suitable hardware was unavailable. Keep model IDs/revisions, embedding dimensions, normalization/preprocessing and index version explicit; never mix incompatible vector spaces.

A single-server deployment is the initial server target. Do not require Kubernetes. Scale workers or services separately only when measured workload justifies the operational cost.

#### 8. Security and privacy

Treat documents as untrusted data. Enforce authentication and authorization server-side for search, collection/document filtering, snippets, citation opening, previews, downloads, graph traversal, and cache access. Prevent content or even the existence of restricted material from leaking through ranking, diagnostics, logs, traces, errors, and generated answers. Test revoked permissions and cache invalidation.

Use safe upload limits, validation, timeouts, bounded concurrency, secure configuration/secrets, dependency/license reviews, and appropriate audit events. Treat prompt injection inside documents as untrusted source content in optional generation mode. Avoid logging raw documents and sensitive query payloads by default. Document the threat model, data flows, trust boundaries, and remaining risks; do not call the system secure/compliant merely because authentication exists.

For the local laptop, implement the simplest safe local-user/auth configuration supported by the design. For server use, support authenticated multi-user identity and resource-level authorization. Do not invent a company SSO provider requirement; keep the identity integration configurable until the actual environment is known.

#### 9. Provider and agent compatibility — after the core gate

The user values the ability to connect model providers and external AI agents. Design stable, documented contracts so the app can later expose retrieval through a small REST API and, where valuable, authenticated MCP tools. Prefer supporting a standard OpenAI-compatible endpoint for compatible providers rather than implementing every vendor separately. Make provider adapters replaceable, and keep retrieval independent of generation and orchestration. Provider compatibility does not mean every provider/agent must be implemented in the first release.

Study these as reference implementations rather than runtimes to stack together:

- OpenCode for provider adapters, plugins, sessions, tools, and UI patterns.
- DeepSeek Harness/Cordis for modular capabilities, event-driven sessions and trajectory inspection.
- OpenHands for agent/runtime and session patterns.
- MCP for tool/resource interoperability; consider A2A only if agent-to-agent delegation becomes a real need.
- LiteLLM and relevant OpenAI-compatible gateways for model routing, after reviewing data flow, licensing, and operational cost.
- Jev/TypeSafe routing/verifier references only after identifying the exact product/API/repository and checking licensing/terms, self-hosting, telemetry/data flow, security, maturity, and a credible test case.

Do not build a custom agent harness, multi-agent orchestration, dynamic plugin loader/marketplace, trajectory replay/forking, or Jev prototype just to satisfy a checklist. Once the core passes its gates, choose one integration based on an actual use case. Record concise structured events for retrieval stages when useful; never record private chain-of-thought or confidential payloads by default. Any plugin or tool integration must not bypass the app's ACL and security policy.

#### 10. UI and implementation quality

Build a polished, functional enterprise search workspace—not a generic chatbot landing page. First-release UI focus: query/search controls, filters appropriate to the corpus, ranked evidence, source navigation, ingestion status, and useful error/no-result states. Add only the settings/diagnostics required to operate that workflow. Use coherent typography, spacing, hierarchy and accessible behavior; avoid gratuitous gradients, glass effects, oversized marketing text, fake metrics, excessive cards, and decorative AI motifs. Every visible control must work.

Use the existing ecosystem's conventions, typed contracts, migrations, validation, structured errors, useful health/readiness checks, cancellation/timeouts, and tests. Pin important dependencies where appropriate. Prefer established parsers, databases and inference libraries to home-grown implementations. Use OpenAPI for the application API where practical. Do not create a separate document for every conceivable future subsystem; documentation must support actual decisions, development, operation and evaluation.

### Execution gates — follow this order

#### Gate 0 — Inspect, decide, start

Inspect the existing repository and available environment, report only the assumptions that materially affect implementation, and compare at most two or three candidates for the major decisions. Record a concise architecture overview/decision log and source register in the repository. Then proceed to implementation without waiting for approval unless blocked by a real dependency or unsafe assumption.

#### Gate 1 — The decisive search-only vertical slice

Implement one complete, runnable workflow:

1. Start the app using documented commands in the available laptop environment, on CPU.
2. Ingest at least one representative company document if provided; otherwise use clearly labelled fixtures.
3. Persist document identity, version, source location, metadata and permissions.
4. Embed locally and retrieve relevant passages using the selected lexical/dense baseline.
5. Show the ranked result and open the actual source/location.
6. Demonstrate an exact-identifier query and a conceptual/paraphrased query.
7. Demonstrate no-generator operation with all generation/cloud settings absent.
8. Run basic authorization-isolation, parsing/indexing-error, no-evidence, and source-link tests.

**Do not implement the optional items below before this gate passes.** A polished UI, architecture diagram, generic abstraction, or extensive research report cannot compensate for a search workflow that fails this test.

#### Gate 2 — First-release reliability and evidence quality

After Gate 1 passes, make the core slice credible:

- Incremental updates, delete propagation, deduplication and idempotent retry behavior.
- Clear state for failed/stale/incomplete indexing and recovery/retry paths.
- Permission regression tests across search, snippets, citations, previews, downloads and caches.
- A small versioned evaluation set with answerable queries, unanswerable queries and hard negatives; retrieval baseline metrics and abstention risk/coverage reporting.
- Repeatable measurements for latency/resources where hardware/corpus are available; otherwise provide benchmark scripts and mark metrics unmeasured.
- Backup/reindex/restore and local/server configuration documentation appropriate to the implemented system.
- Basic health checks, structured diagnostics and operational guidance.

Do not claim abstention is calibrated unless the held-out labelled evaluation provides enough evidence. Do not add a reranker merely because it is common; evaluate its gains versus resource/latency cost on the same candidate pool.

#### Gate 3 — Optional, one capability at a time

Only after Gates 1 and 2 pass, select the highest-value next capability based on an explicit user problem, expected benefit, small design, and acceptance test. Possible candidates include:

- A minimal authenticated REST/MCP integration for an external agent.
- One provider adapter or OpenAI-compatible model endpoint.
- Optional local answer generation with claim/source validation and abstention.
- Local reranking if the measured improvement warrants it.
- Graph-assisted retrieval for a labelled set of relationship/multi-hop queries.
- A simple inspectable retrieval-run view based on safe, redacted stage events.
- An advanced parser or document connector demanded by real corpus needs.

For Jev, first resolve exactly which product is intended and verify source/API, terms/licence, hosting, privacy/data flow and maturity. Make an adopt/prototype/defer/reject decision. Prototype only when a real, reproducible test can compare it to deterministic routing and a plausible measurable benefit exists. “Investigated” does not mean “adopted.”

Do not stack OpenCode, DeepSeek Harness, OpenHands, LangGraph, LiteLLM and Jev into one runtime. They overlap; borrow patterns only when needed. Generic plugin lifecycle, marketplace, dynamic code loading, multi-agent orchestration, A2A, trajectory replay/forking, multiple production search engines, separate graph database, Kubernetes and large evaluation dashboards are not first-release requirements.

#### Gate 4 — Deployment and hardening

Verify the CPU laptop profile and the dedicated CPU server profile where the environment permits. Verify GPU acceleration only when compatible hardware is available. Run the available test suite; examine security and authorization failures; test recovery and deletion; document measured latency/resource results with hardware, corpus and concurrency; and align README claims with actual behavior. Do not add distributed infrastructure without load evidence.

### Minimal required repository artifacts

Create only the documentation and code needed to make decisions, operate the product and verify it:

- A README with laptop setup, CPU server deployment, optional GPU setup, model provisioning/offline behavior, supported formats, limitations, test commands, and recovery basics.
- A concise architecture overview/data-flow/deployment diagram and a small ADR/source register covering only material decisions.
- API schema/OpenAPI and database migrations/configuration as appropriate to the chosen architecture.
- A clearly labelled fixture corpus and repeatable retrieval/abstention evaluation script if company data is unavailable.
- Automated unit/integration tests for the core workflow, permissions, ingestion/update/delete, source links, and failure states.
- Basic operating guidance for backups, reindexing, model/index versions and troubleshooting.

Do not create multiple long research documents just because they appear in an earlier speculative list. A concise, source-backed research note is enough to support the decisions. Detailed reference information in this prompt need not be copied verbatim into the repository.

### First-release acceptance criteria

The first release is accepted when the following are verified, not merely documented:

1. A developer can start the application from documented commands on the tested laptop environment.
2. CPU-only search works with no generator, no GPU and no outbound AI inference.
3. A supported document can be ingested; job status/errors are real; unchanged content is not unnecessarily reprocessed.
4. Search returns useful ranked evidence for an exact-identifier query and a conceptual query, with real source references.
5. No-evidence, incomplete/stale indexing and technical failure are not represented as the same state.
6. Authorization checks prevent cross-scope access through the tested endpoints and UI paths.
7. Update/deletion/retry behavior does not leave stale searchable content or create duplicate indexed records in the exercised tests.
8. A repeatable labelled fixture evaluation reports the baseline metrics actually measured, including unanswerable-query false-answer behavior and answer coverage where applicable.
9. The dedicated CPU-server profile is runnable/documented with persistent storage and authenticated multi-user access; claim only the tests actually run.
10. The UI is checked functionally, and visually if the tooling is available.
11. There are no fabricated test results, benchmark values, confidence probabilities, licence claims or completion statements.

GPU operation, graph retrieval, generation, external agent integrations, a plugin framework, a trajectory replay UI, OCR for every file type, Kubernetes, and broad provider support are **roadmap capabilities**, not first-release acceptance criteria. The architecture should leave sensible room for them without implementing them prematurely.

### Reporting and stopping rules

At each gate, report concisely:

- What changed and the important files/modules.
- Exact commands run and their actual results.
- What is verified versus merely implemented, planned or blocked.
- Retrieval/abstention/performance measurements, including dataset/hardware context; mark unmeasured items clearly.
- Remaining risks and the next highest-value task.

Do not stop after writing a plan or reporting that the next step is to implement. Continue with in-scope work until the current gate passes or a genuine blocker prevents it. Do not keep adding optional features or researching indefinitely after enough evidence exists to proceed. If a reviewer finds a material correctness, security, data-loss, authorization, regression, or acceptance-criteria issue, fix it and rerun relevant tests; do not invent speculative requirements during review.

**Start now: inspect the repository and available runtime, make the minimum defensible architecture decisions, then implement Gate 1.**

---

## 12. Final principle

**Build evidence retrieval first; prove it can abstain; then add only those advanced capabilities that earn their cost.** A trustworthy CPU-only search application that deploys cleanly to a dedicated server is the product. Graphs, generative answers, provider adapters, agent harnesses, plugins and trajectories are optional ways to extend that product—not prerequisites for it.
