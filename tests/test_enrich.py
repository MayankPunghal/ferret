"""Background summaries by a local language model (enrich.py), against a fake OpenAI-compatible server on
localhost: what is stored, when a clearer title is used, that only indexed documents are summarised, failures and switches."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from knowledge_search import db, enrich, runtime
from knowledge_search.api import state
from knowledge_search.ingest import requeue

from .conftest import search, token

REPLY = {"title": "Payment Retry Runbook", "summary": "How payments are retried.  Covers backoff.",
         "topics": ["payments", "Payments", "retries"]}


@pytest.fixture
def fake_llm():
    calls, reply = [], {"content": json.dumps(REPLY)}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": reply["content"]}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", calls, reply
    server.shutdown()
    with db.pool().connection() as c:
        c.execute("UPDATE documents SET enrichment = NULL, enrich_claimed_at = NULL")
        c.execute("DELETE FROM app_settings WHERE key IN ('enrich_enabled', 'context_chars', 'enrich_hours')")


def enricher(url):
    return enrich.Enricher(state.settings.model_copy(update={"llm_url": url, "llm_model": "fake"}))


def run_all(e, limit=500):
    n = 0
    while e.run_once():
        n += 1
        assert n < limit
    return n


def one_doc():
    """An indexed document without a description of its own."""
    with db.pool().connection() as c:
        return c.execute("SELECT id, title FROM documents WHERE status = 'indexed' AND NOT metadata ? 'description' "
                         "AND NOT metadata ? 'summary' ORDER BY title LIMIT 1").fetchone()


def test_summaries_topics_and_clearer_title_only_for_vague_titles(client, corpus, drain, fake_llm):
    url, calls, _ = fake_llm
    doc = one_doc()
    with db.pool().connection() as c:
        c.execute("UPDATE documents SET title = 'Overview' WHERE id = %s", (doc["id"],))
    try:
        e = enricher(url)
        n = run_all(e)
        with db.pool().connection() as c:
            total = c.execute("SELECT count(*) AS n FROM documents WHERE status = 'indexed'").fetchone()["n"]
            rows = {r["id"]: r["enrichment"] for r in c.execute("SELECT id, enrichment FROM documents WHERE status = 'indexed'")}
            titles = {r["id"]: r["title"] for r in c.execute("SELECT id, title FROM documents")}
            st = enrich.status(c, e.model)
    finally:
        with db.pool().connection() as c:
            c.execute("UPDATE documents SET title = %s WHERE id = %s", (doc["title"], doc["id"]))
    assert n == total == len(calls) and e.state == "idle"
    assert st == {"summarised": total, "pending": 0, "failed": 0}
    assert calls[0]["response_format"] == {"type": "json_object"} and calls[0]["temperature"] == 0
    assert "Document title:" in calls[0]["messages"][0]["content"]
    assert sum("not descriptive" in c["messages"][0]["content"] for c in calls) == 2  # 'Overview', ledger-config.txt
    for i, en in rows.items():
        assert en["summary"] == "How payments are retried. Covers backoff."
        assert en["topics"] == ["payments", "retries"]  # case-insensitive duplicates dropped
        vague = i == doc["id"] or titles[i] == "ledger-config.txt"  # a bare file name is vague too
        assert en["title"] == ("Payment Retry Runbook" if vague else None), titles[i]
    assert not e.run_once()  # nothing left: each document is summarised once per indexed version

    root = token(client, "root")
    r = search(client, root, "ERR-PAY-4012")["results"][0]
    assert r["summary"] == "How payments are retried. Covers backoff." and r["topics"] == ["payments", "retries"]
    p = client.get(r["passage_url"], headers=root).json()
    assert p["summary"] == r["summary"] and "enrichment" not in p


def test_indexed_documents_are_summarised_while_others_wait_and_summary_goes_into_embeddings(client, corpus, drain, fake_llm):
    url, calls, _ = fake_llm
    e = enricher(url)
    doc = one_doc()
    with db.pool().connection() as c:
        assert requeue(c, doc["id"])
    assert e.run_once() and len(calls) == 1  # another, already indexed document; `doc` waits for its index
    drain()
    with db.pool().connection() as c:
        runtime.save(c, {"context_chars": 300}, None)
        # only `doc` is left to summarise
        c.execute("UPDATE documents SET enrichment = jsonb_build_object('summary', 'x', 'version_id', "
                  "indexed_version_id::text, 'model', 'fake', 'prompt', %s) WHERE id <> %s",
                  (enrich.PROMPT_VERSION, doc["id"]))
    assert e.run_once() and not e.run_once()
    with db.pool().connection() as c:
        job = c.execute("SELECT status FROM jobs WHERE document_id = %s ORDER BY id DESC LIMIT 1", (doc["id"],)).fetchone()
        runtime.save(c, {"context_chars": 0}, None)
    assert job["status"] == "queued"  # re-indexed once, with the summary in its passage embeddings
    drain()
    with db.pool().connection() as c:
        en = c.execute("SELECT enrichment FROM documents WHERE id = %s", (doc["id"],)).fetchone()["enrichment"]
    assert en["summary"]  # same version: the summary stays valid after the re-index, no loop
    assert not e.run_once()


def test_failures_are_retried_then_given_up(client, corpus, fake_llm):
    url, _, reply = fake_llm
    reply["content"] = "Sure! Here is a summary of the document."
    e = enricher(url)
    with db.pool().connection() as c:
        total = c.execute("SELECT count(*) AS n FROM documents WHERE status = 'indexed'").fetchone()["n"]
    assert e.run_once() and e.state == "error: reply is not JSON"
    assert run_all(e) + 1 == total * enrich.MAX_ATTEMPTS
    with db.pool().connection() as c:
        assert enrich.status(c, "fake") == {"summarised": 0, "pending": 0, "failed": total}
        en = c.execute("SELECT enrichment FROM documents WHERE status = 'indexed' LIMIT 1").fetchone()["enrichment"]
    assert en["attempts"] == enrich.MAX_ATTEMPTS and "not JSON" in en["error"]


def test_off_when_disabled_or_without_a_model(client, corpus, fake_llm, tmp_path):
    url, calls, _ = fake_llm
    with db.pool().connection() as c:
        runtime.save(c, {"enrich_enabled": False}, None)
    e = enricher(url)
    assert not e.run_once() and e.state == "off" and not calls
    none = enrich.Enricher(state.settings.model_copy(update={"llm_url": "", "model_cache": tmp_path}))
    assert not none.available and not none.run_once() and none.state == "no model"


@pytest.mark.parametrize("title,vague", [("Overview", True), ("index", True), ("Q3 report final.docx", True),
                                         ("Page 3", True), ("12", True), ("Payment retry runbook", False),
                                         ("Kubernetes Services", False)])
def test_vague_titles(title, vague):
    assert enrich.vague_title(title) is vague


def test_clearer_title_only_when_it_is_clearer():
    assert enrich.clearer_title("beta.txt", "Cobalt Gate Opening Schedule") == "Cobalt Gate Opening Schedule"
    assert enrich.clearer_title("beta.txt", "beta.txt") is None and enrich.clearer_title("ledger-config.txt", "Ledger Config") is None
    assert enrich.clearer_title("Overview", "Overview") is None and enrich.clearer_title("Overview", "") is None
    assert enrich.clearer_title("Ledger Service", "Ledger Service Guide") is None  # own title is fine


def test_parse_reply_tolerates_thinking_and_extra_text():
    raw = '<think>hmm</think>Here you go: {"title": "T", "summary": " A  b. ", "topics": ["x", "", "y"]}'
    assert enrich.parse_reply(raw) == {"title": "T", "summary": "A b.", "topics": ["x", "y"], "type": "other"}


def test_a_model_server_that_cannot_start_is_reported_not_retried(client, corpus, drain, fake_llm, tmp_path):
    drain()
    (tmp_path / "llm" / "bin" / "b1").mkdir(parents=True)
    (tmp_path / "llm" / "bin" / "b1" / "llama-server").write_text("not a program")  # not executable
    (tmp_path / "llm" / "tiny.gguf").write_bytes(b"GGUF")
    e = enrich.Enricher(state.settings.model_copy(update={"llm_url": "", "model_cache": tmp_path}))
    assert e.available and e.model == "tiny.gguf"
    assert not e.run_once() and e.state.startswith("error: the local language model could not start")
    assert not e.run_once() and e.server.proc is None  # not retried until the app restarts
    with db.pool().connection() as c:
        assert not c.execute("SELECT 1 FROM documents WHERE enrich_claimed_at IS NOT NULL OR enrichment IS NOT NULL").fetchone()


def test_without_a_model_the_default_one_is_downloaded_in_the_background(client, corpus, drain, monkeypatch, tmp_path):
    drain()
    calls = []

    def fake_download(settings, name=enrich.DEFAULT_LLM):
        calls.append(name)
        if len(calls) == 1:
            raise OSError("network unreachable")
        d = enrich.llm_dir(settings)
        (d / "bin" / "b1").mkdir(parents=True)
        (d / "bin" / "b1" / "llama-server").write_text("")
        (d / "model.gguf").write_bytes(b"GGUF")
        (d / "selected.txt").write_text("model.gguf")

    monkeypatch.setattr(enrich, "download", fake_download)
    settings = state.settings.model_copy(update={"llm_url": "", "model_cache": tmp_path})
    e = enrich.Enricher(settings)
    assert not e.available and not e.run_once() and "could not be downloaded" in e.state
    assert not e.run_once() and len(calls) == 1 and "could not be downloaded" in e.state  # not retried this run
    with db.pool().connection() as c:
        assert "could not be downloaded" in enrich.describe(e, c, True, [])
    e = enrich.Enricher(settings)  # next start: retried, then used
    assert e.run_once() and e.available and e.model == "model.gguf" and len(calls) == 2
    assert enrich.Enricher(settings.model_copy(update={"offline": True, "model_cache": tmp_path / "x"})).run_once() is False
    assert len(calls) == 2  # never downloads offline


def test_documents_list_shows_the_summary_stage(client, corpus, drain, fake_llm, monkeypatch):
    url, _, _ = fake_llm
    drain()
    e = enricher(url)
    monkeypatch.setattr(state, "enricher", e, raising=False)
    root = token(client, "root")
    docs = client.get("/api/documents", headers=root).json()
    assert {d["summary_stage"] for d in docs if d["status"] == "indexed"} == {"waiting"}
    assert not any(d["summary_text"] for d in docs)
    run_all(e)
    docs = client.get("/api/documents", headers=root).json()
    assert {d["summary_stage"] for d in docs if d["status"] == "indexed"} == {"done"}
    assert {d["summary_text"] for d in docs if d["status"] == "indexed"} == {"How payments are retried. Covers backoff."}
    assert not any(d["adding_summary"] for d in docs if d["status"] != "indexed")
    with db.pool().connection() as c:
        runtime.save(c, {"enrich_enabled": False}, None)
    assert all(d["summary_stage"] is None for d in client.get("/api/documents", headers=root).json())


def test_type_and_topics_reuse_the_collections_names(client, corpus, drain, fake_llm):
    url, calls, reply = fake_llm
    reply["content"] = json.dumps({**REPLY, "type": "Runbook"})
    e = enricher(url)
    run_all(e)
    assert "runbook (operational steps" in calls[0]["messages"][0]["content"]
    assert any("Reuse these topic names" in c["messages"][0]["content"] and "payments; retries" in c["messages"][0]["content"]
               for c in calls[1:])  # later documents of a collection see its topics
    root = token(client, "root")
    r = search(client, root, "ERR-PAY-4012")["results"][0]
    assert r["doc_type"] == "runbook"
    assert {d["doc_type"] for d in client.get("/api/documents", headers=root).json() if d["status"] == "indexed"} == {"runbook"}

    # a new prompt version: labels are rewritten, the summary already in the embeddings stays (no re-index)
    doc = one_doc()
    reply["content"] = json.dumps({"title": "x", "type": "how to", "summary": "A different summary.",
                                   "topics": ["Payment", "RETRIES", "ledger"]})
    with db.pool().connection() as c:
        runtime.save(c, {"context_chars": 300}, None)
        c.execute("UPDATE documents SET enrichment = jsonb_set(enrichment, '{prompt}', '2') WHERE id = %s", (doc["id"],))
        jobs = c.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]
    assert e.run_once() and not e.run_once()
    with db.pool().connection() as c:
        en = c.execute("SELECT enrichment FROM documents WHERE id = %s", (doc["id"],)).fetchone()["enrichment"]
        assert c.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == jobs
        runtime.save(c, {"context_chars": 0}, None)
    assert en["summary"] == "How payments are retried. Covers backoff." and en["prompt"] == enrich.PROMPT_VERSION
    assert en["type"] == "how-to" and en["topics"] == ["payments", "retries", "ledger"]  # known spellings


def test_only_within_its_hours(client, corpus, drain, fake_llm):
    import time

    url, calls, _ = fake_llm
    e = enricher(url)
    now = time.localtime().tm_hour
    with db.pool().connection() as c:
        runtime.save(c, runtime.validate({"enrich_hours": f"{(now + 1) % 24}-{(now + 2) % 24}"}), None)
    assert not e.run_once() and e.state.startswith("waiting for its hours") and not calls
    with db.pool().connection() as c:
        runtime.save(c, runtime.validate({"enrich_hours": f"{now}:00 - {(now + 1) % 24}"}), None)
    assert e.run_once() and calls


@pytest.mark.parametrize("window, hour, inside", [("", 3, True), ("22-6", 23, True), ("22-6", 5, True),
                                                  ("22-6", 6, False), ("22-6", 12, False), ("9-17", 9, True),
                                                  ("9-17", 17, False)])
def test_hours_window(window, hour, inside):
    assert enrich.in_hours(window, hour) is inside


def test_hours_setting_is_checked():
    assert runtime.validate({"enrich_hours": " 22:00 – 06:00 "}) == {"enrich_hours": "22-6"}
    assert runtime.validate({"enrich_hours": ""}) == {"enrich_hours": ""}
    for bad in ("22", "25-6", "6-6", "night"):
        with pytest.raises(ValueError):
            runtime.validate({"enrich_hours": bad})


def test_types_and_topic_spellings():
    def kind(t):
        return enrich.parse_reply(json.dumps({"summary": "s", "type": t}))["type"]

    assert [kind(t) for t in ("Runbook", "How to", "decision_record", "blog post", "")] == \
        ["runbook", "how-to", "decision record", "other", "other"]
    assert enrich.topic_key("Payment-Services") == enrich.topic_key("payment services") == "payment service"
    assert enrich.topic_key("access") == "access"  # not a plural
    assert enrich.align_topics(["Payment Services", "payment-service", "Ledger"], ["payment services"]) == \
        ["payment services", "Ledger"]
