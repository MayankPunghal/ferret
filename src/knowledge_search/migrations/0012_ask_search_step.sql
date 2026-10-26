-- Ask: keep the search step with each answer (what was searched, how it scored, the closest matches), so a
-- reopened conversation still shows why the answer rests on those passages and what the evidence check said.
ALTER TABLE ask_messages ADD COLUMN IF NOT EXISTS search jsonb;
