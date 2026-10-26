"""Ask: an answer only from passages that passed the evidence gate, with citations; no evidence means no model
call and the search's own refusal; the model's NOT_IN_DOCUMENTS becomes "no answer"; conversations are saved per
user; the answer streams; the earlier turns travel with the question but never as evidence."""

import json
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


def fake_model(*chunks, thinking="", seen=None):
    """What the model server streams: text pieces (and its reasoning, before them)."""
    def gen(url, model, prompt, think, max_tokens=900):
        if seen is not None:
            seen.update(url=url, model=model, prompt=prompt, think=think)
        if thinking:
            yield "thinking", thinking
        for chunk in chunks:
            yield "text", chunk
    return gen


def allow_closest(client, on: bool) -> None:
    """The Settings switch: answer from the closest matches when nothing passed the evidence check."""
    r = client.put("/api/admin/settings", headers=token(client, "root"), json={"ask_allow_unsupported": on})
    assert r.status_code == 200, r.text


def test_no_evidence_means_no_answer_and_no_model_call(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    seen = {}
    monkeypatch.setattr(ask, "_stream_model", fake_model("should not be asked", seen=seen))
    allow_closest(client, False)
    try:
        r = client.post("/api/ask", headers=token(client, "alice"), json={"question": "zebra quantum lasagna policy"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer"] is None and body["answered"] is False and body["citations"] == []
        assert not seen  # nothing to answer from: the model is never asked
        assert body["outcome"] == "no_adequate_evidence" and body["message"]
        assert body["near"], "the refusal still lists what the search found"
    finally:
        allow_closest(client, True)


def test_the_answer_cites_the_passages_the_model_was_given(client, corpus, monkeypatch):
    seen = {}
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(ask, "_stream_model", fake_model("Retry after 30 seconds ", "[1].", seen=seen))
    body = client.post("/api/ask", headers=token(client, "alice"), json={"question": "ERR-PAY-4012"}).json()
    assert body["answered"] and body["answer"] == "Retry after 30 seconds [1]."
    assert body["citations"][0]["n"] == 1 and body["citations"][0]["chunk_id"]
    assert len(body["citations"]) <= ask.PASSAGES and body["outcome"] == "evidence_found"
    assert seen["url"] == "http://fake/v1" and seen["model"] == MODEL and seen["think"] is False
    assert "ERR-PAY-4012" in seen["prompt"] and "[1] " in seen["prompt"]
    assert body["model"] and body["seconds"] >= 0


def test_the_model_saying_it_is_not_in_the_documents_is_a_refusal(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(ask, "_stream_model", fake_model("NOT", "_IN_DOC", "UMENTS"))
    body = client.post("/api/ask", headers=token(client, "alice"), json={"question": "ERR-PAY-4012"}).json()
    assert body["answered"] is False and body["answer"] is None


def test_think_first_asks_for_the_reasoning_and_keeps_it(client, corpus, monkeypatch):
    seen = {}
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(ask, "_stream_model", fake_model("Retry once [1].", thinking="The passage says so. ",
                                                         seen=seen))
    body = client.post("/api/ask", headers=token(client, "alice"),
                       json={"question": "ERR-PAY-4012", "think": True}).json()
    assert body["answered"] and body["thinking"].strip() == "The passage says so." and seen["think"] is True


def test_history_travels_with_the_question_but_stays_bounded():
    class Result:  # the parts of search.Result that build_prompt reads
        title, clearer_title, heading_path, page, text = "Runbook", None, "Restart", 4, "Restart the service."

    long_turn = [ask.Turn(role="user", text="q" * 3000), ask.Turn(role="assistant", text="a" * 3000)]
    prompt = ask.build_prompt("and the evening batch?", [Result()], long_turn)
    assert prompt.count("User: ") == 1 and prompt.count("You: ") == 1
    assert "q" * (ask.CHARS_PER_TURN + 1) not in prompt  # each turn is cut, not pasted whole
    assert "and the evening batch?" in prompt and "[1] Runbook > Restart (page 4)" in prompt
    assert "not evidence" in prompt and len(prompt) < 14000


def test_a_conversation_is_saved_and_its_turns_reach_the_next_question(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    seen = {}
    monkeypatch.setattr(ask, "_stream_model", fake_model("Retry once ", "[1].", seen=seen))
    h = token(client, "alice")
    cid = client.post("/api/ask/conversations", headers=h).json()["id"]
    first = client.post("/api/ask", headers=h, json={"question": "ERR-PAY-4012", "conversation_id": cid}).json()
    assert first["answered"] and first["conversation_id"] == cid
    second = client.post("/api/ask", headers=h, json={"question": "and how do I retry it?", "conversation_id": cid})
    assert second.status_code == 200
    assert "User: ERR-PAY-4012" in seen["prompt"]  # the earlier question came from the stored conversation
    body = client.get(f"/api/ask/conversations/{cid}", headers=h).json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user", "assistant"]
    assert body["messages"][1]["citations"] and body["title"].startswith("ERR-PAY-4012")
    listed = client.get("/api/ask/conversations", headers=h).json()["conversations"]
    assert any(c["id"] == cid and c["turns"] == 4 for c in listed)
    assert any(c["id"] == cid for c in client.get("/api/ask/conversations?q=retry", headers=h).json()["conversations"])
    assert client.patch(f"/api/ask/conversations/{cid}", headers=h, json={"title": "Retries"}).json()["title"] == "Retries"
    assert client.delete(f"/api/ask/conversations/{cid}", headers=h).status_code == 200
    assert client.get(f"/api/ask/conversations/{cid}", headers=h).status_code == 404


def test_another_users_conversation_is_not_readable(client, corpus):
    alice, bob = token(client, "alice"), token(client, "bob")
    cid = client.post("/api/ask/conversations", headers=alice).json()["id"]
    assert client.get(f"/api/ask/conversations/{cid}", headers=bob).status_code == 404
    assert client.patch(f"/api/ask/conversations/{cid}", headers=bob, json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/ask/conversations/{cid}", headers=bob).status_code == 404


def test_the_answer_streams_its_search_its_text_and_its_end(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(ask, "_stream_model", fake_model("Page the on-call ", "engineer [1]."))
    h = token(client, "alice")
    cid = client.post("/api/ask/conversations", headers=h).json()["id"]
    events = []
    with client.stream("POST", "/api/ask/stream", headers=h,
                       json={"question": "ERR-PAY-4012", "conversation_id": cid}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "search" and "text" in kinds and kinds[-1] == "done"
    search = events[0]
    assert search["citations"] and search["outcome"] == "evidence_found"
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Page the on-call engineer [1]."
    assert events[-1]["answered"] is True and events[-1]["conversation_id"] == cid
    saved = client.get(f"/api/ask/conversations/{cid}", headers=h).json()["messages"]
    assert saved[-1]["role"] == "assistant" and saved[-1]["text"] == "Page the on-call engineer [1]."


def test_the_model_list_and_the_mcp_tool(client, corpus):
    body = client.get("/api/ask/models", headers=token(client, "alice")).json()
    assert {m["name"] for m in body["models"]} == set(enrich.LLMS)
    assert all("installed" in m and m["file"].endswith(".gguf") for m in body["models"])
    from knowledge_search import mcp

    tool = next(t for t in mcp.TOOLS if t["name"] == "ask")
    assert tool["inputSchema"]["required"] == ["question"] and tool["annotations"]["readOnlyHint"] is True

def test_the_setting_answers_from_the_closest_matches(client, corpus, monkeypatch):
    """Owner: one setting, not a button per question — Ask answers on its own, marked unconfirmed."""
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    seen = {}
    monkeypatch.setattr(ask, "_stream_model", fake_model("The closest page says he is the COO ", "[1].", seen=seen))
    allow_closest(client, True)
    h = token(client, "alice")
    body = client.post("/api/ask", headers=h, json={"question": "zebra quantum lasagna policy"}).json()
    assert body["unconfirmed"] is True and body["answered"] is True
    assert body["answer"] == "The closest page says he is the COO [1]."
    assert body["citations"] and body["citations"][0]["confirmed"] is False
    assert "closest match, not confirmed" in seen["prompt"]


def _rewriting_model(rewritten: str, answer: str, seen: list):
    """The model is called twice for a follow-up: first to write the search query, then to answer."""
    def gen(url, model, prompt, think, max_tokens=900):
        seen.append(prompt)
        if prompt.startswith("Rewrite the user's latest question"):
            yield "text", rewritten
        else:
            yield "text", answer
    return gen


def test_a_follow_up_searches_with_a_rewritten_query(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    seen = []
    monkeypatch.setattr(ask, "_stream_model",
                        _rewriting_model("who reports to Kabir Singh", "Sumit Bhandari [1].", seen))
    history = [{"role": "user", "text": "Who is Kabir Singh?"},
               {"role": "assistant", "text": "He is the COO [1]."}]
    events = []
    with client.stream("POST", "/api/ask/stream", headers=token(client, "alice"),
                       json={"question": "who all report to him?", "history": history}) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    search = events[0]
    assert search["searched"] == "who reports to Kabir Singh"
    assert search["question"] == "who all report to him?"
    assert any("Who is Kabir Singh?" in p for p in seen if p.startswith("Rewrite"))


def test_a_retry_does_not_add_the_question_twice(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(ask, "_stream_model", fake_model("Retry once ", "[1]."))
    h = token(client, "alice")
    cid = client.post("/api/ask/conversations", headers=h).json()["id"]
    client.post("/api/ask", headers=h, json={"question": "ERR-PAY-4012", "conversation_id": cid})
    client.post("/api/ask", headers=h, json={"question": "ERR-PAY-4012", "conversation_id": cid, "new_turn": False})
    messages = client.get(f"/api/ask/conversations/{cid}", headers=h).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "assistant"]


def test_a_refusal_shows_the_closest_matches_and_can_still_answer_from_them(client, corpus, monkeypatch):
    """Owner, 2026-10-26: "Who is Kabir Singh?" got no answer and the window showed nothing. A refusal must
    carry what the search found (the closest matches, with their scores and the gate), and when the user asks,
    the model may answer from them — labelled unconfirmed."""
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    seen = {}
    monkeypatch.setattr(ask, "_stream_model", fake_model("The closest page says he is the COO ", "[1].", seen=seen))
    h = token(client, "alice")
    allow_closest(client, False)
    try:
        body = client.post("/api/ask", headers=h, json={"question": "zebra quantum lasagna policy"}).json()
        assert body["answer"] is None and body["outcome"] == "no_adequate_evidence"
        assert body["gate"]["mode"] and body["gate"]["dense_min"] is not None and "near" in body
        assert body["near"], "the refusal lists what the search found"
        assert not seen, "no model call while nothing passed the gate"
    finally:
        allow_closest(client, True)

    again = client.post("/api/ask", headers=h,
                        json={"question": "zebra quantum lasagna policy", "allow_unsupported": True}).json()
    assert again["unconfirmed"] is True and again["answered"] is True
    assert again["answer"] == "The closest page says he is the COO [1]."
    assert again["citations"] and again["citations"][0]["confirmed"] is False
    assert "closest match, not confirmed" in seen["prompt"]
    assert "According to the closest matching passages" in seen["prompt"]


def test_the_stream_carries_the_search_step_and_the_closest_matches(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(ask, "_stream_model", fake_model("not asked"))
    h = token(client, "alice")
    allow_closest(client, False)
    events = []
    try:
        with client.stream("POST", "/api/ask/stream", headers=h,
                           json={"question": "zebra quantum lasagna policy"}) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
    finally:
        allow_closest(client, True)
    search = events[0]
    assert search["type"] == "search" and search["outcome"] == "no_adequate_evidence"
    assert search["citations"] == [] and search["near"] and search["gate"]["mode"]
    assert events[-1]["type"] == "done" and events[-1]["answered"] is False



def test_a_follow_up_answers_from_the_passages_the_conversation_already_cited(client, corpus, monkeypatch):
    """Owner, 2026-10-26: "it could have answered from the same search's result the first time and the second
    time too". When the new search finds nothing, the passages the last answer cited are used again."""
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    seen = {}
    monkeypatch.setattr(ask, "_stream_model", fake_model("Fifteen days for the first question ", "[1].", seen=seen))
    allow_closest(client, False)  # no closest matches: only the carry-over can answer
    h = token(client, "alice")
    try:
        cid = client.post("/api/ask/conversations", headers=h).json()["id"]
        first = client.post("/api/ask", headers=h,
                            json={"question": "ERR-PAY-4012", "conversation_id": cid}).json()
        assert first["answered"] and first["citations"]
        second = client.post("/api/ask", headers=h,
                             json={"question": "zebra quantum lasagna policy", "conversation_id": cid}).json()
    finally:
        allow_closest(client, True)
    assert second["answered"], "the follow-up answered from what the conversation already cited"
    assert second["citations"] and second["citations"][0]["chunk_id"] == first["citations"][0]["chunk_id"]
    assert "already cited" in second["message"]
    assert "[1] " in seen["prompt"]


def test_a_question_that_produced_no_answer_leaves_no_turn_behind(client, corpus, monkeypatch):
    """A send stopped before the first word used to leave the question alone in the conversation, so the next
    send showed it twice (owner, 2026-10-26)."""
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(ask, "_stream_model", lambda *a, **k: iter(()))  # the model writes nothing
    h = token(client, "alice")
    cid = client.post("/api/ask/conversations", headers=h).json()["id"]
    with client.stream("POST", "/api/ask/stream", headers=h,
                       json={"question": "ERR-PAY-4012", "conversation_id": cid}) as r:
        list(r.iter_lines())
    assert client.get(f"/api/ask/conversations/{cid}", headers=h).json()["messages"] == []


def test_a_follow_up_without_a_pronoun_is_still_rewritten(client, corpus, monkeypatch):
    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    seen = []
    monkeypatch.setattr(ask, "_stream_model",
                        _rewriting_model("number of leave days per type", "Fifteen [1].", seen))
    history = [{"role": "user", "text": "How many leaves of each type do employees get?"},
               {"role": "assistant", "text": "Privilege, casual, sick … [1]."}]
    events = []
    with client.stream("POST", "/api/ask/stream", headers=token(client, "alice"),
                       json={"question": "and the exact numbers", "history": history}) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[0]["searched"] == "number of leave days per type"
