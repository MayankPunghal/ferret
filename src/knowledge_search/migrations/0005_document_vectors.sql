-- One vector per document (mean of its passage vectors) for meaning-based "related pages" links
-- (graph.similar). Filled at indexing time; existing documents are backfilled from their passages by
-- db.migrate in Python (pgvector's avg() crashes the embedded PostgreSQL on Windows, like its distances).
ALTER TABLE documents ADD COLUMN embedding vector;
ALTER TABLE documents ADD COLUMN embedding_model text;
