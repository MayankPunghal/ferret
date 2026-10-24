# Plan: an **Ask** tab, where you ask questions and a local model answers from your documents (owner request, 2026-10-24)

Owner request: "a separate tab, similar to search with a text box but with a back-and-forth, text-based
conversation. Gemma 4 E4B or E2B answers, and behind the model it uses search to get the documents and
answers from the search results." Also: an `ask` tool in the MCP server, as an option for people without a
Claude subscription. This is the "second brain" step of the end goal (CLAUDE.md).

**Status: plan, not built.** The numbers quoted come from `eval/llm_answers.py`
(`eval/results/answers-gemma-4-E2B-report.json`) and the summary-model comparison (HANDOFF §4f, §4k), all
on a 4-core CPU. Everything else is unmeasured.

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

Gemma 4 E4B against E2B on summaries: no better on 9 graded pages, slightly better document types on 14
(10 vs 8, too few to call), **about 2.3× slower** (40.7 s vs 17.4 s per page) and ~8.7 GB of memory.

What this means for the design:

1. **Search decides whether there is evidence, not the model.** Letting the model veto answers cost 28
   points of answered questions on Kubernetes for no fewer false answers there.
2. **The model is a worse ranker than search** (61% vs 95%), so it never reorders the sources.
3. **An answer takes ~15 s with E2B and probably ~35 s with E4B** (estimate from the summary timings), so it
   must stream word by word, with visible steps, and a Stop button.

## The Ask tab

```
Ask                                                        [ New conversation ]

  You   How do I roll back the orders service?

  Ferret   Run `hctl rollback orders-svc` [1]. Rollback restores the previous
           ReplicaSet but does not revert database migrations, so migrations must
           stay backwards compatible for one release [1][2].
           Sources  [1] Orders Service Deployment Runbook › Rollback
                    [2] Orders Service › Deployments
           Searched for "roll back orders service" · Gemma 4 E2B · 14 s   Copy

  You   And what if a migration already ran?

  Ferret   ● Searching… ● Reading 5 passages… ● Writing…           [ Stop ]

┌───────────────────────────────────────────────────────────────┐
│ Ask about your documents…                                  ⏎  │
└───────────────────────────────────────────────────────────────┘
Answers are written by a local model from your documents. Check the sources.
```

- One text box, the conversation above it, **New conversation** to start over. Each [n] and each source opens
  the passage panel (the same one search uses, with Back).
- Each answer shows **what was searched for**, the model and the time, so a wrong answer can be traced to a
  wrong search.
- When search finds no evidence: "I couldn't find this in your documents." plus what was searched and a link
  to the search results for that query. No answer is written.
- With conflicting sources: both versions, each cited, and the "sources disagree" note search already shows.
- Collections: the same scope picker as Search.
- Search stays as it is: no answer box on the Search tab. One place for answers keeps both pages simple.

## How one turn works

Fixed steps run by the app. The model does not choose when or what to search:

1. **Rewrite a follow-up into a stand-alone search** (from the second question on). "And what if a migration
   already ran?" becomes "orders service rollback after database migration ran". The model gets the last 3
   turns and returns one line (~2–4 s). The first question is searched as typed.
2. **Search** with that query, exactly as the Search tab does: hybrid search, reranker, evidence check,
   permission filter for the signed-in user.
3. **Evidence check decides.** Evidence found or conflicting sources → step 4. Anything else → the "couldn't
   find" reply above.
4. **Answer** from the top 5 passages (numbered [1]–[5] with document title and section) and the last 3 turns
   (earlier answers shortened). The model must answer only from the passages, cite every sentence with [n],
   and reply `NOT_FOUND` if they do not contain the answer.
5. **Check, without a model:** remove citations to passages that do not exist; every number, identifier,
   command and file path in the answer must appear in a passage it cites, or that sentence is flagged
   "not in the cited passage"; an answer with no valid citation is replaced by "I couldn't write an answer
   supported by these passages" with the sources listed.

**Why not let the model call search as a tool, agent-style?** Small models choose tools unreliably, and each
extra model step costs 5–15 s on a CPU. The fixed steps give the same "the model uses search" behaviour,
predictably. Later, if measured useful: one extra search when the model replies `NOT_FOUND`.

## Conversations and privacy

- The conversation lives **in the browser tab** (kept until New conversation or sign-out). The browser sends
  the recent turns with each question; the server stores nothing.
- Passages are always fetched again on the server, through the signed-in user's own search, so nobody can
  get an answer built from documents they cannot read.
- Text inside documents is treated as data: the prompt says so, and the model has no tools, so a document
  that contains instructions can at worst change the wording of an answer, which the citation check and
  the visible sources expose.
- Logs → Activity shows each turn (search query, passages used, timings, tokens), like other model calls.
- Later, if wanted: saved conversations per user (a table, deleted with the user).

## Where it runs

- **The llama.cpp server the app already runs for summaries** (`enrich.py`): one model in memory, nothing new
  to install. Its context grows from 8,192 to 16,384 tokens if passages plus history need it (measure).
- **Model:** Gemma 4 E2B by default; E4B selectable for a stronger machine. The default is chosen by the
  evaluation below, not assumed.
- **Questions come before background work:** while an answer is being written, summaries and indexing pause
  between batches (one shared flag) and continue afterwards. The model server runs at the lowest CPU
  priority for background summaries, so without this pause an answer during heavy indexing would be much
  slower.
- **One answer at a time per server;** others see "Waiting for the local model…". Many users at once need a
  GPU or a bigger machine ([plan-gpu.md](plan-gpu.md)).

## Settings (Settings → Ask)

- **Ask tab:** off by default (like summaries); turning it on downloads the model if summaries have not.
- **Model:** the summary model by default; any installed model, measured ones marked.
- **Passages per answer:** 5 (measured); 3–8. **Turns remembered:** 3.

## MCP: an optional `ask` tool

For assistants that run a small or local model, or for people without a Claude subscription who connect a
free client. It runs the same steps as one Ask turn and returns the answer, the citations and the sources.

- Off unless enabled in Settings, read-only tokens may use it.
- Its description tells capable assistants to prefer `search` + `read_passage` and write their own answer:
  a strong model reading the passages does better than relaying a small model's answer.

## Phases

1. **Ask tab, single questions:** steps 2–5, streaming, Stop, sources, Settings, Activity entries. Tests
   against a fake model server (like `tests/test_enrich.py`), including two users with different access.
2. **Follow-up questions:** step 1 (rewrite), the history window, New conversation.
3. **MCP `ask` tool,** reusing phase 1–2 code.
4. Only if measured useful: a second search on `NOT_FOUND`; saved conversations; document summaries as
   extra context.

## Evaluation before it is turned on for anyone

Extend `eval/llm_answers.py` to the shipped steps, on Kubernetes, the fixtures and the owner's documents
([TODO.md](TODO.md) item 12):

- **False answers:** no more than the evidence check gives today (0 of 16 Kubernetes, 3 of 40 fixtures).
- **Supported answers:** share of sentences whose citations support them: citation check plus a blind manual
  review of 50 answers per corpus.
- **Follow-ups:** 20 short labelled conversations (2–3 turns each, Kubernetes): does the rewritten query find
  the labelled page (compare with searching the raw follow-up)?
- **Models:** Gemma 4 E2B and E4B (owner's choice), plus Ministral 3 3B and Qwen3.5-4B, without thinking
  mode (it took about 4× longer for summaries).
- **Speed on the owner's laptop:** time to the first word and to the full answer, idle and while indexing.

## Acceptance

- No answer when search found no evidence; no permission leak (test with two users).
- At least 95% of answer sentences supported by their citations (manual review); no extra false answers.
- Follow-up rewriting finds the labelled page at least as often as the raw follow-up does.
- First words within ~5 s on the owner's laptop with E2B when idle (target, unmeasured); Stop works.
- Search is exactly as fast as before; with the Ask tab off, nothing of it shows.

## Size

Medium: `answers.py` (rewrite, prompt, citation check), one streaming route, the shared pause flag, the Ask
tab in `index.html`/`app.js`, a Settings section, the MCP tool, tests and the evaluation run.
