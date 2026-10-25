"""Gate 1/2: ingestion lifecycle — idempotency, updates, deletes, failures, retries, degraded states.

Uses the private ``scratch`` collection (only user ``sam``) so it does not disturb corpus tests.
"""

import numpy as np

from knowledge_search import db
from knowledge_search.api import state
from knowledge_search.embedder import EmbeddingError

from .conftest import collection_id, search, token

V1 = b"# Widget Calibration\n\nThe zephyr widget must be calibrated every 90 days using torque wrench TW-88.\n"
V2 = b"# Widget Calibration\n\nThe zephyr widget must be calibrated every 30 days using laser gauge LG-12.\n"


def upload(client, h, name, data, cid=None):
    return client.post("/api/documents", headers=h, data={"collection_id": cid or collection_id("scratch")},
                       files={"file": (name, data)})


def chunk_rows(doc_id):
    with db.pool().connection() as c:
        return c.execute("SELECT version_id, text FROM chunks WHERE document_id = %s", (doc_id,)).fetchall()


def test_ingest_update_unchanged_delete(client, drain):
    sam = token(client, "sam")
    r = upload(client, sam, "widget.md", V1)
    assert r.status_code == 202 and r.json()["outcome"] == "queued"
    doc_id = r.json()["document_id"]

    # Before indexing: absence cannot be established -> coverage state, not "no evidence".
    res = search(client, sam, "how often is the zephyr widget calibrated")
    assert res["outcome"] == "index_coverage_incomplete"
    assert res["coverage"]["pending"] == 1

    assert drain() == 1
    res = search(client, sam, "TW-88")
    assert res["outcome"] == "evidence_found" and res["results"][0]["document_id"] == doc_id

    # Identical bytes: no new version, no job.
    r = upload(client, sam, "widget.md", V1)
    assert r.json()["outcome"] == "unchanged" and drain() == 0

    # Changed content: new version replaces old chunks atomically; nothing stale or duplicated.
    r = upload(client, sam, "widget.md", V2)
    assert r.json()["outcome"] == "queued" and r.json()["document_id"] == doc_id
    assert drain() == 1
    rows = chunk_rows(doc_id)
    assert len({row["version_id"] for row in rows}) == 1 and all("LG-12" in row["text"] for row in rows)
    old = search(client, sam, "TW-88")
    assert old["outcome"] == "no_adequate_evidence"
    assert not any(r["supported"] or "TW-88" in r["snippet"] for r in old["results"])
    new = search(client, sam, "LG-12")
    assert new["outcome"] == "evidence_found" and new["results"][0]["version_no"] == 2

    # Delete propagates to chunks, search, source and stored original.
    sha = client.get(f"/api/documents/{doc_id}", headers=sam).json()["versions"][0]["sha256"]
    assert client.delete(f"/api/documents/{doc_id}", headers=sam, ).status_code == 200
    assert chunk_rows(doc_id) == []
    assert all(r["document_id"] != doc_id for r in search(client, sam, "LG-12")["results"])
    assert client.get(f"/api/documents/{doc_id}/source", headers=sam).status_code == 404
    assert not state.blobs.path(sha).exists()


def test_rejected_and_failed_ingestion_are_explicit(client, drain):
    sam = token(client, "sam")
    r = upload(client, sam, "malware.exe", b"MZ....")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unsupported_type"
    assert upload(client, sam, "empty.md", b"").json()["detail"]["code"] == "empty_file"

    r = upload(client, sam, "broken.pdf", b"%PDF-1.4\nthis is not really a pdf")
    doc_id = r.json()["document_id"]
    drain()
    d = client.get(f"/api/documents/{doc_id}", headers=sam).json()
    assert d["status"] == "failed" and d["error_code"] in ("invalid_pdf", "no_extractable_text")
    # A failed document makes scope coverage incomplete; searches must not claim absence.
    res = search(client, sam, "anything about quantum flux capacitors")
    assert res["outcome"] == "index_coverage_incomplete" and res["coverage"]["failed"] == 1
    # Manual retry re-queues; deterministic parse failures fail again without looping.
    assert client.post(f"/api/documents/{doc_id}/retry", headers=sam).status_code == 200
    assert drain() == 1
    assert client.get(f"/api/documents/{doc_id}", headers=sam).json()["status"] == "failed"
    assert client.post(f"/api/documents/{doc_id}/retry", headers={**sam}).status_code == 200
    client.delete(f"/api/documents/{doc_id}", headers=sam)
    drain()
    assert search(client, sam, "anything about quantum flux capacitors")["coverage"]["complete"]


def test_transient_embedding_failure_is_retried(client, drain, monkeypatch):
    sam = token(client, "sam")
    original = state.embedder.embed_documents

    def boom(texts):
        raise EmbeddingError("simulated runtime failure")

    monkeypatch.setattr(state.embedder, "embed_documents", boom)
    doc_id = upload(client, sam, "retry.md", b"# Retry\n\nThe quokka protocol uses port 7781.\n").json()["document_id"]
    drain()
    with db.pool().connection() as c:
        job = c.execute("SELECT status, attempts, error_code FROM jobs WHERE document_id = %s", (doc_id,)).fetchone()
        assert (job["status"], job["attempts"], job["error_code"]) == ("queued", 1, "embedding_failed")
        c.execute("UPDATE jobs SET run_after = now() WHERE document_id = %s", (doc_id,))
        c.commit()
    monkeypatch.setattr(state.embedder, "embed_documents", original)
    assert drain() == 1
    assert search(client, sam, "quokka protocol port")["outcome"] == "evidence_found"
    client.delete(f"/api/documents/{doc_id}", headers=sam)


def test_write_permission_required(client, corpus):
    bob = token(client, "bob")  # read-only on engineering
    r = upload(client, bob, "x.md", b"# x\n\nhello world text", cid=collection_id("engineering"))
    assert r.status_code == 404
    doc = client.get("/api/documents", headers=bob).json()[0]
    assert client.delete(f"/api/documents/{doc['id']}", headers=bob).status_code == 404


def test_dense_failure_is_degraded_not_absence(client, corpus, monkeypatch):
    def boom(q):
        raise EmbeddingError("model unavailable")

    monkeypatch.setattr(state.embedder, "embed_query", boom)
    alice = token(client, "alice")
    res = search(client, alice, "what is the budget for trade show booths next year")
    assert res["outcome"] == "retrieval_degraded" and "dense_branch_failed" in res["reason_codes"]
    # Exact identifiers still work via lexical/exact branches.
    res = search(client, alice, "ERR-PAY-4012")
    assert res["outcome"] == "evidence_found" and "results_may_be_incomplete" in res["reason_codes"]


def test_backend_failure_is_explicit(client, corpus, monkeypatch):
    import knowledge_search.api as api

    def boom(*a, **k):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(api, "search", boom)
    r = client.post("/api/search", headers=token(client, "alice"), json={"query": "ERR-PAY-4012"})
    assert r.status_code == 503
    assert r.json()["outcome"] == "retrieval_failure" and "exploded" not in r.text


def test_indexing_job_reports_progress(client, drain):
    sam = token(client, "sam")
    body = ("# Progress\n\n" + "\n\n".join(f"Paragraph {i} about the vermilion pipeline stage {i}." * 6
                                         for i in range(30))).encode()
    doc = upload(client, sam, "progress.md", body).json()["document_id"]
    listed = next(d for d in client.get("/api/documents", headers=sam).json() if d["id"] == doc)
    assert listed["status"] == "pending" and listed["queue_position"] == 0
    drain()
    with db.pool().connection() as c:
        job = c.execute("SELECT stage, progress_done, progress_total FROM jobs WHERE document_id = %s", (doc,)).fetchone()
        n = c.execute("SELECT count(*) AS n FROM chunks WHERE document_id = %s", (doc,)).fetchone()["n"]
    assert job["stage"] == "saving" and job["progress_done"] == job["progress_total"] == n > 1
    client.delete(f"/api/documents/{doc}", headers=sam)


def test_documents_are_filtered_sorted_and_paged_past_1000(client, corpus):
    """The Documents tab pages through the server; API clients without `limit` still get the newest 1,000."""
    root = token(client, "root")
    with db.pool().connection() as c:
        c.execute("INSERT INTO documents (collection_id, source_key, title, status) "
                  "SELECT %s, 'bulk-' || i, 'Bulk ' || i, 'failed' FROM generate_series(1, 1050) i", (collection_id("scratch"),))
    try:
        r = client.get("/api/documents?limit=100&status=failed&q=bulk", headers=root)
        assert len(r.json()) == 100 and r.headers["x-total-count"] == "1050"
        n = client.get("/api/documents/counts?q=bulk", headers=root).json()
        assert n["matching"] == 1050 and n["failed"] >= 1050 and n["all"] > 1050
        assert len(client.get("/api/documents", headers=root).json()) == 1000
        page = client.get("/api/documents?q=bulk&sort=name&desc=false&limit=3&offset=1", headers=root).json()
        assert [d["title"] for d in page] == ["Bulk 10", "Bulk 100", "Bulk 1000"]
        bob = token(client, "bob")  # cannot read scratch
        assert client.get("/api/documents/counts?q=bulk", headers=bob).json()["matching"] == 0
    finally:
        with db.pool().connection() as c:
            c.execute("DELETE FROM documents WHERE source_key LIKE 'bulk-%%'")


def test_paused_indexing_leaves_jobs_queued(client, drain):
    from knowledge_search import runtime

    sam = token(client, "sam")
    upload(client, sam, "paused.md", b"# Paused\n\nWaiting for the pause to end.\n")
    with db.pool().connection() as c:
        runtime.save(c, {"indexing_paused": True}, None)
    try:
        drain()
        with db.pool().connection() as c:
            assert c.execute("SELECT count(*) AS n FROM jobs WHERE status = 'queued'").fetchone()["n"] >= 1
    finally:
        with db.pool().connection() as c:
            runtime.save(c, {"indexing_paused": False}, None)
    drain()
    with db.pool().connection() as c:
        assert c.execute("SELECT count(*) AS n FROM jobs WHERE status = 'queued'").fetchone()["n"] == 0


def test_an_edit_embeds_only_the_changed_passages(client, drain, monkeypatch):
    """Passages whose embedding text and model are unchanged keep their vectors (plan-large-documents step 3)."""
    from knowledge_search import runtime

    sam = token(client, "sam")
    parts = lambda changed: "# Parts\n\n" + "\n\n".join(  # noqa: E731
        f"## Part {i}\n\n" + f"Notes on the {'edited ' if i == changed else ''}widget zone {i}, part {i}. " * 25 for i in range(4))
    doc = upload(client, sam, "parts.md", parts(-1).encode()).json()["document_id"]
    drain()
    calls = []
    with db.pool().connection() as c:
        e = runtime.embedder(runtime.load(c).embed_model)  # the one the worker uses
    real = e.embed_documents
    monkeypatch.setattr(e, "embed_documents", lambda texts: calls.append(len(texts)) or real(texts))
    upload(client, sam, "parts.md", parts(2).encode())
    drain()
    with db.pool().connection() as c:
        rows = c.execute("SELECT heading_path, text, embedding FROM chunks WHERE document_id = %s", (doc,)).fetchall()
    changed = [r for r in rows if r["heading_path"].endswith("Part 2")]
    assert changed and sum(calls) == len(changed) < len(rows)
    # the kept vectors are the ones embedding everything again gives
    from knowledge_search.ingest import requeue

    with db.pool().connection() as c:
        c.execute("UPDATE chunks SET embed_hash = NULL WHERE document_id = %s", (doc,))
        requeue(c, doc)
    calls.clear()
    drain()
    assert sum(calls) == len(rows)
    with db.pool().connection() as c:
        again = {r["text"]: r["embedding"] for r in c.execute("SELECT text, embedding FROM chunks WHERE document_id = %s", (doc,))}
    # same vectors up to batch padding (other passages in the batch change the last digits; macOS more than others)
    cos = lambda a, b: float(a @ b / np.linalg.norm(a) / np.linalg.norm(b))  # noqa: E731
    assert all(cos(again[r["text"]].to_numpy(), r["embedding"].to_numpy()) > 0.999 for r in rows)
    assert search(client, sam, "edited widget zone 2")["results"][0]["document_id"] == doc
    client.delete(f"/api/documents/{doc}", headers=sam)
