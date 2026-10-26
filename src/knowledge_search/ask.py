"""Ask: a chat that answers in words with the local language model, from the passages search found.

Search runs first with the caller's permissions; only passages that passed the evidence gate are numbered and
go to the model, and the answer must cite them ([1], [2]). No supported passage means no model call at all: the
search's own "not found" message is the answer, so the chat cannot invent one. The model answers plain text
(Markdown is allowed) and replies `NOT_IN_DOCUMENTS` when the passages do not answer the question, which the
window shows as "No answer from your documents".

A conversation is saved per user (ask_conversations / ask_messages, migration 0011): its earlier turns travel
with each question so follow-ups read naturally, marked in the prompt as context that is never evidence.
Answers stream token by token (`/api/ask/stream`, server-sent events); `/api/ask` returns the whole answer for
assistants and tests. "Think first" turns on the model's reasoning, streamed into a collapsed card.

The model is the one the app already runs for document summaries (enrich.py) unless another one is chosen, in
which case ask.py runs its own llama-server for it, one model at a time. Nothing leaves the computer.
"""

import json
import logging
import os
import threading
import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import db, enrich, runtime
from .deps import Conn, User, state
from .search import search

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["ask"])

PASSAGES = 8  # passages handed to the model: enough to answer, short enough for a small model's context
CHARS_PER_PASSAGE = 1500
TURNS = 6  # earlier question/answer pairs kept for follow-ups
CHARS_PER_TURN = 700
CHARS_PER_ANSWER = 4000  # an earlier answer as history is cut, so a long one cannot fill the prompt
PLAN_TURNS = 12  # the query planner sees more of the conversation than the answerer does
PLAN_CHARS = 1200
NOT_IN_DOCUMENTS = "NOT_IN_DOCUMENTS"

PROMPT = """You answer questions about the user's own documents, using ONLY the numbered passages below.
Cite every passage you use as [1] or [2]. If the passages do not answer the question, reply with exactly
NOT_IN_DOCUMENTS and nothing else: a guess is worse than no answer. Keep commands, error codes, names, numbers
and versions exactly as written. Be concise: a few sentences or a short list. Markdown is allowed.
{closest}{history}
Question: {question}

Passages:
{passages}
"""

CLOSEST = """Passages marked "closest match, not confirmed" did not pass the evidence check. Read them, and use
them only if they really answer the question; say which of them you used. If the best of them is only loosely
related, reply with exactly NOT_IN_DOCUMENTS instead of guessing.
"""


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=8000)


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    conversation_id: uuid.UUID | None = None
    history: list[Turn] = Field(default_factory=list, max_length=20)
    model: str = ""  # an enrich.LLMS name; "" = the model the summaries use
    think: bool = False
    # answer from the closest matches when no passage passed the evidence check (the user asked for it; the
    # answer says so and the window labels it "unconfirmed")
    allow_unsupported: bool = False  # else the setting decides (runtime.ask_allow_unsupported)
    new_turn: bool = True  # False for a Retry: the question is already in the conversation
    collection_ids: list[int] | None = None


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


# --- prompt ---------------------------------------------------------------------------------------------------


def _history_text(history: list[Turn], limit: int | None = None, chars: int | None = None) -> str:
    """The last turns, as context for what the question refers to (never as evidence)."""
    turns = [t for t in history if t.text.strip()][-(limit or TURNS * 2):]
    if not turns:
        return ""
    lines = []
    for t in turns:
        text = t.text.strip()
        text = text[: (chars or CHARS_PER_TURN) if t.role == "user" else (chars or CHARS_PER_ANSWER)]
        lines.append(f"{'User' if t.role == 'user' else 'You'}: {text}")
    return ("\nThe conversation so far, for what the question refers to (it is not evidence; cite only the "
            "passages):\n" + "\n".join(lines) + "\n")


def build_prompt(question: str, results: list, history: list[Turn] | None = None, unconfirmed: bool = False) -> str:
    """The prompt for one question. Separate, so a test can read it without a model."""
    passages = "\n\n".join(
        f"[{n}] {r.clearer_title or r.title}"
        f"{' > ' + r.heading_path if r.heading_path else ''}{f' (page {r.page})' if r.page else ''}"
        f"{' (closest match, not confirmed)' if getattr(r, 'closest', False) else ''}\n"
        f"{r.text[:CHARS_PER_PASSAGE]}"
        for n, r in enumerate(results, start=1))
    any_closest = unconfirmed or any(getattr(r, "closest", False) for r in results)
    return PROMPT.format(closest=CLOSEST if any_closest else "", history=_history_text(history or []),
                         question=question.strip(), passages=passages)


# --- the model -------------------------------------------------------------------------------------------------


class _AnswerModel:
    """The local model that writes answers: the summaries' server when it already runs that model (one model in
    memory on a laptop), otherwise a server of its own, and only one at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.server: enrich.LocalServer | None = None
        self.file = ""  # the .gguf this server holds
        self.broken = ""

    def serving(self, chosen: str, device: str) -> tuple[str, str]:
        """(url, model) for the chosen enrich.LLMS name ("" = the summaries' model). Starts a server if needed."""
        s = runtime.base()
        e = getattr(state, "enricher", None)
        if s.llm_url:  # answers come from the OpenAI-compatible server KS_LLM_URL names
            return s.llm_url, getattr(e, "model", "")
        if chosen:
            file = enrich.LLMS[chosen][1]
        else:  # the model the app already runs for summaries
            file = getattr(e, "model", "") or ""
            if not file:
                found = enrich.installed(s)
                file = found[1].name if found else ""
        if not file:
            raise HTTPException(409, "Ask needs a local model: download one in Settings → Models (Gemma 4 E2B "
                                     "is the default).")
        if e and e.server and e.server.model.name == file:  # share the summaries' server: one model in memory
            if not e._ensure_server():
                raise HTTPException(503, e.broken or "the local language model could not start")
            return e.url, file
        found = enrich.installed(s, file)
        if not found:
            raise HTTPException(409, f"the model {file} is not downloaded yet: download it in Settings → Models.")
        if self.server and self.file == file and self.server.proc and self.server.proc.poll() is None:
            return self.server.url, file
        with self.lock:
            if self.server:
                self.server.stop()
                self.server = None
            threads = s.llm_threads or max(1, (os.cpu_count() or 2) // 2)
            server = enrich.LocalServer(found[0], found[1], threads, Path(s.data_dir) / "logs")
            if device == "gpu":
                try:
                    server.gpu_exe = enrich.gpu_exe(s)
                except Exception as exc:  # noqa: BLE001 - the CPU build carries on
                    log.warning("Ask: GPU build unavailable (%s), using the CPU", exc)
            try:
                server.start()
            except (OSError, enrich.LLMError) as exc:
                self.broken = f"the local language model could not start ({exc})"[:300]
                raise HTTPException(503, self.broken) from exc
            self.server, self.file = server, file
            return server.url, file


_models = _AnswerModel()


def _stream_model(url: str, model: str, prompt: str, think: bool, max_tokens: int = 900) -> Iterator[tuple[str, str]]:
    """("thinking"|"text", piece) from the server's streaming reply, as it is written."""
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0,
            "stream": True, "chat_template_kwargs": {"enable_thinking": bool(think)}}
    if model:
        body["model"] = model
    try:
        with httpx.Client(timeout=httpx.Timeout(600.0, connect=15.0)) as client, \
                client.stream("POST", url.rstrip("/") + "/chat/completions", json=body) as r:
            if r.status_code >= 400:
                raise enrich.LLMError(f"the model server answered HTTP {r.status_code}")
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    thought = delta.get("reasoning_content") or delta.get("reasoning")
                    if thought:
                        yield "thinking", thought
                    if delta.get("content"):
                        yield "text", delta["content"]
    except httpx.HTTPError as exc:
        raise enrich.LLMError(str(exc)) from exc


class Answer:
    """One answer as it is written: its text, its thinking, and whether the model found nothing in the passages."""

    def __init__(self) -> None:
        self.text = ""
        self.thinking = ""
        self.abstained = False
        self.seconds = 0.0
        self.model = ""

    def pieces(self, pieces: Iterator[tuple[str, str]]) -> Iterator[tuple[str, str]]:
        """Relay the model's pieces, holding the first characters back until we know it is not the sentinel."""
        hold = ""
        decided = False
        started = time.perf_counter()
        for kind, piece in pieces:
            if kind == "thinking":
                self.thinking += piece
                yield kind, piece
                continue
            if decided:
                self.text += piece
                yield kind, piece
                continue
            hold += piece
            head = hold.lstrip().upper()
            if head.startswith(NOT_IN_DOCUMENTS):  # decided: the model found nothing to answer from
                self.abstained, decided, hold = True, True, ""
                continue
            if len(head) < len(NOT_IN_DOCUMENTS):  # could still become the sentinel: keep holding
                continue
            decided = True
            self.text = hold
            yield "text", hold
            hold = ""
        if not decided and hold:
            decided = True
            if NOT_IN_DOCUMENTS in hold.upper():
                self.abstained = True
            else:
                self.text = hold
                yield "text", hold
        if self.abstained or not self.text.strip():
            self.abstained, self.text = True, ""
        self.seconds = round(time.perf_counter() - started, 1)


# --- conversations --------------------------------------------------------------------------------------------


def _owned(conn, user_id: int, cid: uuid.UUID):
    row = conn.execute("SELECT id, title FROM ask_conversations WHERE id = %s AND user_id = %s",
                       (cid, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "conversation not found")
    return row


def _turns(conn, cid: uuid.UUID) -> list[Turn]:
    rows = conn.execute("SELECT role, text FROM ask_messages WHERE conversation_id = %s ORDER BY id DESC LIMIT %s",
                        (cid, TURNS * 2)).fetchall()
    return [Turn(role=r["role"], text=r["text"] or "") for r in reversed(rows)]


def _save_message(conn, cid: uuid.UUID, role: str, text: str, *, citations=None, thinking=None, outcome=None,
                  model=None, seconds=None, search=None) -> int:
    if role == "user":  # the same question twice in a row is one turn (a double submit, or an aborted send)
        last = conn.execute("SELECT id, role, text FROM ask_messages WHERE conversation_id = %s "
                            "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        if last and last["role"] == "user" and (last["text"] or "").strip() == text.strip():
            return last["id"]
    row = conn.execute(
        "INSERT INTO ask_messages (conversation_id, role, text, citations, thinking, outcome, model, seconds, search) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (cid, role, text, json.dumps(citations) if citations is not None else None, thinking or None, outcome,
         model, seconds, json.dumps(search) if search is not None else None)).fetchone()
    if role == "user":  # the first question names the conversation, until it is renamed
        conn.execute("UPDATE ask_conversations SET title = CASE WHEN title = '' THEN %s ELSE title END, "
                     "updated_at = now() WHERE id = %s", (text[:80], cid))
    else:
        conn.execute("UPDATE ask_conversations SET updated_at = now() WHERE id = %s", (cid,))
    conn.commit()
    return row["id"]


@router.get("/ask/conversations")
def conversations(p: User, conn: Conn, q: str = "") -> dict:
    """The caller's conversations, newest first; `q` searches titles and what was said."""
    like = f"%{q.strip()}%"
    rows = conn.execute(
        "SELECT c.id, c.title, c.updated_at, "
        " (SELECT count(*) FROM ask_messages m WHERE m.conversation_id = c.id) AS turns "
        "FROM ask_conversations c WHERE c.user_id = %s AND (%s = '' OR c.title ILIKE %s OR EXISTS ("
        "  SELECT 1 FROM ask_messages m WHERE m.conversation_id = c.id AND m.text ILIKE %s)) "
        "ORDER BY c.updated_at DESC LIMIT 200", (p.user_id, q.strip(), like, like)).fetchall()
    return {"conversations": [{"id": str(r["id"]), "title": r["title"] or "Untitled",
                               "updated_at": r["updated_at"].isoformat(), "turns": r["turns"]} for r in rows]}


@router.post("/ask/conversations")
def new_conversation(p: User, conn: Conn) -> dict:
    row = conn.execute("INSERT INTO ask_conversations (user_id) VALUES (%s) RETURNING id", (p.user_id,)).fetchone()
    conn.commit()
    return {"id": str(row["id"]), "title": "", "turns": 0}


@router.get("/ask/conversations/{cid}")
def conversation(cid: uuid.UUID, p: User, conn: Conn) -> dict:
    row = _owned(conn, p.user_id, cid)
    rows = conn.execute("SELECT id, role, text, citations, thinking, outcome, model, seconds, search, created_at "
                        "FROM ask_messages WHERE conversation_id = %s ORDER BY id", (cid,)).fetchall()
    return {"id": str(row["id"]), "title": row["title"],
            "messages": [{"id": r["id"], "role": r["role"], "text": r["text"] or "",
                          "citations": r["citations"] or [], "thinking": r["thinking"] or "",
                          "outcome": r["outcome"], "model": r["model"], "seconds": r["seconds"],
                          "search": r["search"], "created_at": r["created_at"].isoformat()} for r in rows]}


@router.patch("/ask/conversations/{cid}")
def rename(cid: uuid.UUID, body: Rename, p: User, conn: Conn) -> dict:
    _owned(conn, p.user_id, cid)
    conn.execute("UPDATE ask_conversations SET title = %s, updated_at = now() WHERE id = %s",
                 (body.title.strip(), cid))
    conn.commit()
    return {"id": str(cid), "title": body.title.strip()}


@router.delete("/ask/conversations/{cid}")
def delete(cid: uuid.UUID, p: User, conn: Conn) -> dict:
    _owned(conn, p.user_id, cid)
    conn.execute("DELETE FROM ask_conversations WHERE id = %s", (cid,))
    conn.commit()
    return {"ok": True}


# --- asking ---------------------------------------------------------------------------------------------------


NEAR = 3  # closest matches shown when nothing passed the evidence check
ANAPHORA = re.compile(r"\b(he|him|his|she|her|hers|they|them|their|it|its|this|that|those|these|there|same|also|"
                      r"we|us|our|ours|you|your|yours|each|every|both|former|latter)\b", re.IGNORECASE)
REWRITE = """Write 3 short search queries that together would find the answer in the user's own documents.
Use the shape documents actually use, not the shape of the question: subject + thing + action, no question words,
no "policy about", no punctuation. Examples of the shape (not of the subject):
  laptop request approval manager
  asset return exit clearance form
  bereavement leave days immediate family
  induction checklist first week equipment
Use the conversation to know what the question refers to. Keep names, codes and numbers exactly.
One per line, no numbering, at most 10 words each.
{history}
Question: {question}
"""
MAX_QUERIES = 3
ROUNDS = 3  # a budget, not a script: the model decides each round whether it has enough
DECIDE = """You are looking for the answer to a question in the user's documents.

Question: {question}

Passages found so far (title - where - first words):
{passages}

Do these passages contain the answer? If they do not, write up to 2 short search queries that would find the
missing piece: the words a policy, runbook or handbook would use, not question words. Reply with JSON only:
{{"enough": true or false, "queries": ["query", "query"], "missing": "what is missing"}}"""


def _passage_list(results, limit: int = 8) -> str:
    lines = []
    for n, r in enumerate(results[:limit], start=1):
        where = " > ".join(x for x in [r.heading_path or "", f"page {r.page}" if getattr(r, "page", None) else ""] if x)
        lines.append(f"[{n}] {r.clearer_title or r.title}" + (f" - {where}" if where else "") +
                     f" - {_one_line(r.text)[:200]}")
    return "\n".join(lines) or "(none)"


def _decide(rt, body: AskBody, question: str, results: list) -> dict:
    """The model's own verdict on the passages it has, and what to search if they are not enough."""
    try:
        url, model = _models.serving(body.model, rt.llm_device)
    except HTTPException:
        return {"enough": False, "queries": []}
    prompt = DECIDE.format(question=question, passages=_passage_list(results))
    raw = "".join(piece for kind, piece in _stream_model(url, model, prompt, think=False, max_tokens=220)
                  if kind == "text")
    try:
        return json.loads(raw)
    except ValueError:
        pass
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else {"enough": False, "queries": []}
    except ValueError:
        return {"enough": False, "queries": []}


def _search_queries(rt, body: AskBody, history: list[Turn]) -> list[str]:
    """The queries to search: the user's own words first, then what the local model writes from the conversation.

    One query is not enough for a small model's phrasing ("what is the policy about issuing laptop?"): the
    keyword and policy-worded variants reach sections the first one misses (owner, 2026-10-26)."""
    asked = body.question.strip()
    try:
        url, model = _models.serving(body.model, rt.llm_device)
    except HTTPException:  # no model on this computer: search what was asked, plus its keywords
        return [asked]
    prompt = REWRITE.format(history=_history_text(history, limit=PLAN_TURNS, chars=PLAN_CHARS), question=asked)
    pieces: list[str] = []
    try:
        for kind, piece in _stream_model(url, model, prompt, think=False, max_tokens=160):
            if kind == "text":
                pieces.append(piece)
    except enrich.LLMError:
        return [asked]
    out = [asked]
    for line in "".join(pieces).splitlines():
        query = " ".join(line.split()).strip(' -–—•*"“”.,:')
        if not query or len(query.split()) > 14 or len(query) < 3:
            continue
        if query.lower() not in {q.lower() for q in out}:
            out.append(query)
        if len(out) >= MAX_QUERIES:
            break
    return out[:MAX_QUERIES]


class _Cited:
    """A passage the conversation already cited, fetched again for a follow-up (permits checked)."""

    def __init__(self, row, confirmed: bool) -> None:
        self.chunk_id, self.document_id = row["chunk_id"], str(row["document_id"])
        self.collection = row["collection"]
        self.title, self.clearer_title, self.heading_path = row["doc_title"], None, row["heading_path"]
        self.page, self.text = row["page"], row["text"] or ""
        anchor = f"#{row['anchor']}" if row.get("anchor") else ""
        self.source_url = f"/api/documents/{row['document_id']}/view" + (f"#page={self.page}" if self.page else anchor)
        self.passage_url = f"/api/chunks/{self.chunk_id}"
        self.signals = {"carried_over": True}
        self.match_basis: list[str] = ["carried_over"]
        self.confirmed = confirmed


def _carried(conn, p, cid: uuid.UUID) -> list:
    """The passages the last answer in this conversation cited, still readable by this user."""
    row = conn.execute("SELECT citations FROM ask_messages WHERE conversation_id = %s AND role = 'assistant' "
                       "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    cites = (row or {}).get("citations") or []
    ids = [c.get("chunk_id") for c in cites if c.get("chunk_id")]
    if not ids:
        return []
    rows = conn.execute(
        "SELECT c.id AS chunk_id, c.document_id, c.doc_title, c.heading_path, c.page, c.anchor, c.text, "
        "col.name AS collection FROM chunks c JOIN collections col ON col.id = c.collection_id "
        "WHERE c.id = ANY(%s) AND c.collection_id = ANY(%s)", (ids, sorted(p.readable))).fetchall()
    flags = {c["chunk_id"]: c.get("confirmed", True) for c in cites if c.get("chunk_id")}
    return [_Cited(r, flags.get(r["chunk_id"], True)) for r in rows]


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _cite(n: int, r, confirmed: bool) -> dict:
    return {"n": n, "chunk_id": r.chunk_id, "document_id": str(r.document_id), "collection": r.collection,
            "title": r.clearer_title or r.title, "heading_path": r.heading_path, "page": r.page,
            "source_url": r.source_url, "passage_url": r.passage_url, "confirmed": confirmed,
            "preview": _one_line(r.text)[:240],
            "cosine": r.signals.get("cosine_similarity"), "rerank": r.signals.get("rerank_score")}


def _brief(r) -> dict:
    """A passage as the window shows it in the search step: where it is and how it scored."""
    return {"chunk_id": r.chunk_id, "title": r.clearer_title or r.title, "heading_path": r.heading_path,
            "page": r.page, "collection": r.collection, "source_url": r.source_url,
            "cosine": r.signals.get("cosine_similarity"), "rerank": r.signals.get("rerank_score"),
            "why": r.match_basis, "branches": r.signals.get("branches") or [],
            "terms": r.signals.get("query_term_coverage"), "preview": _one_line(r.text)[:240]}


class Plan:
    """What one question sends: the passages, the citations shown with the answer, and the closest matches
    (always listed, so the window can show what the search actually found)."""

    def __init__(self, rt, found, used, cites, history, near, unconfirmed, gate, query="") -> None:
        self.rt, self.found, self.used, self.cites = rt, found, used, cites
        self.history, self.near, self.unconfirmed, self.gate = history, near, unconfirmed, gate
        self.query = query  # what the search ran with (the planned queries, joined)
        self.queries: list[str] = []  # the queries themselves, shown in the step
        self.rounds: list[dict] = []  # what was searched and what the model decided each round
        self.note = ""  # why these passages (e.g. carried over from the previous answer)
        self.question_id = None  # the turn this question added, dropped again if no answer is written


def _prepare(conn, p, body: AskBody) -> Plan:
    """Search, then keep the passages that count as evidence (and, when asked, the closest matches)."""
    rt = runtime.load(conn)
    if not body.model and getattr(rt, "ask_model", ""):
        body.model = rt.ask_model  # chosen in Settings -> Ask
    question_id = None
    if body.conversation_id:
        _owned(conn, p.user_id, body.conversation_id)
        if body.new_turn:
            before = conn.execute("SELECT coalesce(max(id), 0) AS m FROM ask_messages WHERE conversation_id = %s",
                                  (body.conversation_id,)).fetchone()["m"]
            saved = _save_message(conn, body.conversation_id, "user", body.question.strip())
            if saved > before:  # a new row, not the same question again
                question_id = saved
            history = _turns(conn, body.conversation_id)[:-1]  # the question was just saved
        else:  # a Retry: the question is already the last turn
            history = _turns(conn, body.conversation_id)
            history = history[:-1] if history and history[-1].role == "user" else history
    else:
        history = body.history
    asked = body.question.strip()
    by_id: dict[int, object] = {}
    score: dict[int, float] = {}
    rounds: list[dict] = []
    found = None

    def run(queries: list[str]) -> None:
        """Search these queries and fold what they found into the pool (reciprocal rank fusion)."""
        nonlocal found
        for query in queries:
            res = search(conn, p, query, rt, body.collection_ids, 10)
            if found is None:
                found = res
            elif not any(r.supported for r in found.results) and any(r.supported for r in res.results):
                found = res
            for rank, r in enumerate(res.results, start=1):
                score[r.chunk_id] = score.get(r.chunk_id, 0.0) + 1.0 / (60 + rank)
                if r.chunk_id not in by_id or (r.supported and not by_id[r.chunk_id].supported):
                    by_id[r.chunk_id] = r

    # round 1: the user's own words and the model's keyword-shaped variants, always
    queries = [asked] + [q for q in _search_queries(rt, body, history) if q.lower() != asked.lower()][:MAX_QUERIES - 1]
    run(queries)
    rounds.append({"n": 1, "queries": queries, "passages": len(by_id)})
    # further rounds: the model reads what was found and decides whether it needs more, and what to search
    for n in range(2, ROUNDS + 1):
        if not by_id:
            break
        verdict = _decide(rt, body, asked, list(by_id.values()))
        rounds[-1]["enough"] = bool(verdict.get("enough"))
        rounds[-1]["missing"] = str(verdict.get("missing") or "")[:200]
        if verdict.get("enough") and any(r.supported for r in by_id.values()):
            break
        more = [str(q).strip()[:120] for q in (verdict.get("queries") or []) if str(q).strip()][:2]
        if not more:
            break
        before = set(by_id)
        run(more)
        rounds.append({"n": n, "queries": more, "passages": len(by_id)})
        if set(by_id) == before:  # it found nothing new: stop rather than loop
            rounds[-1]["novel"] = False
            break
    if not any(r.supported for r in by_id.values()):  # nothing confirmed anywhere: a keyword-only pass
        try:
            lex = search(conn, p, asked, rt, body.collection_ids, 10, branches=("lexical",))
        except Exception:  # noqa: BLE001 - the hybrid results stand on their own
            lex = None
        for r in (lex.results if lex else []):
            if r.supported:
                by_id.setdefault(r.chunk_id, r)
    ordered = sorted(by_id.values(), key=lambda r: -score.get(r.chunk_id, 0.0))
    # one passage per section: six chunks of "Privilege Leave" crowd out the handbook's bereavement page
    seen_sections, merged = set(), []
    for r in ordered:
        section = (str(r.document_id), r.heading_path or "")
        if section in seen_sections:
            continue
        seen_sections.add(section)
        merged.append(r)
    found.results = merged
    conn.commit()
    confirmed = [r for r in found.results if r.supported]
    unsupported = [r for r in found.results if not r.supported]
    carried: list = []
    if not confirmed and body.conversation_id:  # "how many of each do we get": keep the passages already cited
        carried = [r for r in _carried(conn, p, body.conversation_id)][:PASSAGES]
    allow = body.allow_unsupported or getattr(rt, "ask_allow_unsupported", False)
    used = (confirmed + carried)[:PASSAGES]
    if allow:  # the model decides what is close enough: confirmed passages, then the closest matches
        for r in unsupported:
            r.closest = True
        used = (used + unsupported)[:PASSAGES]
    unconfirmed = any(getattr(r, "closest", False) for r in used)
    cites = [_cite(n, r, (not unconfirmed) and getattr(r, "confirmed", True)) for n, r in enumerate(used, start=1)]
    gate = {"mode": rt.gate_mode, "dense_min": rt.gate_dense_min, "rerank_min": rt.gate_rerank_min}
    note = ""
    if carried and not confirmed:
        note = ("No new passage passed the evidence check this time: answering from the passages this conversation "
                "already cited.")
    elif unconfirmed:
        note = ("Some of these passages did not pass the evidence check: the model decides whether they answer, and "
                "says so when they do not.")
    # the closest matches shown besides the ones used: when the answer already rests on them, list the rest
    near = unsupported[len(used):] if unconfirmed else unsupported
    all_queries = [q for rnd in rounds for q in rnd["queries"]]
    plan = Plan(rt, found, used, cites, history, [_brief(r) for r in near[:NEAR]], unconfirmed, gate,
                " · ".join(all_queries))
    plan.queries = list(dict.fromkeys(all_queries))
    plan.rounds = rounds
    plan.note = note
    plan.question_id = question_id
    return plan


def _step(plan: Plan) -> dict:
    """The search step stored with an answer (the window shows it when the conversation is reopened)."""
    return {"question": plan.found.query, "searched": plan.query, "queries": plan.queries,
            "rounds": plan.rounds, "outcome": plan.found.outcome,
            "message": plan.note or plan.found.message,
            "passages": len(plan.used), "citations": plan.cites, "near": plan.near, "gate": plan.gate,
            "unconfirmed": plan.unconfirmed}


def _answer(rt, body: AskBody, plan: Plan) -> Answer:
    """Run the model and return the finished Answer (used by /api/ask and the MCP tool)."""
    out = Answer()
    url, model = _models.serving(body.model, rt.llm_device)
    out.model = Path(model).stem if model else ""
    prompt = build_prompt(body.question, plan.used, plan.history, plan.unconfirmed)
    list(out.pieces(_stream_model(url, model, prompt, body.think)))
    return out


@router.get("/ask/models")
def ask_models(p: User, conn: Conn) -> dict:
    """The models Ask can use, and whether each is downloaded (the same list as the document summaries)."""
    s, e = runtime.base(), getattr(state, "enricher", None)
    return {"default": enrich.DEFAULT_LLM, "external": bool(s.llm_url),
            "chosen": rt.ask_model if (rt := runtime.load(conn)) else "",
            "think": bool(rt.ask_think), "allow_unsupported": bool(rt.ask_allow_unsupported),
            "current": getattr(e, "model", "") or "",
            "models": [{"name": k, "file": v[1], "note": v[2], "installed": bool(enrich.installed(s, v[1]))}
                       for k, v in enrich.LLMS.items()]}


@router.post("/ask")
def ask(body: AskBody, p: User, conn: Conn) -> dict:
    if body.model and body.model not in enrich.LLMS:
        raise HTTPException(422, f"unknown local model {body.model}")
    plan = _prepare(conn, p, body)
    base = {"question": body.question, "searched": plan.query, "queries": plan.queries,
            "rounds": plan.rounds, "outcome": plan.found.outcome,
            "message": plan.note or plan.found.message, "citations": plan.cites,
            "near": plan.near, "gate": plan.gate, "unconfirmed": plan.unconfirmed,
            "conversation_id": str(body.conversation_id) if body.conversation_id else None}
    if not plan.used:  # no model call: there is nothing to answer from
        return {"answer": None, "answered": False, "model": "", "seconds": 0, **base}
    try:
        out = _answer(plan.rt, body, plan)
    except enrich.LLMError as exc:
        raise HTTPException(503, f"the local language model failed: {exc}") from exc
    if body.conversation_id:
        _save_message(conn, body.conversation_id, "assistant", out.text, citations=plan.cites,
                      thinking=out.thinking, outcome=plan.found.outcome, model=out.model, seconds=out.seconds,
                      search=_step(plan))
    return {"answer": out.text or None, "answered": not out.abstained and bool(out.text),
            "thinking": out.thinking or None, "model": out.model, "seconds": out.seconds, **base}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/ask/stream")
def ask_stream(body: AskBody, p: User, conn: Conn):
    """The same answer as `/api/ask`, written out as it is produced (server-sent events)."""
    if body.model and body.model not in enrich.LLMS:
        raise HTTPException(422, f"unknown local model {body.model}")
    plan = _prepare(conn, p, body)
    question = body.question.strip()

    def events() -> Iterator[str]:
        out = Answer()
        model = ""
        saved = False
        try:
            yield _sse({"type": "search", "question": question, "searched": plan.query,
                        "queries": plan.queries, "rounds": plan.rounds,
                        "outcome": plan.found.outcome,
                        "message": plan.note or plan.found.message, "citations": plan.cites, "passages": len(plan.used),
                        "near": plan.near, "gate": plan.gate, "unconfirmed": plan.unconfirmed})
            if not plan.used:  # no model call: there is nothing to answer from
                yield _sse({"type": "done", "answered": False, "model": "", "seconds": 0, "abstained": True,
                            "conversation_id": str(body.conversation_id) if body.conversation_id else None})
                return
            url, model = _models.serving(body.model, plan.rt.llm_device)
            out.model = Path(model).stem if model else ""
            prompt = build_prompt(question, plan.used, plan.history, plan.unconfirmed)
            for kind, piece in out.pieces(_stream_model(url, model, prompt, body.think)):
                yield _sse({"type": kind, "text": piece})
            if body.conversation_id and (out.text or out.thinking):  # saved before "done", so a reload shows it
                with db.pool().connection() as c:
                    _save_message(c, body.conversation_id, "assistant", out.text, citations=plan.cites,
                                  thinking=out.thinking, outcome=plan.found.outcome, model=out.model,
                                  seconds=out.seconds, search=_step(plan))
                saved = True
            yield _sse({"type": "done", "answered": not out.abstained and bool(out.text), "model": out.model,
                        "seconds": out.seconds, "abstained": out.abstained,
                        "conversation_id": str(body.conversation_id) if body.conversation_id else None})
        except enrich.LLMError as exc:
            yield _sse({"type": "error", "message": f"the local language model failed: {exc}"})
        finally:
            if plan.question_id and not out.text and not out.thinking:  # stopped before anything was written
                try:
                    with db.pool().connection() as c:
                        c.execute("DELETE FROM ask_messages WHERE id = %s AND role = 'user'", (plan.question_id,))
                        c.commit()
                except Exception:  # noqa: BLE001 - the message staying is worse than a failed delete, but not fatal
                    log.exception("Ask: could not drop the unanswered question")
            # the client may have stopped it, or it failed: keep what was written, so the conversation says so
            elif body.conversation_id and not saved and (out.text or out.thinking):
                try:
                    with db.pool().connection() as c:
                        _save_message(c, body.conversation_id, "assistant", out.text, citations=plan.cites,
                                      thinking=out.thinking, outcome=plan.found.outcome, model=out.model,
                                      seconds=out.seconds, search=_step(plan))
                except Exception:  # noqa: BLE001 - a failed save must not break the stream
                    log.exception("Ask: could not save the answer")

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
