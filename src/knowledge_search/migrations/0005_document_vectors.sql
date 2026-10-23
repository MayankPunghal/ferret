-- One vector per document (mean of its passage vectors) for meaning-based "related pages" links
-- (graph.similar). Filled at indexing time; existing documents are backfilled from their passages.
ALTER TABLE documents ADD COLUMN embedding vector;
ALTER TABLE documents ADD COLUMN embedding_model text;
UPDATE documents d SET embedding = s.v, embedding_model = s.m
FROM (SELECT document_id, avg(embedding) AS v, min(embedding_model) AS m FROM chunks GROUP BY document_id) s
WHERE s.document_id = d.id;
