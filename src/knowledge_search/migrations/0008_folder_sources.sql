-- Folders on the app's computer that are indexed by path and synced on request (Documents → Folders, MCP).
-- documents.folder_id: the folder a document came from (a sync removes those whose file is gone).
CREATE TABLE folder_sources (
    id            bigserial PRIMARY KEY,
    collection_id bigint NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    path          text NOT NULL,
    types         text[] NOT NULL,
    created_by    bigint REFERENCES users(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    synced_at     timestamptz,
    result        jsonb,
    UNIQUE (collection_id, path)
);
ALTER TABLE documents ADD COLUMN folder_id bigint REFERENCES folder_sources(id) ON DELETE SET NULL;
CREATE INDEX documents_folder ON documents(folder_id) WHERE folder_id IS NOT NULL;
