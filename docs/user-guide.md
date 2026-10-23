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

Results that did not pass the evidence check are listed as **closest matches — not confirmed as
evidence**. The right passage is often among them (especially for paraphrased questions); read before relying on it.

## Each result shows

- document title, collection, heading path or PDF page, and version;
- a snippet with the matching words highlighted;
- when the optional local language model is installed: **Summary (local model)** — two sentences on what the
  document is about — and **Topics**; click a topic to search for it. A vague title ("Overview", a file name)
  is replaced by a clearer one written by the model; hover over it to see the document's own title. These
  are written by the model, so they can be wrong: the snippet and passage are the evidence, never the summary;
- related documents in up to four rows — **Links to**, **Referenced by**, **Mentions** and **Related in
  meaning** — up to four each; click one to open its passage (the passage view lists them all);
- **View passage** — the passage with the chunks before and after it;
- **Open source** — the original file (PDFs open at the page).

Through the API (`POST /api/search`) each result additionally includes the full chunk text, `chunk_id`,
`document_id`, `passage_url`, `source_url` and the ranking signals.

## Questions that follow links

When your question names a page — by its title or file name, e.g. *"who is on call for the services the
Orders Service depends on"* — search also follows that page's links (both directions, one hop) and adds the
most relevant passage of each linked page to the results, marked **linked from Orders Service**. Up to half
of the ten results can come from linked pages; the rest are ordinary matches. Only pages you may read are
followed. If the question names no page, the links of the top result are followed. Administrators can turn
this off (Settings → Linked documents).

## Connections (linked documents)

**View passage** also shows how the document is connected to others you can access:

- **Links from this document** — Markdown links, `[[wiki links]]` and front-matter relations
  (`depends_on`, `related`, `calls`, `owned_by`, `part_of`, `see_also`); a link to `file.md#section`
  opens that section.
- **Linked from** — documents that link to or depend on this one.
- **Mentions (found automatically)** — documents connected without any link: this document names their
  title ("… uses the Asset Service …"), or they name this one. Works for crawled or exported pages that do
  not declare links; titles of two or more words are matched (singular/plural and case ignored).
- **Related by meaning (found automatically)** — documents about the same thing even if neither names the
  other: each is among the other's three closest documents by meaning (with the similarity shown). Useful
  for crawled pages; on the Kubernetes docs most such pairs are pages the authors also linked by hand.
- **Depends on this (blast radius)** — documents that depend on it directly or via one more hop:
  "what is affected if this changes?".

**Show as graph** draws the same neighbourhood (1 or 2 hops; blue = depends on, grey =
links to, dashed = related / part of, dotted = mentions, green dots = related by meaning); click a node to open that document. Click any entry to open it. Documents you cannot access never appear; links whose target is not found in
your accessible documents are listed by name only. Example front matter:

```yaml
---
title: Orders Service
depends_on: [payments-service, ledger-service]
part_of: checkout-workflow
---
```

## Uploading documents

**Documents** tab (needs write access to a collection): pick the collection, then drag files onto the drop
zone or click it to browse. Several files can be uploaded at once.

- Supported: `.md` (optional YAML front matter: `title`, `series`, `version`, `effective_date`), `.txt`
  (UTF-8), `.html`/`.htm` (doc-site and wiki exports: only the main content is indexed, menus are dropped;
  links become connections), `.docx` (Word: headings, lists, tables), `.pptx` (PowerPoint: a
  section per slide, with the speaker notes), `.pdf` with a text layer.
  Scanned PDFs without text are rejected (`no_extractable_text`); legacy `.doc`, `.pptx`, `.xlsx` are not
  supported yet. Opening an HTML source shows it with scripts and external content disabled; Word files
  are downloaded.
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
