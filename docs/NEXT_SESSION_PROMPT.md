# Prompt to continue in a new Claude Code session

Copy everything below the line into a new (local) Claude Code session opened in this repository
(`git clone https://github.com/MayankPunghal/ferret`, or `git pull` in an existing clone).

---

You are continuing work on **Ferret** (this repository, formerly "knowledge-search"): an offline-first,
permission-aware knowledge search for technical/IT teams, with **Ferret Crawler** (`site2md/`) to copy
websites and intranets into it. The previous (cloud) session ended on 2026-10-25 with everything committed
and pushed. Before changing anything:

1. **Read the instructions and state, in this order:**
   - `CLAUDE.md`: the owner's standing instructions and end goal. Follow them exactly.
   - `docs/TODO.md`: **only open work**, audited against every owner request.
   - `docs/PLANS.md`: the catalogue of written plans not yet built, with size, dependencies and suggested
     order. Each row links to its `docs/plan-*.md`.
   - `docs/HANDOFF.md`: what is built and verified, measured results (§4), decisions and answered questions
     (§5; do not re-open these without new data), explored vs unexplored (§6), and gotchas (§8).
2. **Map the repository** before editing, and keep the map short:
   - `src/knowledge_search/`: the app (FastAPI `api.py`, parsing/convert, chunking, search and evidence
     gate, enrichment/summaries with llama.cpp, MCP server, `static/` UI).
   - `site2md/`: Ferret Crawler; its HTML→Markdown converter `site2md/src/site2md/html.py` is shared with
     Ferret's HTML parsing.
   - `eval/` (scripts and `eval/results/`), `tests/`, `scripts/` (e.g. `ui_check.py`), `docs/`
     (`architecture.md`, `evaluation.md`, `development.md` with the UI walkthrough).
   - Confirm the files and functions each plan names still exist where the plan says. Note any mismatch in
     the plan before building it.
3. **Check the baseline:** `uv sync && uv run ferret download-model && uv run pytest -q` (expect 132 passed;
   tests start their own embedded PostgreSQL, no Docker). If anything fails, fix that first and report it.
   (The command-line download is only for the tests; users download every model from the app: Settings →
   Download, and the search models download by themselves on first start. Keep it that way: no feature may
   need a terminal.)

Then work through `docs/PLANS.md` in its order (it matches `docs/TODO.md`), unless the owner picks something
else:

1. `plan-summaries-status.md`: fixes the documents list stopping at 1,000 rows (TODO 1) and adds the
   Summaries counts (TODO 9).
2. `plan-tables.md`: tables from crawled pages, Word and PowerPoint kept as real tables; passages never cut
   mid-table (TODO 13). Step 5 is a measurement; only keep it if it is clearly better.
3. `plan-large-documents.md` steps 2–3 (step 1 was measured: no gain on 4 cores).
4. `plan-gpu.md` (the owner has an Intel iGPU; measure on their laptop).
5. Release path: `plan-release.md` (CI first), `plan-docker.md`, `plan-installer.md`. The package name
   needs the owner first (TODO 24).
6. `plan-invites.md`.
7. `plan-answers.md`: the Ask chat window, in six phases.
8. `plan-images-and-models.md`: on hold until the Gemma 4 E2B vision run (TODO 2 has the commands) and the
   owner's go-ahead.

**After each item:**
- Remove it from `docs/TODO.md` and update its row in `docs/PLANS.md`.
- Record what was built and measured in `docs/HANDOFF.md`.
- Run `uv run pytest -q` (and the UI walkthrough in `docs/development.md` for UI changes).
- Commit with a clear message and push.

**Rules:**
- Keep it simple: no new database or service.
- Never claim a result you did not run, and mark unmeasured numbers as unmeasured.
- No external AI calls; local models only.
- Say plainly when you disagree with the owner.
- Ask the owner (Mayank) only when a decision is genuinely theirs.
