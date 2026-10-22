# Admin guide

Administrators see two extra tabs: **Settings** and **Users & access**.

## Settings

| Setting | What it does | Trade-off |
|---|---|---|
| **Embedding model** | Turns passages and queries into vectors for semantic search. Default `bge-base-en-v1.5` (~210 MB) — best measured accuracy/speed trade-off; `bge-small` (~70 MB) is ~3× faster to index but less accurate; larger models are available but untested here. Measurements: [Evaluation](evaluation.md). | Larger = better retrieval, slower indexing and queries on CPU. Changing it requires a reindex. |
| **Chunk size** | Target passage length in characters (200–4000). | Smaller = more precise passages; larger = more context per result. |
| **Overlap** | Repeats the last N characters of a passage at the start of the next, so a sentence split at a boundary is still found. Untick to disable. | More overlap = slightly larger index. |
| **Reranker** | A local cross-encoder re-reads the top candidates together with the query, re-orders them and (in the reranker gate modes) decides what counts as evidence. | Better precision/coverage; adds CPU time per query (see [Evaluation](evaluation.md)). |
| **Candidates to rerank** | How many top results the reranker reads. | More = better recall, slower. |
| **Evidence thresholds** (advanced) | When a passage counts as evidence. | Higher = fewer false answers, more "no adequate evidence". Tuned values come from the evaluation; re-tune after changing the model. |

Changing the model or chunking marks already-indexed documents as **outdated**: the Settings page shows how
many, and search reports *indexing incomplete* until they are reindexed. Click **Reindex outdated
documents** (or **Reindex everything**); progress is visible on the Documents and Index status tabs.
New models are downloaded once in the background (shown under the model selector).

All models run locally (ONNX Runtime on CPU); nothing is sent to external services.

## Users & access

- **Collections** — create one per team/topic. Grant a **group** read or read & write access. Collections
  without grants are visible to admins only.
- **Users** — create users and type their groups (comma-separated). Changing a user's groups, disabling or
  resetting a password signs that user out everywhere. Admins can read and write every collection.
- Groups are created implicitly the first time you use a name.

Permissions are enforced inside every database query: users never see restricted documents, snippets,
counts, or even whether a restricted document exists.

## Command line equivalents

```bash
uv run knowledge-search create-collection policies --description "Company policies"
uv run knowledge-search grant policies everyone              # read
uv run knowledge-search grant policies policy-editors --write
uv run knowledge-search create-user maria --password -        # prints a generated password
uv run knowledge-search add-to-group maria everyone
uv run knowledge-search ingest policies ./policies --wait     # bulk import a folder
```

## Index status and jobs

**Index status** shows per-collection counts (indexed, pending, failed) and the active configuration; admins
also see the job queue. Jobs that crash are retried automatically (3 attempts with backoff); parse errors
are permanent until the file is fixed and uploaded again.
