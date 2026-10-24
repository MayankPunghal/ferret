# Plan: keep tables as tables (owner request, 2026-10-24)

Owner request: "The crawler sucks at maintaining the structure of tabular data." Seen on the AWS Transform
quotas page: the copied Markdown shows each table row as a paragraph, and the rows under a merged
"Mainframe" cell start with an empty cell.

**Status: plan, not built.**

## Cause (checked in the code)

One converter does it for both apps: `site2md/src/site2md/html.py` `html_to_markdown`, used by Ferret
Crawler for every page and by Ferret for uploaded `.html` files (`parsing.parse_html`).

1. **Not a Markdown table.** Cells are joined with ` | `, but rows have no `|` at the start and end, and
   there is no `|---|` line under the header. Ferret's reading view (`app.js`) only draws a table for lines
   that start and end with `|`, so every row shows as a paragraph.
2. **Merged cells lost.** `rowspan` and `colspan` are ignored: "Mainframe" appears in the first of its three
   rows only; the others start with an empty cell.
3. **Cells with several paragraphs break the row,** because `<p>` inside a cell starts a new block.
4. **Search loses context.** Long tables are split into passages like any text (`parsing._windows`), so a
   later passage can hold rows without the header and without the merged cell: "Lines of code for each job |
   30,000,000 | No" never says "Mainframe" or "Quota". Such a passage is hard to find and hard to read alone.

The same weakness is in Ferret's Word converter (`convert.docx_to_markdown`: ` | ` joins, merged cells
ignored) and PowerPoint tables (`convert.pptx_to_markdown`: each row a `- a | b` line). PDF tables are a
different problem (the PDF text has no cell boundaries); they stay with "better PDF parsing" in
[TODO.md](TODO.md).

## What to build, in order

### 1. Write real tables (small; fixes what the owner saw)

In `html_to_markdown`, collect each `<table>` into a grid instead of streaming text:

- Rows from `<tr>` (in `<thead>`, `<tbody>`, `<tfoot>` order); cells from `<th>`/`<td>`.
- **`rowspan` / `colspan`: repeat the cell's text into every row and column it covers**, so each row is
  complete on its own ("Mainframe | Lines of code for each job | 30,000,000 … | No | …").
- Header row: the `<thead>` row, else a first row of `<th>`, else the first row.
- Output GFM: `| a | b |`, then `|---|---|`, then the rows. A `|` inside a cell becomes `\|`; line breaks
  and paragraphs inside a cell become `<br>` so the row stays on one line. Links and inline code inside cells
  keep their Markdown.
- `<caption>` becomes a bold line above the table.
- Rows with fewer cells are padded so every row has the header's width.

The same grid writer is used by the Word converter (`w:vMerge` / `w:gridSpan` are its merged cells) and the
PowerPoint converter (`a:tc` with `rowSpan` / `gridSpan`), so all three produce the same tables.

### 2. Tables that are not data stay text

Some pages use tables only to place things on the page. Write the cells as plain paragraphs, not a grid,
when the table:

- has one column, or one row; or
- contains another table (the outer one is layout; the inner one is written as a table); or
- has cells with headings, lists or code blocks (GFM cells cannot hold them).

### 3. The reading view shows them properly

`app.js` already builds `<table>` from `|` lines; add: the first row as a header (`<th>`), skip the `|---|`
line, turn `<br>` back into line breaks (nothing else from the cell is treated as HTML), and keep the
horizontal scroll wrapper for wide tables.

### 4. Never cut a table in the middle when splitting into passages

In `parsing._windows` / `chunk_document`, treat a table as one block:

- A table that fits in a passage stays whole (passages may run to the existing 1.5× size limit for it).
- A longer table is split **between rows**, and every part starts with the table's header row (and caption),
  so each passage reads as a small table with its column names. The heading path already gives the section.
- No overlap inside tables: repeating the header does that job.

### 5. Measure one more form before choosing (not assumed)

A table row may match questions better when indexed as "Workload: Mainframe · Dimension: Lines of code for
each job · Quota: 30,000,000 lines of code for each supported region · Adjustable: No" while still being
shown as a table. That is a guess, not a result. Compare **table passages with repeated headers** (step 4)
against **rows written as "column: value" for the index only**, on about 20 labelled questions about table
pages (AWS quotas pages, Kubernetes reference tables, a Word file with merged cells), with `eval/tricks.py`.
Keep the change only if it is clearly better.

## Not planned (and why)

- **Keeping the website's HTML table inside the Markdown.** Markdown allows it, but the reading view would
  then have to show raw HTML from websites safely, and search would index the tags. The grid plus the rules
  above cover nearly all real tables; very complex ones (lists or code inside cells) lose layout but no text.
- **A table-extraction model for PDFs.** That belongs with OCR and Docling (TODO item 20), measured
  separately.

## Tests

HTML fixtures, each checked as Markdown text and as passages:

- header in `<thead>`; header as `<th>` in the first row; no header at all;
- `rowspan` (the AWS shape: one cell over three rows) and `colspan`; both in one table;
- `|` inside a cell; a cell with two paragraphs; a link and inline code in a cell; a caption;
- a one-column layout table and a table nested in a table;
- a 200-row table split into passages: every passage starts with the header row, no row is cut.

Word: a table with vertically and horizontally merged cells. PowerPoint: a slide table with a merged cell.
Reading view: one Playwright check that the AWS-shaped table shows as a table with a header row.

## Rollout

- **Ferret Crawler** gets a version bump; its manifest records the converter version. Existing copies change
  only when crawled again, or with **Update** if the copy kept the original HTML (`save_html`): then pages
  can be converted again without fetching.
- **Ferret** bumps its parser version, so documents converted the old way show as outdated in Index status;
  **Reindex** applies the change.

## Size

Small to medium: the grid writer shared by three converters (~150 lines), passage splitting for tables, a few
lines in the reading view, the fixtures above, and one evaluation run for step 5.
