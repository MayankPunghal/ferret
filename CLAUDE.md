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

## Working conventions
- Python 3.13, `uv` for env/deps (versions pinned in `pyproject.toml` / `uv.lock`).
- Tests need PostgreSQL+pgvector: `docker compose up -d db` then `uv run pytest`.
- Read `docs/HANDOFF.md` first when resuming.
