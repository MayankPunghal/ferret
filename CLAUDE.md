# Project memory / instructions for Claude

## Standing instructions from the owner (Mayank)
- The originating cloud session had a hard budget (~$100 credit). Before the budget is exhausted the
  session must: commit work in logical commits with proper messages, push to the owner's GitHub repo,
  and write `docs/HANDOFF.md` covering explored and unexplored paths, decisions, POCs and research.
  Future work continues in a **local** Claude Code session (Claude Pro), not cloud sessions.
- Authoritative scope: `docs/brief/final_enterprise_retrieval_research_and_master_prompt.md`, section 11
  (execution gates + first-release acceptance criteria). Sections 1–10 are references, not a checklist.
- Never claim a gate/test passes without running it. Mark unmeasured numbers as unmeasured.
- No external AI calls unless explicitly opted in. No GraphRAG / generation / plugin system / agent
  harness before Gates 1–2 pass.

- **Do not over-engineer.** If something can be achieved in a simpler way, the simpler/easier way is
  the best way — as long as the quality of the outcome is not compromised. Prefer deleting or
  simplifying code over adding abstractions, services or infrastructure.

## End goal (owner, standing)
A **production-grade, offline-first knowledge search (and later "second brain" Q&A) for technical/IT
people** — PMs, BAs, developers, DBAs, DevOps; codebase docs, runbooks, business workflows. Must be:
one-click install for a non-technical user on Windows/macOS/Linux, no external AI provider required,
trustworthy (cites real passages, abstains instead of guessing, enforces permissions), and polished.
Indexing speed may be traded for accuracy (background jobs); retrieval of 1–6 s with a visible loader is
acceptable. Graph-style navigation of heavily interlinked technical docs (Graphify-like wikis:
relationships, blast radius, from→to links) is a priority research direction.

**Work like the lead architect:** keep going until satisfied with quality, look and functionality; audit
your own work periodically against this goal and the brief's gates (re-read this file, run the tests and
the UI walkthrough, check docs match behaviour); make decisions autonomously; keep the handoff current.

## Working conventions
- Python 3.13, `uv` for env/deps (versions pinned in `pyproject.toml` / `uv.lock`).
- Tests start their own embedded PostgreSQL: `uv run pytest -q` (no Docker). Model cache: `data/models`
  (`uv run knowledge-search download-model` once).
- Read `docs/HANDOFF.md` first when resuming.
