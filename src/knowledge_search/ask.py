"""Ask: a chat that answers in words with the local language model, from the passages search found.

Search runs first with the caller's permissions; only passages that passed the evidence gate are numbered and
go to the model, and the answer must cite them ([1], [2]). No supported passage means no model call at all: the
search's own "not found" message is the answer, so the chat cannot invent one. The earlier turns of the
conversation travel with the question so follow-ups ("and for the evening batch?") can be understood, but the
answer must still rest on the passages of the current search.

The model is the one the app already runs for document summaries (enrich.py) unless another one is chosen, in
which case ask.py runs its own llama-server for it, one model at a time. Nothing leaves the computer.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import enrich, runtime
from .deps import Conn, User, state
from .search import search

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["ask"])

PASSAGES = 6  # passages handed to the model: enough to answer, short enough for a small model's context
CHARS_PER_PASSAGE = 1500
TURNS = 4  # earlier question/answer pairs kept for follow-ups
CHARS_PER_TURN = 700
_gen = threading.Lock()  # one answer at a time: llama-server runs one request, -np 1

PROMPT = """You answer questions about the user's own documents, using ONLY the numbered passages below.
Cite every passage you use like [1] or [2, 3]. If the passages do not answer the question, say so plainly in
one sentence and do not guess: an answer that is not in the passages is worse than no answer. Keep commands,
error codes, names, numbers and versions exactly as written. Be concise: a few sentences, or a short list.
{history}
Question: {question}

Passages:
{passages}

Reply as JSON: {{"answer": "<the answer, with [n] citations, or the sentence saying it is not in the documents>",
"answered": true or false}}"""


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=4000)


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=20)
    model: str = ""  # an enrich.LLMS name; "" = the model the summaries use
    collection_ids: list[int] | None = None


def _history_text(history: list[Turn]) -> str:
    """The last turns, as context for the phrasing of the question only (never as evidence)."""
    turns = [t for t in history if t.text.strip()][-TURNS * 2:]
    if not turns:
        return ""
    lines = [f"{'User' if t.role == 'user' else 'You'}: {t.text.strip()[:CHARS_PER_TURN]}" for t in turns]
    return ("\nThe conversation so far, for what the question refers to (it is not evidence; cite only the "
            "passages):\n" + "\n".join(lines) + "\n")


def build_prompt(question: str, results: list, history: list[Turn] | None = None) -> str:
    """The prompt for one question. Separate, so a test can read it without a model."""
    passages = "\n\n".join(
        f"[{n}] {r.clearer_title or r.title}"
        f"{' > ' + r.heading_path if r.heading_path else ''}{f' (page {r.page})' if r.page else ''}\n"
        f"{r.text[:CHARS_PER_PASSAGE]}"
        for n, r in enumerate(results, start=1))
    return PROMPT.format(history=_history_text(history or []), question=question.strip(), passages=passages)


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


_answer = _AnswerModel()


@router.get("/ask/models")
def ask_models(p: User, conn: Conn) -> dict:
    """The models Ask can use, and whether each is downloaded (the same list as the document summaries)."""
    s, e = runtime.base(), getattr(state, "enricher", None)
    return {"default": enrich.DEFAULT_LLM, "external": bool(s.llm_url),
            "current": getattr(e, "model", "") or "",
            "models": [{"name": k, "file": v[1], "note": v[2], "installed": bool(enrich.installed(s, v[1]))}
                       for k, v in enrich.LLMS.items()]}


@router.post("/ask")
def ask(body: AskBody, p: User, conn: Conn) -> dict:
    if body.model and body.model not in enrich.LLMS:
        raise HTTPException(422, f"unknown local model {body.model}")
    rt = runtime.load(conn)
    found = search(conn, p, body.question, rt, body.collection_ids, 10)
    conn.commit()
    passages = [r for r in found.results if r.supported][:PASSAGES]
    cites = [{"n": n, "chunk_id": r.chunk_id, "document_id": r.document_id, "collection": r.collection,
              "title": r.clearer_title or r.title, "heading_path": r.heading_path, "page": r.page,
              "source_url": r.source_url, "passage_url": r.passage_url}
             for n, r in enumerate(passages, start=1)]
    base = {"outcome": found.outcome, "message": found.message, "citations": cites}
    if not passages:  # no model call: there is nothing to answer from
        return {"answer": None, "answered": False, **base, "model": ""}

    url, model = _answer.serving(body.model, rt.llm_device)
    prompt = build_prompt(body.question, passages, body.history)
    started = time.perf_counter()
    with _gen:
        try:
            raw = enrich.chat(url, model, prompt, max_tokens=700, timeout=300, label="Ask")
        except enrich.LLMError as exc:
            raise HTTPException(503, f"the local language model failed: {exc}") from exc
    try:
        reply = json.loads(raw)
        answer = str(reply.get("answer") or "").strip()
        answered = bool(reply.get("answered", True)) and bool(answer)
    except (ValueError, AttributeError):  # a model that ignores the JSON request: the text still says something
        answer, answered = raw.strip(), bool(raw.strip())
    return {"answer": answer or None, "answered": answered, **base,
            "model": Path(model).stem, "seconds": round(time.perf_counter() - started, 1)}
