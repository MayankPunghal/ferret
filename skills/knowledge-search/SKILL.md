---
name: knowledge-search
description: Answers questions from the user's own documents (runbooks, design docs, wikis, policies, slide decks, PDFs) through their local Knowledge Search app, and manages that app (upload folders, check indexing, retry failed documents, re-index). Use when the user asks something their organisation's documents should answer ("how do we roll back X", "what does error ERR-… mean", "who owns the ledger service", "what breaks if we change Y"), asks to search, read or upload their docs, or asks about the knowledge-search index.
---

# Knowledge Search

The user runs Knowledge Search, a local search app over their documents, at `KS_URL` (default
`http://127.0.0.1:8000`). `ks.py`, next to this file, is the only way to reach it: Python 3 with no dependencies,
JSON output.

```bash
python ks.py <command> ...       # run it from this skill's folder or give the full path
```

Sign-in comes from the environment: `KS_TOKEN`, or `KS_USER` + `KS_PASSWORD`, an account of the app. Use a
read-only account unless the user wants you to upload or delete. A `KS_TOKEN` is read-only unless it was
created with "May also make changes" on the app's AI assistants page. The token is cached in
`~/.cache/knowledge-search/`. If they are missing, ask the user to set them. Never ask the user to paste a
password into the chat.

**Data leaves the machine.** The app itself never calls an external AI service. With this skill, every
passage you read is sent to Anthropic as part of the conversation. The first time in a conversation, say this
in one line, unless the user already acknowledged it. If they say their documents must not leave the
company, stop and point them to the app's own search page (`KS_URL` in a browser).

## Answering a question

1. **Search.** `python ks.py search "<question>"`. Read `outcome` before the results:
   - `evidence_found`: passages with `supported: true` passed the app's evidence check.
   - `conflicting_evidence`: versions of one document disagree. Compare `version` (series, version,
     effective_date) and say which one applies, or that it is unclear.
   - `no_adequate_evidence`: nothing passed the check. Unsupported results are closest matches only.
   - `index_coverage_incomplete`: some documents are still indexing or failed, so a missing answer proves nothing.
     Say so.
   - `retrieval_degraded` / `retrieval_failure`: a technical problem. Run `ks.py status` and report it.
2. **Search again** when the first query misses: use the exact identifiers, error codes, service and file names,
   and the user's own words, plus one or two rephrasings. Results can be `linked_from` a page the question names.
   Those come from following that page's links.
3. **Read enough context.** A result's `text` is one passage. Use `ks.py passage <chunk_id>` for the passages
   before and after it. Use `ks.py doc <document_id> --text` when the answer spans a document, for example
   steps, a table or a whole runbook.
4. **Follow connections** for "what depends on", "who owns", "what breaks if" or multi-hop questions:
   - `ks.py links <document_id>` lists links out, links in, mentions and related-in-meaning documents, each
     with a `chunk_id` you can open;
   - `ks.py impact <document_id>` lists what depends on it, up to 2 hops.
5. **Answer only from what you read.** Cite each claim with its document title, its section (`heading_path`)
   or page, and the `open` link. Quote exact values (commands, numbers, limits) from the text rather than
   paraphrasing them.
   - If the passages do not answer the question, say that the indexed documents the account can read do not
     cover it. Do not fill the gap from general knowledge without labelling it as such.
   - `summary`, `topics` and `clearer_title` are written by a small local model and can be wrong. Use them to
     choose what to read, never as evidence.
6. Keep it short: the answer, then sources. Mention conflicts, outdated versions (`outdated_version`) and
   documents you could not read.

## Managing the app

| Task | Command |
|---|---|
| Who am I, which collections (ids) can I read or write | `ks.py me` |
| Index health: coverage, models, summary progress | `ks.py status` |
| List documents (filter `--collection ID`, `--status failed`) | `ks.py docs` |
| Upload files or whole folders (recursive; .md .txt .html .docx .pptx .pdf) | `ks.py upload PATH... --collection ID` |
| Retry a failed document | `ks.py retry DOCUMENT_ID` |
| Delete a document (permanent) | `ks.py delete DOCUMENT_ID --yes` |
| Re-index outdated documents, or `--all` (administrators) | `ks.py reindex` |

- Uploading is asynchronous. Check progress with `status` or `docs`.
- Unchanged files are skipped. A changed file with the same name becomes a new version.
- Failed documents carry `error_code`. `no_extractable_text` means a scanned PDF or an empty page, and retrying
  will not help. Tell the user, and offer to delete it.
- Ask the user before every delete and before `reindex --all`, which can take hours on a large archive.
- Settings, users and groups are managed in the app's web page (Settings and Users & access tabs), not here.
