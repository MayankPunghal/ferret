"""Ask: no supporting passage means no model call and the search's own refusal; a model is required otherwise."""

from knowledge_search import ask, enrich
from knowledge_search.deps import state

from .conftest import token


def test_no_evidence_no_answer(client, corpus):
    r = client.post("/api/ask", headers=token(client, "alice"), json={"question": "zebra quantum lasagna policy"})
    assert r.status_code == 200 and r.json()["answer"] is None and not r.json()["answered"]


def test_answer_cites_only_supported_passages(client, corpus, monkeypatch):
    class FakeEnricher:
        available, url, model, broken = True, "http://fake", "m", ""
        settings = state.settings

        def _ensure_server(self):
            return True

    seen = {}

    def fake_chat(url, model, prompt, **kw):
        seen["prompt"] = prompt
        return '{"answer": "Retry after 30 seconds [1].", "answered": true}'

    monkeypatch.setattr(state, "enricher", FakeEnricher(), raising=False)
    monkeypatch.setattr(enrich, "chat", fake_chat)
    r = client.post("/api/ask", headers=token(client, "alice"), json={"question": "ERR-PAY-4012"}).json()
    assert r["answered"] and r["answer"].endswith("[1].") and r["citations"][0]["n"] == 1
    assert "ERR-PAY-4012" in seen["prompt"] and len(r["citations"]) <= ask.PASSAGES
