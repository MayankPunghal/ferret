-- Initial schema: identity, permissions, documents/versions, chunks, jobs, audit.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id            bigserial PRIMARY KEY,
    username      text NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9_.-]{2,64}$'),
    password_hash text NOT NULL,
    is_admin      boolean NOT NULL DEFAULT false,
    disabled      boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE groups (
    id   bigserial PRIMARY KEY,
    name text NOT NULL UNIQUE
);

CREATE TABLE user_groups (
    user_id  bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id bigint NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

-- Opaque session tokens; only the SHA-256 of the token is stored.
CREATE TABLE sessions (
    token_hash bytea PRIMARY KEY,
    user_id    bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions(user_id);

-- A collection is the unit of permission: documents inherit its grants.
CREATE TABLE collections (
    id          bigserial PRIMARY KEY,
    name        text NOT NULL UNIQUE CHECK (name ~ '^[a-z0-9_-]{2,64}$'),
    description text NOT NULL DEFAULT ''
);

CREATE TABLE collection_grants (
    collection_id bigint NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    group_id      bigint NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    can_write     boolean NOT NULL DEFAULT false,
    PRIMARY KEY (collection_id, group_id)
);

-- Logical document identity = (collection, source_key). Versions hold content.
CREATE TABLE documents (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id      bigint NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    source_key         text NOT NULL,
    source_uri         text,
    title              text NOT NULL DEFAULT '',
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    latest_version_id  bigint,
    indexed_version_id bigint,
    status             text NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'processing', 'indexed', 'failed')),
    error_code         text,
    error_detail       text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (collection_id, source_key)
);

CREATE TABLE document_versions (
    id          bigserial PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_no  integer NOT NULL,
    sha256      text NOT NULL,
    filename    text NOT NULL,
    media_type  text NOT NULL,
    size_bytes  bigint NOT NULL,
    created_by  bigint REFERENCES users(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_no)
);
CREATE INDEX document_versions_sha_idx ON document_versions(sha256);

ALTER TABLE documents
    ADD CONSTRAINT documents_latest_fk FOREIGN KEY (latest_version_id)
        REFERENCES document_versions(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT documents_indexed_fk FOREIGN KEY (indexed_version_id)
        REFERENCES document_versions(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;

-- Searchable passages. Only chunks of documents.indexed_version_id exist (old ones are
-- replaced in the same transaction), and embedding_model records the vector space.
-- No ANN index on purpose: exact KNN over the authorized subset is exact-recall ground
-- truth at small/medium scale. See docs/ARCHITECTURE.md for when to add HNSW.
CREATE TABLE chunks (
    id              bigserial PRIMARY KEY,
    document_id     uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id      bigint NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    collection_id   bigint NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    ordinal         integer NOT NULL,
    doc_title       text NOT NULL,
    heading_path    text NOT NULL DEFAULT '',
    page            integer,
    anchor          text,
    text            text NOT NULL,
    idents          text[] NOT NULL DEFAULT '{}',
    embedding       vector(384) NOT NULL,
    embedding_model text NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', doc_title), 'A') ||
        setweight(to_tsvector('english', heading_path), 'B') ||
        setweight(to_tsvector('english', text), 'C')
    ) STORED,
    UNIQUE (version_id, ordinal)
);
CREATE INDEX chunks_tsv_idx ON chunks USING gin(tsv);
CREATE INDEX chunks_idents_idx ON chunks USING gin(idents);
CREATE INDEX chunks_collection_idx ON chunks(collection_id);
CREATE INDEX chunks_document_idx ON chunks(document_id);

-- Durable job queue (SELECT ... FOR UPDATE SKIP LOCKED); no external broker.
CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id   bigint NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed', 'superseded')),
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_at    timestamptz,
    error_code   text,
    error_detail text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_queue_idx ON jobs(status, run_after);
CREATE INDEX jobs_document_idx ON jobs(document_id);

-- Audit trail: identifiers and actions only, never document text or query text.
CREATE TABLE audit_events (
    id        bigserial PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    user_id   bigint,
    action    text NOT NULL,
    target    text,
    detail    jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE app_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);
