-- Any embedding dimension (models are selectable at runtime); vectors of different models never mix
-- because every query filters on chunks.embedding_model. Exact KNN needs no typed column.
ALTER TABLE chunks ALTER COLUMN embedding TYPE vector;

-- Which model + chunking configuration produced a document's current chunks. When the active
-- configuration differs, the document is "outdated" (coverage incomplete) until reindexed.
ALTER TABLE documents ADD COLUMN index_signature text;

-- Admin-editable runtime settings (overrides of KS_* defaults), e.g. chunk size, reranker.
CREATE TABLE app_settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_by bigint REFERENCES users(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
