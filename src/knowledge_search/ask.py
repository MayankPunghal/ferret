"""Ask: a question answered in words by the local language model, only from the passages search found.

Search runs first with the caller's permissions; only passages that passed the evidence gate go to the model,
numbered, and the answer must cite them ([1], [2]). No supported passage: no model call, the search's own
"not found" message instead. Each question stands alone (no chat history is sent), so an answer never rests
on an earlier answer. Uses the summaries' model server (enrich.py); without it Ask says how to turn it on.
"""

import json
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import enrich, runtime
from .deps import Conn, User, state
from .search import search

router = APIRouter(prefix="/api", tags=["ask"])
_lock = threading.Lock()  # ponytail: one answer at a time on a CPU; a queue per user if that becomes a problem
PASSAGES = 6

PROMPT = """Answer the question using ONLY the numbered passages below. Cite the passages you use as [1], [2].
If the passages do not answer the question, say so plainly and do not guess. Be concise and exact: keep
commands, error codes, names and numbers as written.

Question: {question}

{passages}

Reply as JSON: {{"answer": "<answer with [n] citations>", "answered": true or false}}"""


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@router.post("/ask")
def ask(body: AskBody, p: User, conn: Conn):
    rt = runtime.load(conn)
    found = search(conn, p, body.question, rt, None, 10)
    conn.commit()
    passages = [r for r in found.results if r.supported][:PASSAGES]
    cites = [{"n": n, "title": r.clearer_title or r.title, "heading_path": r.heading_path, "page": r.page,
              "source_url": r.source_url, "passage_url": r.passage_url, "snippet": r.snippet}
             for n, r in enumerate(passages, start=1)]
    if not passages:
        return {"answer": None, "answered": False, "message": found.message, "outcome": found.outcome, "citations": []}
    e = state.enricher
    if not e or not (e.available or e.settings.llm_url):
        raise HTTPException(503, "Ask needs the local language model: turn on document summaries in Settings "
                                 "(it downloads the model once).")
    text = "\n\n".join(f"[{n}] {r.title}{' > ' + r.heading_path if r.heading_path else ''}\n{r.text[:1500]}"
                       for n, r in enumerate(passages, start=1))
    with _lock:
        if not e._ensure_server():
            raise HTTPException(503, e.broken or "the local language model could not start")
        try:
            out = enrich.chat(e.url, e.model,
                              PROMPT.format(question=body.question, passages=text),
                              max_tokens=700, timeout=300, label="Ask")
        except enrich.LLMError as err:
            raise HTTPException(503, f"the local language model failed: {err}") from err
    try:
        reply = json.loads(out)
        answer, answered = str(reply.get("answer") or "").strip(), bool(reply.get("answered", True))
    except (ValueError, AttributeError):
        answer, answered = out.strip(), True
    return {"answer": answer, "answered": answered and bool(answer), "outcome": found.outcome,
            "message": found.message, "citations": cites}
