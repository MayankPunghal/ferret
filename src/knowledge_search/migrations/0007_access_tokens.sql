-- Access tokens for AI assistants (MCP, skill): long-lived sessions with a name, listed and revoked by their
-- owner on the "AI assistants" page. Browser sessions keep label NULL.
ALTER TABLE sessions ADD COLUMN id bigserial UNIQUE;
ALTER TABLE sessions ADD COLUMN label text;
