-- Live progress of an indexing job, shown in the Documents tab.
ALTER TABLE jobs ADD COLUMN stage text;
ALTER TABLE jobs ADD COLUMN progress_done integer;
ALTER TABLE jobs ADD COLUMN progress_total integer;
