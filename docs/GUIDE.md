# Knowledge Search — Setup and User Guide

## 1. Install (once)

| Platform | Command |
|---|---|
| Linux / macOS | `./setup.sh` (add `--demo` for sample data, `--start` to launch afterwards) |
| Windows | `setup.bat` (add `-Demo`, `-Start`) — runs `setup.ps1` with PowerShell |

What it does, step by step (all idempotent): check Docker → install `uv` if missing → start
PostgreSQL+pgvector (`docker compose up -d db`) → `uv sync` → download the embedding model → apply
migrations → create `admin` (password printed once and saved to `.admin-password.txt`).

Start the app any time with `uv run knowledge-search serve` → <http://127.0.0.1:8000>.

## 2. Organise access: collections, groups, users

A **collection** is a set of documents and the unit of permission. **Groups** are granted read or write
on collections; **users** belong to groups. Admins can read and write everything.

```bash
uv run knowledge-search create-collection policies --description "Company policies"
uv run knowledge-search grant policies everyone            # read access
uv run knowledge-search grant policies policy-editors --write
uv run knowledge-search create-user maria --password -      # prints a generated password
uv run knowledge-search add-to-group maria everyone
uv run knowledge-search add-to-group maria policy-editors
```

Membership changes apply on the user's next request; `add-to-group`/`remove-from-group` also sign the
user out everywhere.

## 3. Test run with your own documents

1. Put 3–10 files in a folder, e.g. `~/trial-docs/` (`.md`, `.txt` UTF-8, or `.pdf` with selectable text).
   Use non-confidential documents for a first trial.
2. Create a collection and give yourself access (or sign in as `admin`):
   ```bash
   uv run knowledge-search create-collection trial
   uv run knowledge-search grant trial trial-users --write
   uv run knowledge-search create-user tester --password -
   uv run knowledge-search add-to-group tester trial-users
   ```
3. Ingest — either the CLI (indexes before returning):
   ```bash
   uv run knowledge-search ingest trial ~/trial-docs --wait
   ```
   or the UI: **Documents → choose file → collection `trial` → Upload & index** (status changes from
   `pending` to `indexed`; failures show a reason code and a **Retry** button).
4. Search (UI or API) with three kinds of queries and note the outcome banner:
   * an **exact term** from a document (an ID, code, filename, product name) → expect *evidence found*;
   * a **question in your own words** about something the documents explain → expect the right passage
     at the top (it may be shown as a lower-relevance match — the gate is deliberately conservative);
   * a **question the documents cannot answer** → expect *no adequate evidence*.
5. Click **View passage** (context around the hit) and **Open source** (the original file; PDFs open at
   the page).
6. Edit one file and ingest again: only the changed file is reprocessed (`queued`), the rest print
   `unchanged`; searching for removed text no longer finds it.
7. Optional: write your queries and the expected files into a copy of `eval/queries.jsonl` and run
   `eval/run_eval.py` to get measured numbers for *your* documents (see docs/EVALUATION.md).

Markdown tips: YAML front matter (`title`, `series`, `version`, `effective_date`) improves titles and
lets the app flag conflicting versions of the same policy series.

## 4. Reading search results

| Outcome banner | Meaning |
|---|---|
| Supporting evidence found | at least one passage passed the evidence gate (exact identifier, very strong semantic match, or semantic + keyword agreement) |
| Evidence found, but sources disagree | supported passages come from different versions of the same `series` |
| No adequate supporting evidence | nothing passed the gate in the indexed sources **you can access**; not a claim that the information does not exist anywhere |
| Indexing incomplete | some documents in your scope are pending, failed or outdated, so absence cannot be established |
| Retrieval degraded / failed | a technical problem; not a statement about the documents |

Badges explain why a passage counts as evidence. Weaker candidates are listed separately under
"lower-relevance matches".

## 5. Operations

* **Health:** `/healthz`, `/readyz`; **Index status** tab (your scope); admins also see job queue state.
* **Backup:** `docker compose exec db pg_dump -U ks ks > backup.sql` and copy `data/blobs/`.
  Restore: load the dump into an empty database, put `blobs/` back. (Procedure not yet rehearsed.)
* **Reindex everything** (e.g. after changing `KS_EMBED_MODEL`):
  `docker compose exec db psql -U ks ks -c "INSERT INTO jobs(document_id, version_id) SELECT id, latest_version_id FROM documents"`
  then make sure a worker is running. Re-run the evaluation and re-tune `KS_GATE_*` for the new model.
* **Server mode:** run the API with `KS_INPROCESS_WORKERS=0` and one or more `knowledge-search worker`
  processes so bulk uploads do not slow down searches.
* **Logs:** structured to stdout. Document text and query text are not logged; the `audit_events` table
  records logins, uploads, deletes, source opens and search outcomes (without query text).
