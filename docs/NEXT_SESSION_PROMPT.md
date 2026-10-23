# Prompt to continue in a new Claude Code session

Copy everything below the line into a new (local) Claude Code session opened in this repository.

---

You are continuing work on **Knowledge Search** (this repository): an offline-first, permission-aware
knowledge search for technical/IT teams. Before doing anything:

1. Read `CLAUDE.md` (owner's standing instructions and end goal — follow them exactly), then
   `docs/HANDOFF.md` (verified vs unverified state, decisions, explored/unexplored paths) and
   `docs/research-graph.md` (graph navigation design + what v1 implements).
2. Verify the baseline: `uv sync && uv run knowledge-search download-model && uv run pytest -q`
   (expect 39 passed; tests start their own embedded PostgreSQL, no Docker). If anything fails, fix that
   first and report it.

Then continue the graph-navigation track, in this order, one increment at a time, each with tests,
a short note in `docs/research-graph.md`, and a commit:

1. **Evaluation first.** Build a small interlinked technical corpus (e.g. generate a Markdown wiki for a
   public repository with the owner's codebase-documenter skill from https://github.com/MayankPunghal/Skills,
   or extend `fixtures/corpus/platform-wiki/`) and ~40 labelled questions in classes: single-hop lookup,
   multi-hop (2–3 links), blast radius ("what is affected if X changes"), from→to/ownership, workflow
   walk-through, unanswerable. Label the *set* of passages needed. Add an eval script that reports
   evidence-set recall@k for: today's hybrid search vs. hybrid + 1-hop link expansion.
2. **Link expansion in ranking** behind an admin setting (default off): after fusion, add 1-hop linked
   passages of the top results as candidates (badge "linked from …"), then rerank/gate as usual. Turn it on
   by default only if the evaluation shows a gain on multi-hop questions without hurting single-hop.
3. **Shared-identifier edges** (documents that share rare identifiers such as service names or error
   codes; `chunks.idents` already exists) as an extra, lower-weight edge kind — again only if measured useful.
4. **Agent tools**: expose `search`, `get_passage`, `get_links`, `impact` for agents (REST is there; add an
   authenticated MCP server only if simple), always permission-filtered.

Rules: keep it simple (no new database or service), never claim results you did not run, mark unmeasured
numbers as unmeasured, run `uv run pytest -q` and `scripts/ui_check.py` (needs a running demo server, see
`docs/development.md`) before each commit, keep `docs/HANDOFF.md` current, commit and push with clear
messages. Ask the owner (Mayank) only if a decision is genuinely theirs.
