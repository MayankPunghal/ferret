# Plan: **Ask**, a chat window where a local model answers from your documents (owner request, 2026-10-24)

Owner request, in three steps on 2026-10-24: (1) a local model answers from what search finds and the
reranker ranks; (2) a separate tab, a text box with back-and-forth conversation, Gemma 4 E4B or E2B, search
behind the model; an MCP `ask` tool for people without a Claude subscription; (3) **the goal: a proper
conversation window like Claude Code, OpenCode or the DeepSeek harness, with picture upload, thinking,
to-and-fro chat, and every conversation saved.** This is the "second brain" step of the end goal (CLAUDE.md).

**Status: plan, not built.** Numbers quoted come from `eval/llm_answers.py`
(`eval/results/answers-gemma-4-E2B-report.json`), the summary-model comparison and the vision check
(HANDOFF §4f, §4k), all on a 4-core CPU. Everything else is unmeasured.

## What was already measured

Gemma 4 E2B answering from search's top 5 passages (2026-10-23):

| | Kubernetes (56 answerable, 16 must refuse) | Fixtures (56 answerable, 40 must refuse) |
|---|---|---|
| Search's evidence check alone: answered / false answers | 89% / 0 | 73% / 3 |
| Model alone decides: answered / false answers | 70% / 0 | 77% / 3 |
| Both must agree: answered / false answers | 61% / 0 | 55% / 1 |
| First passage the model cites is a right one | 61% | 68% |
| Search's own first passage is a right one | 95% | 100% |
| Time per answer (median) | 15 s | 15 s |

- **Gemma 4 E4B vs E2B** (summaries): no better on 9 graded pages, document types 10 vs 8 of 14 (too few to
  call), **about 2.3× slower** (40.7 s vs 17.4 s per page), ~8.7 GB of memory.
- **Thinking mode** (E2B, summaries): about **4× slower** (78 s vs 18 s per request) for at most a page or two
  better; not offered for summaries.
- **Vision** (E2B + its image projector): text inside images read accurately (labels, `ERR-PAY-4012`, a
  Settings screenshot), **20–56 s per image**; arrows and relations in diagrams about half wrong. The
  GLM-4.6V-Flash comparison is unfinished ([TODO.md](TODO.md) item 2).

Design rules that follow:

1. **Search decides whether there is evidence, not the model.** Letting the model veto cost 28 points of
   answered questions on Kubernetes for no fewer false answers there.
2. **The model never reorders sources** (it is a worse ranker: 61% vs 95%).
3. **Everything streams and every step is visible:** an answer takes ~15 s with E2B, about 2.3× that with
   E4B, about 4× with thinking, plus 20–56 s per picture.

## The window

```
┌──────────────────────┬─────────────────────────────────────────────────────────────┐
│ + New conversation   │  Orders rollback after a migration           Gemma 4 E2B ▾  │
│ 🔍 Search chats       │                                                             │
│ Today                │  You   How do I roll back the orders service?               │
│ ● Orders rollback…   │                                                             │
│   Payment error 4012 │  Ferret ▸ Searched "roll back orders service" · 5 passages  │
│ Yesterday            │         Run `hctl rollback orders-svc` [1]. Rollback        │
│   VPN setup (image)  │         restores the previous ReplicaSet but does not       │
│   …                  │         revert database migrations [1][2].                  │
│                      │         Sources [1] Deployment Runbook › Rollback           │
│                      │                 [2] Orders Service › Deployments            │
│                      │         14 s · 1,840 tokens   Copy  Retry                   │
│                      │                                                             │
│                      │  You   [screenshot.png]  What does this error mean?         │
│                      │                                                             │
│                      │  Ferret ▸ Read the picture (31 s): "ERR-PAY-4012 card       │
│                      │           declined by issuer"                               │
│                      │         ▸ Thinking (collapsed)                              │
│                      │         ● Writing…                               [ Stop ]   │
│                      │ ┌─────────────────────────────────────────────────────────┐ │
│                      │ │ Ask about your documents…                               │ │
│                      │ │ 📎 Picture   💭 Think first   Scope: All collections ▾   ⏎ │ │
│                      │ └─────────────────────────────────────────────────────────┘ │
└──────────────────────┴─────────────────────────────────────────────────────────────┘
```

**Conversation list (left):** New conversation; every conversation saved and listed by date; a title written
by the model after the first answer (editable); search over titles and messages; rename, delete; collapses
on a phone like the main navigation.

**Messages:**
- Answers rendered as Markdown (lists, tables, code blocks with Copy), streamed word by word.
- **Step cards, as in Claude Code or OpenCode:** each step the app ran is a small collapsible line above
  the answer: "Read the picture", "Searched '…' · 5 passages", "Thinking". Opening one shows the details:
  the query, the passages with their scores, the picture's text, the thinking.
- **Sources** under each answer; every [n] and every source opens the passage panel search uses (with Back).
- Footer per answer: time, tokens, model. Actions: Copy, **Retry** (write again, optionally with another
  model or thinking on), and on your own message **Edit** (re-runs from there; the old branch is replaced).
- "I couldn't find this in your documents" when search finds no evidence, with what was searched and a link
  to the search results. With conflicting sources: both versions, each cited.

**Composer:** multi-line box (Enter sends, Shift+Enter new line); **📎 Picture** (button, paste or drag and
drop; PNG/JPEG/WebP up to 10 MB, up to 4 per message, thumbnails before sending); **💭 Think first**
(per message; slower, the thinking is shown collapsed); **scope** picker (collections, as on Search); the
**model** picker in the header (any installed model, measured ones marked). **Stop** while writing.

**Export:** a conversation as Markdown (with sources as links), from its menu.

The Search tab stays as it is. The DeepSeek harness's request/response detail already exists for admins in
Logs → Activity; each chat turn appears there too.

## How one turn works

Fixed steps run by the app; each is shown as a step card:

1. **Read pictures** (if any): the vision model writes the picture's text and a one-line description
   (20–56 s each, measured with E2B). That text is saved with the message.
2. **Make a search query:** the first question is searched as typed (plus the picture's text if any).
   From the second turn on, the model rewrites the question with the recent turns into one stand-alone
   query ("and what if a migration already ran?" → "orders service rollback after a database migration ran").
3. **Search** exactly as the Search tab does (hybrid, reranker, evidence check), with the signed-in user's
   permissions and the chosen scope.
4. **Evidence check decides:** evidence found or conflicting → step 5; otherwise the "couldn't find" reply.
5. **Answer** from the top 5 passages ([1]–[5], with document title and section), the pictures (if the model
   can see) and the conversation so far (see Long conversations). With **Think first**, the model reasons
   before answering and the reasoning streams into the collapsed Thinking card. The model must use only the
   passages for facts, cite every sentence with [n], and say `NOT_FOUND` if they do not contain the answer.
6. **Check, without a model:** drop citations to passages that do not exist; every number, identifier,
   command and file path in the answer must appear in a passage it cites, or that sentence is marked "not in
   the cited passage"; an answer left with no valid citation becomes "I couldn't write an answer supported by
   these passages", with the sources.

Questions about the conversation itself ("summarise what we found", "shorter please") skip steps 2–4 and
answer from the earlier turns and their sources, cited the same way.

**Why fixed steps, not the model calling tools freely (yet)?** Small models choose tools unreliably, and each
extra model step costs 5–15 s on a CPU. Fixed steps give the same visible "searched → read → answered" flow,
predictably. **Agent mode** (the model may call `search`, `read_passage`, `document_links` itself, up to a few
steps, each shown as a step card) is phase 6, only if it answers multi-hop questions measurably better.

## Long conversations

- The model sees the last turns in full while they fit its context (16,384 tokens, raised from the summary
  server's 8,192 if measured necessary); older turns are replaced by a short running summary the model
  writes (shown as a "Earlier messages summarised" line, like compaction in Claude Code).
- Sources of earlier turns are not re-sent; each new question searches again.

## Saved history and privacy

- **Saved on the server, per user,** in two tables: `conversations` (user, title, scope, model, created,
  updated) and `messages` (role, text, thinking, step details, citations as passage references, picture
  references, tokens, timings). Pictures go to the existing blob store. One migration.
- **Only the owner of a conversation can open it.** Admins see counts, not content (the Database tab hides
  these tables' text). Deleting a conversation deletes its pictures; deleting a user deletes their
  conversations. Backups include them (same database and blob folder).
- **Access changes are respected:** sources are re-checked when a saved conversation is opened; a source the
  user can no longer read shows as "no longer available to you", and its answer is hidden with that note.
- **Retention setting** (admin): keep for ever (default) or delete conversations older than N days.
- Text inside documents and pictures is treated as data: the prompt says so, the model has no tools (until
  agent mode), and citations plus visible sources expose a bad answer.

## Where it runs

- **The llama.cpp server the app already runs for summaries** (`enrich.py`), with the model's image
  projector loaded when pictures are on: one model in memory, nothing new to install.
- **Model:** Gemma 4 E2B by default; E4B selectable per conversation for stronger machines. The default is
  set by the evaluation below. Pictures need a vision model (Gemma 4 E2B's was checked; E4B is described as
  multimodal but was not checked; GLM-4.6V-Flash after its comparison).
- **Chat comes before background work:** while a turn runs, summaries and indexing pause between batches
  (one shared flag). The model server runs at the lowest CPU priority for background summaries, so without
  this pause a turn during heavy indexing would be much slower.
- **One turn at a time per server;** others see "Waiting for the local model…" with their place in line.
  Many users at once need a GPU or a bigger machine ([plan-gpu.md](plan-gpu.md)).

## Settings (Settings → Ask)

Ask tab on/off (off by default; turning it on downloads the model if summaries have not) · default model ·
pictures on/off · Think first allowed · passages per answer (5; 3–8) · retention.

## MCP: an optional `ask` tool

For assistants running a small or local model, or people without a Claude subscription using a free client.
It runs one turn (steps 2–6, no pictures) and returns the answer, citations and sources; it can continue a
saved conversation by id. Off unless enabled in Settings. Its description tells capable assistants to prefer
`search` + `read_passage` and write their own answer.

## Phases

1. **Chat window with saved history:** conversation list, steps 3–6, streaming, step cards, sources, citation
   check, Stop, Copy, Settings, Activity entries; migration for the two tables. Tests against a fake model
   server (like `tests/test_enrich.py`), including two users and a revoked grant.
2. **Real conversation:** step 2 (rewriting follow-ups), long-conversation summary, Retry, Edit, model
   titles, search over chats, export, retention.
3. **Think first** (per message, collapsed thinking).
4. **Pictures** (after the vision comparison, item 2 in TODO).
5. **MCP `ask` tool.**
6. Only if measured useful: **agent mode** (model calls the tools itself), a second search on `NOT_FOUND`,
   document summaries as extra context.

## Evaluation before it is turned on for anyone

Extend `eval/llm_answers.py` to the shipped steps, on Kubernetes, the fixtures and the owner's documents
([TODO.md](TODO.md) item 12):

- **False answers:** no more than the evidence check gives today (0 of 16 Kubernetes, 3 of 40 fixtures).
- **Supported answers:** share of sentences whose citations support them; citation check plus a blind
  manual review of 50 answers per corpus.
- **Follow-ups:** 20 labelled conversations of 2–3 turns (Kubernetes): the rewritten query finds the labelled
  page at least as often as the raw follow-up.
- **Thinking:** the same 50 questions with and without; offered only if answers are measurably better.
- **Pictures:** 20 screenshots of errors and settings screens with a labelled page each: does the picture's
  text lead search to it?
- **Models:** Gemma 4 E2B and E4B (owner's choice), plus Ministral 3 3B and Qwen3.5-4B for text-only.
- **Speed on the owner's laptop:** time to the first word and to the full answer, idle and while indexing.

## Acceptance

- No answer when search found no evidence; no permission leak, including after a grant is revoked (tests).
- At least 95% of answer sentences supported by their citations (manual review); no extra false answers.
- Conversations survive a restart and a backup/restore; only their owner can open them.
- First words within ~5 s with E2B on the owner's laptop when idle (target, unmeasured); Stop works.
- Search is exactly as fast as before; with Ask off, nothing of it shows.

## Size

Large, in the phases above. Phase 1 alone: `answers.py` (prompt, citation check), a streaming route,
conversation routes, one migration, the pause flag, the Ask tab in `index.html`/`app.js`, Settings, tests.
