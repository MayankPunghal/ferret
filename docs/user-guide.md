# User guide

## Searching

Type a question, a phrase or an exact term into the search box. Use the **Collections** chips to limit the
search; you only ever see collections you have access to.

Good queries:
- exact identifiers: error codes (`ERR-PAY-4012`), API paths, configuration keys, file names, versions;
- natural questions: "how do I roll back the orders service?";
- key phrases: "parental leave entitlement".

## Reading the result banner

| Banner | Meaning |
|---|---|
| **Relevant passages found** | At least one passage passed the evidence check (badges explain why: exact identifier, strong semantic match, keyword agreement, reranker judgement). The check establishes that a passage is on-topic and closely matching — read it before relying on a specific detail. |
| **Evidence found, but sources disagree** | Supporting passages come from different versions of the same document series (e.g. 2024 vs 2025 policy). Check the effective dates. |
| **No adequate supporting evidence** | Nothing passed the check *in the indexed sources you can access*. This is not a claim that the information does not exist anywhere. |
| **Indexing incomplete** | Some documents in your scope are still being indexed, failed, or were indexed with old settings, so absence cannot be established. |
| **Retrieval degraded / failed** | A technical problem (e.g. a model could not be loaded). Not a statement about the documents. |

Results that did not pass the evidence check are listed under **lower-relevance matches** — often still
useful, but treat them with care.

## Each result shows

- document title, collection, heading path or PDF page, and version;
- a snippet with the matching words highlighted;
- **View passage** — the passage with the chunks before and after it;
- **Open source** — the original file (PDFs open at the page).

Through the API (`POST /api/search`) each result additionally includes the full chunk text, `chunk_id`,
`document_id`, `passage_url`, `source_url` and the ranking signals.

## Uploading documents

**Documents** tab (needs write access to a collection): pick the collection, then drag files onto the drop
zone or click it to browse. Several files can be uploaded at once.

- Supported: `.md` (optional YAML front matter: `title`, `series`, `version`, `effective_date`), `.txt`
  (UTF-8), `.pdf` with a text layer. Scanned PDFs without text are rejected (`no_extractable_text`).
- Uploading an identical file again does nothing (`unchanged`). Uploading a changed file with the same name
  creates a new version and replaces the old passages once indexed.
- Failed documents show the reason and a **Retry** button. **Delete** removes the document, its passages
  and the stored file.

Markdown tip: front matter like

```yaml
---
title: Travel and Expense Policy (2025)
series: expense-policy
version: "2025"
effective_date: 2025-04-01
---
```

lets search flag conflicts between versions of the same policy.
