# Plan: a local model writes answers from the found passages (owner request, 2026-10-24)

Owner request: "plan for implementing a local LLM to answer queries based on what search returns and the
reranker reranks." This is the "second brain" step of the end goal (CLAUDE.md).

**Status: plan, not built.** The numbers quoted below come from `eval/llm_answers.py`
(`eval/results/answers-gemma-4-E2B-report.json`, 4-core CPU). Everything else is unmeasured.

## What was already measured (Gemma 4 E2B, top 5 passages, 2026-10-23)

| | Kubernetes (56 answerable, 16 must refuse) | Fixtures (56 answerable, 40 must refuse) |
|---|---|---|
| Search's evidence check alone: answered / false answers | 89% / 0 | 73% / 3 |
| Model alone decides: answered / false answers | 70% / 0 | 77% / 3 |
| Both must agree: answered / false answers | 61% / 0 | 55% / 1 |
| First passage the model cites is a right one | 61% | 68% |
| Search's own first passage is a right one | 95% | 100% |
| Time per answer (median) | 15 s | 15 s |

What this means for the design:

1. **Search decides whether there is evidence, not the model.** Letting the model veto answers cost 28
   points of answered questions on Kubernetes for no fewer false answers there. The model writes; the
   existing evidence check (and its "no evidence" / "conflicting sources" outcomes) stays the judge.
2. **The model is a worse ranker than search** (61% vs 95%), so it never reorders results. The passages stay
   on screen, in search's order, under the answer.
3. **15 s is too slow to wait for in silence.** The answer streams in word by word while the results are
   already shown, so search itself is never slower.

## How it works

1. Search runs exactly as today (hybrid search → reranker → evidence check).
2. If the outcome is **evidence found** or **conflicting sources**, and answers are on, the page asks for an
   answer. Otherwise (no adequate evidence, index coverage incomplete, no searchable scope, retrieval degraded or
   failed), no answer is written, and the page says why, as it does today.
3. The server sends the top 5 passages of the user's own results to the local model, numbered [1]–[5], each
   with its document title and section. A user can therefore never get an answer built from passages they
   cannot read: the passages come from their permission-filtered search.
4. The model must answer only from those passages and mark every sentence with [n]. If they do not contain
   the answer, it replies `NOT_FOUND`. With conflicting sources, it states both versions, each cited.
5. **Check after writing** (no model involved):
   - remove citations to passage numbers that do not exist;
   - every number, identifier, command and file path in the answer must appear in the passages it cites;
     otherwise the sentence is flagged "not found in the cited passage";
   - an answer left with no valid citation is not shown as an answer: "The model could not write an
     answer supported by these passages", with the passages below.
6. The answer streams to the page (server-sent events), labelled **"Answer written by the local model from
   the passages below; check the sources"**. Each [n] opens that passage. Buttons: Stop and Copy.

## Where it runs

- **The same llama.cpp server the app already runs for summaries** (`enrich.py`), and by default the same
  model (Gemma 4 E2B). No second model in memory, nothing new to install.
- **Answers come before background work:** while an answer is being written, the summary job and the
  indexing worker pause between batches (one shared flag), and continue afterwards. The model server runs at
  the lowest CPU priority for background summaries, so without this pause an answer during heavy indexing
  would be much slower.
- One answer at a time per server. A second user waits in line with "Waiting for the local model…"; on a
  shared server with many users, a GPU or a larger machine is the answer ([plan-gpu.md](plan-gpu.md)).

## Settings (Settings → Answers)

- **Write answers:** off by default, like summaries. Turning it on downloads the model if summaries have not
  already done so.
- **Model:** the summary model by default; any installed model can be picked, measured ones marked.
- **Passages given to the model:** 5 (measured); 3–8.

Nothing is stored: answers are not saved and not shared between users. Logs → Activity shows each request
with timing and tokens, as it does for summaries.

## Not included (and why)

- **An `answer` tool in the MCP server.** I recommend against it: AI assistants already have a stronger
  model and should read the passages themselves (`search`, `read_passage`, `read_document`). A small local model in between
  would only add a weaker step.
- **Follow-up questions in a chat thread.** Later, once single answers are measured useful.
- **Following links or reading whole documents before answering** (agent-style). Later; measure first
  whether link-following search already brings the needed passages.

## Evaluation before it is turned on for anyone

Extend `eval/llm_answers.py` to the shipped flow (evidence check decides, model writes, citation check runs):

- **False answers:** stay what the evidence check gives today (0 of 16 Kubernetes, 3 of 40 fixtures); the
  model must not add any.
- **Supported answers:** every sentence's citation actually supports it. Scored by the citation check
  above, plus a blind manual review of 50 answers on Kubernetes and 50 on the owner's documents
  ([TODO.md](TODO.md) item 12).
- **Answered share:** close to the evidence check's (89% on Kubernetes); the model's `NOT_FOUND` on
  questions that do have evidence is counted and reported.
- **Speed on the owner's laptop:** time to the first word and to the full answer, idle and while indexing.
- **Models:** Gemma 4 E2B (installed), Gemma 4 E4B, Ministral 3 3B and Qwen3.5-4B, all without thinking
  mode. Thinking mode only if a model is clearly better with it (it took about 4× longer for summaries: 78 s against 18 s per request).

## Acceptance

- No answer is written when search found no evidence; no permission leak (test with two users).
- At least 95% of answer sentences supported by their citations (manual review), 0 extra false answers.
- First words within 3 s on the owner's laptop when idle (target, unmeasured); the full answer streams with
  Stop working.
- Search results appear exactly as fast as today with answers on.
- Off by default; turning it off removes every trace from the page.

## Size

Medium: a prompt and citation check (`answers.py`), one streaming route, the shared pause flag, a Settings
section, the answer box in `app.js`, tests against a fake model server (like `tests/test_enrich.py`), and
the evaluation run.
