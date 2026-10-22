-- Deterministic link graph between documents (docs/research-graph.md). Rows are rebuilt with the
-- document's chunks (cascade) and targets are resolved at query time, so links to documents ingested
-- later start working without reindexing the source.
CREATE TABLE doc_links (
    id          bigserial PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id    bigint NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    kind        text NOT NULL,      -- links_to | depends_on | related | calls | owned_by | part_of | see_also
    target      text NOT NULL,      -- normalised: lower-case file name or page title
    anchor      text,               -- heading slug, if any
    label       text NOT NULL DEFAULT ''
);
CREATE INDEX doc_links_target_idx ON doc_links(target);
CREATE INDEX doc_links_document_idx ON doc_links(document_id);
