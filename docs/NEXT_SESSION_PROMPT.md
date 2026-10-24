# Prompt to continue in a new Claude Code session

Copy everything below the line into a new (local) Claude Code session opened in this repository
(`git clone https://github.com/MayankPunghal/ferret`, or `git pull` in an existing clone).

---

You are continuing work on **Ferret** (this repository, formerly "knowledge-search"): an offline-first,
permission-aware knowledge search for technical/IT teams, with **Ferret Crawler** (`site2md/`) to copy
websites and intranets into it. Before doing anything:

1. Read `CLAUDE.md` (the owner's standing instructions and end goal; follow them exactly), then
   `docs/TODO.md` (**the current list of open work**, audited against every owner request) and
   `docs/HANDOFF.md` (what is verified vs not, decisions, measured results, gotchas).
2. Check the baseline: `uv sync && uv run ferret download-model && uv run pytest -q`
   (expect 131 passed; tests start their own embedded PostgreSQL, no Docker). If anything fails, fix that
   first and report it.
3. Check `eval/results/` for `images-glm-4.6v-flash-part2.json` and `images-gemma-4-e2b.json` (the vision
   comparison was still running when the cloud session ended). If they are missing, TODO item 2 says how
   to re-run it.

Then work through `docs/TODO.md` in its order, unless the owner picks something else:

- **Fix first:** the documents list stops at 1,000 rows (TODO item 1; the fix is in
  `docs/plan-summaries-status.md`, together with the Summaries-tab counts, item 9).
- **Measure on the owner's laptop:** the parallel-embedding benchmark (item 3), then build
  `docs/plan-large-documents.md` step 1 only if the gain holds and the vectors stay identical.
- **Plans ready to build:** GPU switch (`plan-gpu.md`; the owner has an Intel iGPU), installer and release
  (`plan-installer.md`, `plan-release.md`), Docker (`plan-docker.md`), invite links (`plan-invites.md`),
  the Ask tab, answers by the local model (`plan-answers.md`),
  images/vision (`plan-images-and-models.md`, after the vision result).

Rules: keep it simple (no new database or service), never claim results you did not run, mark unmeasured
numbers as unmeasured, run `uv run pytest -q` before each commit (and the UI walkthrough in
`docs/development.md` for UI changes), keep `docs/TODO.md` and `docs/HANDOFF.md` current, commit and push
with clear messages. Say plainly when you disagree with the owner. Ask the owner (Mayank) only when a
decision is genuinely theirs.
