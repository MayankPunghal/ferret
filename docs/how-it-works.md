# How it works (plain language)

## When a document is uploaded

1. **Text and structure.** The file is read (Markdown, text, HTML, Word or PDF) and split along its headings
   into **passages** of about 900 characters (setting: chunk size). Each passage remembers its document,
   heading path, page and version.
2. **Meaning as numbers.** A local AI model (default `BAAI/bge-base-en-v1.5`, running on your CPU — nothing
   is sent anywhere) turns every passage into an **embedding**: a list of 768 numbers that places the
   passage in a "meaning space". Passages about similar things get similar numbers, even with different
   words ("roll back a release" ≈ "undo a bad deployment").
3. **Words and identifiers.** The words of each passage go into a keyword index (PostgreSQL full-text
   search), and code-like tokens (`ERR-PAY-4012`, `orders-svc`, `/v1/orders`) into an exact-match list.
4. **Links.** Links in the document (Markdown/HTML links, `[[wiki links]]`, front matter such as
   `depends_on`) are stored as edges of the document graph.

Everything is stored in one PostgreSQL database, so permissions, versions and deletions apply to the text,
the vectors and the links at the same time.

## When someone searches

1. The question is turned into 768 numbers with the same model.
2. Three searches run, each only over documents the user may read:
   - **meaning:** the question's numbers are compared with every passage's numbers. The comparison is a
     *dot product* (multiply the numbers pairwise and add up); because all vectors have length 1 this is
     the cosine similarity — 1.0 means "same meaning", around 0 "unrelated". It is an **exact** search
     over all passages (one matrix × vector multiplication in memory), not an approximation;
   - **keywords:** full-text search;
   - **identifiers:** exact matches of codes in the question.
3. The three ranked lists are merged (reciprocal rank fusion: a passage near the top of several lists wins).
4. **Links:** if the question names a page, passages of pages linked to and from it are added
   ("linked from …").
5. **Reranker:** a second local model reads the question together with each of the top 10 passages and
   re-orders them (more accurate than comparing numbers, too slow to run on everything).
6. **Evidence check:** the system says "relevant passages found" only when the top passages are similar
   enough to the question *and* share enough of its words (thresholds measured on labelled questions);
   otherwise it says it found no adequate evidence, or that indexing is incomplete, instead of guessing.

## Why no separate vector or graph database

The vector maths is the cheapest step. Measured on a 4-core laptop CPU, exact search over all passages takes
0.5 ms for 10,000 passages, 5 ms for 100,000 and 39 ms for 500,000 (1.5 GB of memory); turning the question
into numbers takes 25–90 ms and the reranker 100–400 ms. Dedicated vector databases (Qdrant, Milvus, …)
become useful at millions of passages, where they speed things up with *approximate* indexes (HNSW) —
trading a little accuracy for speed — and they add a second system whose permissions and deletions must be
kept in sync. Likewise a graph database (Neo4j) pays off for deep traversals over millions of edges; here
questions follow 1–3 links. See [deployment](deployment.md#large-corpora) for what changes at the scale of
an organisation's whole document archive.

## Accuracy before speed

Quality comes from the models (embedding, reranker), the chunking and the evidence check — not from the
database. Settings let administrators choose larger models and a stronger reranker when a second or two of
extra time is acceptable; each choice is measured in [evaluation](evaluation.md).
