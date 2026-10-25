# Plan: counts on the Summaries tab, like Index status (owner request, 2026-10-24)

Owner request: the Summaries tab should have the same count tiles and per-collection table as Index status.

**Status: built 2026-10-25** (local session; HANDOFF §4p).

Built as planned, with these differences:
- `GET /api/documents` keeps its list response (external pipelines read it); the total is in the
  `X-Total-Count` header, and the tab's counts come from `GET /api/documents/counts`. The server filters
  (`status`, `q`, `doc_type`), sorts (`sort`, `desc`) and pages (`limit` up to 1,000, `offset`).
- Name sorting is now done by PostgreSQL (`lower()`), so "Step 10" sorts before "Step 2"; the browser used
  natural order before. An ICU collation with numeric ordering would fix it if anyone minds.
- "Show more" adds 100 rows at a time up to 1,000; past that the tab asks to filter by name or type.
- The pace is the median of every summary's `seconds` for the current model, not only the last 20
  (there is no time stamp per summary to pick the last 20 by).

## What is there today, and two problems found while checking

- The Summaries tab shows one line from `/api/status` ("Qwen3.5-0.8B…: 254 of 3511 documents · working"),
  a thin progress bar and then **one row per document**.
- It computes its own numbers from `GET /api/documents`, which returns **at most 1,000 documents** (`api.py`
  `list_documents`, `LIMIT 1000`). Past 1,000 documents the bar and the rows are wrong, and the page renders
  thousands of rows (66,000 px tall on the 3,512-document demo).
- The same cap makes the **Documents tab say "All 1000"** when there are 3,512. That is a separate fix
  (below), but the cause is the same.

## The Summaries tab after the change

```
Summaries                                                     Updated 20:23
● Writing summaries: about 2 h 40 min left at the current pace (estimate)

Indexed documents   Summarised   Waiting   Writing   Could not
247                 211          35        1         0
in 5 collections    with topics  queued    now       see list

Collections
Collection     Indexed   Summarised   Waiting   Could not
coeportal          103           79        24           0
engineering        135          123        11           0
...

Model   Gemma 4 E2B · runs 22–6 · Settings
[ Writing (1) | Could not (0) | Summarised (211) ]   Filter by name / topic
… rows, 100 at a time, "Show more"
```

- **Tiles:** the same component as Index status (`kpi()` in `app.js`), so the two pages look alike.
- **Per-collection table:** one SQL query grouped by collection, using the same `PENDING` rule as
  `enrich.status()` (so the numbers match the worker's), readable collections only.
- **Pace and time left:** the enricher already records `seconds` per document (`enrich.py`). Use the median
  of the last 20 to say "about N left", always marked as an estimate. Show "paused until 22:00" when outside
  the configured hours and "off" when summaries are off.
- **The list:** three sub-tabs (Writing / Could not / Summarised), served by the server with paging (`limit`,
  `offset`, `q` for name or topic), 100 rows at a time. The topic filter keeps working through `q`.

## Server changes

1. `enrich.status_by_collection(conn, model, collections)`: one `GROUP BY d.collection_id` query returning
   indexed / summarised / pending / failed per collection, plus "writing now" from the enricher's state.
2. `GET /api/summaries/status` → tiles, per-collection rows, model, state, hours and pace.
3. `GET /api/summaries?stage=done|failed|writing&q=&limit=100&offset=0` → rows and a total.
4. **Documents tab, same cause:** `GET /api/documents` gets `limit`, `offset`, `status` and `q`, returns
   `{rows, total}`. The tab shows "All 3,512" and loads more on scroll or with "Show more". Keep the 1,000
   default for API clients that do not pass `limit`, and say in the response when the list is cut.

## Tests

- The counts match `enrich.status()` for the same scope; another user's unreadable collections never
  appear in counts or rows.
- Paging: 1,050 documents → first page 100, `total` 1,050; the Documents tab says 1,050.
- The time left is absent when nothing has been summarised yet (no pace to base it on).

## Size

Small: about 1 server function, 2 routes, the shared tile component and a paged list. No migration.
