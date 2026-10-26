"""Ask: an answer only from passages that passed the evidence gate, with citations; no evidence means no model
call and the search's own refusal; the last turns travel with the question but never as evidence."""

from types import SimpleNamespace

from knowledge_search import ask, enrich
from knowledge_search.deps import state

from .conftest import token

MODEL = "gemma-4-E2B-it-Q4_K_M.gguf"


class FakeEnricher:
    """The summaries' server, already holding the model, so Ask shares it instead of starting its own."""

    available, url, model, broken = True, "http://fake/v1", MODEL, ""

    def __init__(self):
        self.server = SimpleNamespace(model=SimpleNamespace(name=MODEL))

    def _ensure_server(self):
        return True


def test_no_evidence_means_no_answer_and_no_model_call(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    called = []
    monkeypatch.setattr(enrich, "chat", lambda *a, **k: called.append(a) or "{}")
    r = client.post("/api/ask", headers=token(client, "alice"), json={"question": "zebra quantum lasagna policy"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] is None and body["answered"] is False and body["citations"] == []
    assert not called  # nothing to answer from: the model is never asked
    assert body["outcome"] == "no_adequate_evidence" and body["message"]


def test_the_answer_cites_the_passages_the_model_was_given(client, corpus, monkeypatch):
    seen = {}

    def fake_chat(url, model, prompt, **kw):
        seen.update(url=url, model=model, prompt=prompt)
        return '{"answer": "Retry after 30 seconds [1].", "answered": true}'

    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(enrich, "chat", fake_chat)
    body = client.post("/api/ask", headers=token(client, "alice"), json={"question": "ERR-PAY-4012"}).json()
    assert body["answered"] and body["answer"] == "Retry after 30 seconds [1]."
    assert body["citations"][0]["n"] == 1 and body["citations"][0]["chunk_id"]
    assert len(body["citations"]) <= ask.PASSAGES and body["outcome"] == "evidence_found"
    assert seen["url"] == "http://fake/v1" and seen["model"] == MODEL
    assert "ERR-PAY-4012" in seen["prompt"] and "[1] " in seen["prompt"]
    assert body["model"] and body["seconds"] >= 0


def test_a_model_that_ignores_the_json_request_still_answers(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(enrich, "chat", lambda *a, **k: "It is in the runbook, section 2.")
    body = client.post("/api/ask", headers=token(client, "alice"), json={"question": "ERR-PAY-4012"}).json()
    assert body["answered"] and body["answer"] == "It is in the runbook, section 2."


def test_history_travels_with_the_question_but_stays_bounded():
    class Result:  # the parts of search.Result that build_prompt reads
        title, clearer_title, heading_path, page, text = "Runbook", None, "Restart", 4, "Restart the service."

    long_turn = [ask.Turn(role="user", text="q" * 3000), ask.Turn(role="assistant", text="a" * 3000)]
    prompt = ask.build_prompt("and the evening batch?", [Result()], long_turn)
    assert prompt.count("User: ") == 1 and prompt.count("You: ") == 1
    assert "q" * (ask.CHARS_PER_TURN + 1) not in prompt  # each turn is cut, not pasted whole
    assert "and the evening batch?" in prompt and "[1] Runbook > Restart (page 4)" in prompt
    assert "not evidence" in prompt and len(prompt) < 12000


def test_the_model_list_says_which_ones_are_downloaded(client, corpus):
    body = client.get("/api/ask/models", headers=token(client, "alice")).json()
    assert {m["name"] for m in body["models"]} == set(enrich.LLMS)
    assert all("installed" in m and m["file"].endswith(".gguf") for m in body["models"])
