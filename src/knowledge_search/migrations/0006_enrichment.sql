-- Summary, topics and (for vague titles) a clearer title written by a local language model (enrich.py), for
-- the indexed version named inside the JSON. enrich_claimed_at marks a document being summarised right now.
ALTER TABLE documents ADD COLUMN enrichment jsonb;
ALTER TABLE documents ADD COLUMN enrich_claimed_at timestamptz;
