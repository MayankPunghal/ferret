-- A hash of each passage's embedding text: on re-index, passages whose text and model are unchanged keep their
-- vector instead of being embedded again (docs/plan-large-documents.md step 3). Older rows have none.
ALTER TABLE chunks ADD COLUMN embed_hash text;
