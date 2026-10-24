-- Access tokens are read-only unless created with "may also make changes" (MCP and skill can add documents,
-- and for administrators manage folders, crawls and settings). Tokens made before this were promised read
-- access only, so they stay read-only.
ALTER TABLE sessions ADD COLUMN read_only boolean NOT NULL DEFAULT false;
UPDATE sessions SET read_only = true WHERE label IS NOT NULL;
