"""Admin features: first-run setup, runtime settings (chunking/reranker), reindex, users and access."""

import time

import knowledge_search.api as api
from knowledge_search import db

from .conftest import PASSWORD, search, token


def test_first_run_setup_only_without_admin_and_only_locally(client, corpus, monkeypatch):
    assert client.get("/api/setup").json() == {"needs_admin": False}
    body = {"username": "firstadmin", "password": "first-admin-password"}
    assert client.post("/api/setup/admin", json=body).status_code == 409
    with db.pool().connection() as c:
        c.execute("UPDATE users SET is_admin = false WHERE username = 'root'")
        c.commit()
    try:
        assert client.get("/api/setup").json() == {"needs_admin": True}
        monkeypatch.setattr(api, "LOOPBACK", set())
        assert client.post("/api/setup/admin", json=body).status_code == 403  # remote clients refused
        monkeypatch.undo()
        r = client.post("/api/setup/admin", json=body)
        assert r.status_code == 200 and r.json()["token"]
        assert client.get("/api/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()["is_admin"]
        assert client.post("/api/setup/admin", json={**body, "username": "second"}).status_code == 409
    finally:
        client.cookies.clear()
        with db.pool().connection() as c:
            c.execute("UPDATE users SET is_admin = true WHERE username = 'root'")
            c.execute("DELETE FROM users WHERE username = 'firstadmin'")
            c.commit()


def test_admin_routes_hidden_from_non_admins(client, corpus):
    alice = token(client, "alice")
    for path in ("/api/admin/settings", "/api/admin/users", "/api/admin/collections", "/api/admin/status",
                 "/api/admin/updates"):
        assert client.get(path, headers=alice).status_code == 404
    assert client.put("/api/admin/settings", headers=alice, json={"chunk_size": 500}).status_code == 404


def test_updates_status_without_network(client, corpus):
    root = token(client, "root")
    u = client.get("/api/admin/updates", headers=root).json()
    assert u["current"] and not u["windows_app"] and not u["can_install"]
    assert client.post("/api/admin/updates/install", headers=root).status_code == 400  # not the Windows app


def test_settings_validation(client, corpus):
    root = token(client, "root")
    for bad in ({"chunk_size": 50}, {"chunk_overlap": 5000}, {"embed_model": "evil/model"},
                {"rerank_model": "x"}, {"gate_mode": "magic"}, {"nonsense": 1},
                {"chunk_size": 300, "chunk_overlap": 300}, {"context_chars": 5000}):
        assert client.put("/api/admin/settings", headers=root, json=bad).status_code == 422, bad


def test_chunking_change_marks_outdated_and_reindex_restores(client, corpus, drain):
    root, alice = token(client, "root"), token(client, "alice")
    before = client.get("/api/admin/settings", headers=root).json()
    assert before["index"]["outdated"] == 0 and before["index"]["complete"]
    with db.pool().connection() as c:
        chunks_before = c.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
    try:
        s = client.put("/api/admin/settings", headers=root, json={"chunk_size": 300, "chunk_overlap": 0}).json()
        assert s["values"]["chunk_size"] == 300 and s["index"]["outdated"] == s["index"]["documents"] > 0
        # absence can no longer be established while documents are outdated
        res = search(client, alice, "who won the company football tournament")
        assert res["outcome"] == "index_coverage_incomplete"
        docs = client.get("/api/documents", headers=alice).json()
        assert all(d["needs_reindex"] for d in docs)
        queued = client.post("/api/admin/reindex", headers=root, json={"all": False}).json()["queued"]
        assert queued == s["index"]["documents"]
        drain()
        after = client.get("/api/admin/settings", headers=root).json()["index"]
        assert after["outdated"] == 0 and after["complete"]
        with db.pool().connection() as c:
            assert c.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"] > chunks_before  # smaller chunks
        assert search(client, alice, "ERR-PAY-4012")["outcome"] == "evidence_found"
    finally:
        client.put("/api/admin/settings", headers=root, json={"chunk_size": 900, "chunk_overlap": 150})
        client.post("/api/admin/reindex", headers=root, json={"all": False})
        drain()
    assert client.get("/api/admin/settings", headers=root).json()["index"]["complete"]


def test_reranker_search_path(client, corpus):
    root, alice = token(client, "root"), token(client, "alice")
    try:
        client.put("/api/admin/settings", headers=root, json={
            "rerank_enabled": True, "rerank_model": "Xenova/ms-marco-MiniLM-L-6-v2", "rerank_candidates": 10,
            "gate_mode": "rerank_and_similarity", "gate_rerank_min": 0.0})
        res = search(client, alice, "how do I roll back the orders service")
        assert res["index"]["reranker"] == "Xenova/ms-marco-MiniLM-L-6-v2" and "rerank" in res["timings_ms"]
        assert all(r["signals"]["rerank_score"] is not None for r in res["results"]
                   if "more_documents" not in r["signals"]["branches"])
        assert res["results"][0]["title"] == "Orders Service Deployment Runbook"
        top = res["results"][0]
        assert top["text"] and top["passage_url"] == f"/api/chunks/{top['chunk_id']}"
        assert top["source_url"].startswith(f"/api/documents/{top['document_id']}/view")
        # identifier rules still apply with the reranker gate
        assert search(client, alice, "ERR-PAY-4013")["outcome"] == "no_adequate_evidence"
    finally:
        client.put("/api/admin/settings", headers=root, json={"rerank_enabled": False, "gate_mode": "similarity"})


def test_collections_users_and_revocation(client, corpus):
    root = token(client, "root")
    r = client.post("/api/admin/collections", headers=root, json={"name": "tmpcol", "description": "temp"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert client.post("/api/admin/collections", headers=root, json={"name": "tmpcol"}).status_code == 409
    grant = {"group": "tmpgrp", "can_write": True}
    assert client.put(f"/api/admin/collections/{cid}/grants", headers=root, json=grant).status_code == 400  # no such group
    assert client.post("/api/admin/groups", headers=root, json={"name": "tmpgrp"}).status_code == 201
    assert client.post("/api/admin/groups", headers=root, json={"name": "tmpgrp"}).status_code == 409
    assert client.put(f"/api/admin/collections/{cid}/grants", headers=root, json=grant).status_code == 200
    assert client.post("/api/admin/users", headers=root,  # users join existing groups only
                       json={"username": "tmpuser", "password": PASSWORD, "groups": ["tmpgrq"]}).status_code == 400
    r = client.post("/api/admin/users", headers=root,
                    json={"username": "tmpuser", "password": PASSWORD, "groups": ["tmpgrp"]})
    assert r.status_code == 201
    uid = r.json()["id"]
    t = token(client, "tmpuser")
    me = client.get("/api/me", headers=t).json()
    assert [c["name"] for c in me["collections"]] == ["tmpcol"] and me["collections"][0]["can_write"]
    assert client.patch(f"/api/admin/users/{uid}", headers=root, json={"groups": []}).status_code == 200
    assert client.get("/api/me", headers=t).status_code == 401  # signed out by the change
    assert client.get("/api/me", headers=token(client, "tmpuser")).json()["collections"] == []
    assert client.patch(f"/api/admin/users/{uid}", headers=root, json={"groups": ["tmpgrp"]}).status_code == 200
    t = token(client, "tmpuser")
    assert client.delete("/api/admin/groups/tmpgrp", headers=root).status_code == 200  # its members lose its access
    assert client.get("/api/me", headers=t).status_code == 401
    assert client.get("/api/me", headers=token(client, "tmpuser")).json()["collections"] == []
    me_root = client.get("/api/me", headers=root).json()
    root_id = next(u["id"] for u in client.get("/api/admin/users", headers=root).json() if u["username"] == "root")
    assert client.patch(f"/api/admin/users/{root_id}", headers=root, json={"disabled": True}).status_code == 409
    assert me_root["is_admin"]
    with db.pool().connection() as c:
        c.execute("DELETE FROM users WHERE username = 'tmpuser'")
        c.execute("DELETE FROM collections WHERE name = 'tmpcol'")
        c.commit()


def test_model_list_and_download_requests(client, corpus, monkeypatch):
    """Settings lists every model with whether it is on disk; Download fetches one without selecting it."""
    from knowledge_search import admin, runtime

    h = token(client, "root")
    assert client.get("/api/admin/models", headers=token(client, "bob")).status_code == 404
    m = client.get("/api/admin/models", headers=h).json()
    embed = {x["name"]: x for x in m["embed"]}
    assert embed["BAAI/bge-small-en-v1.5"]["downloaded"] and embed["BAAI/bge-small-en-v1.5"]["in_use"]
    assert embed["BAAI/bge-small-en-v1.5"]["state"] == "ready"
    assert set(m) == {"embed", "rerank", "llm"} and all(not x["in_use"] for x in m["llm"])
    assert client.post("/api/admin/models/download", headers=h, json={"kind": "embed", "name": "nope"}).status_code == 404
    assert client.post("/api/admin/models/download", headers=h, json={"kind": "other", "name": "x"}).status_code == 422
    calls = []
    monkeypatch.setattr(runtime, "download", lambda kind, name: calls.append((kind, name)))
    r = client.post("/api/admin/models/download", headers=h, json={"kind": "rerank", "name": "BAAI/bge-reranker-base"})
    assert r.json() == {"state": "downloading"}
    for _ in range(50):
        if calls and "rerank:BAAI/bge-reranker-base" not in admin._downloads:
            break
        time.sleep(0.05)
    assert calls == [("rerank", "BAAI/bge-reranker-base")]
    assert client.get("/api/admin/settings", headers=h).json()["values"]["rerank_model"] != "BAAI/bge-reranker-base"


def test_first_start_while_the_search_model_downloads(client, corpus, drain, monkeypatch):
    """The app opens before the model is on disk: search answers at once from keywords and says why; a Download
    after a failed first download loads the model and re-queues documents that failed for want of it."""
    from knowledge_search import runtime
    from knowledge_search.deps import state

    emb, h = state.embedder, token(client, "root")
    monkeypatch.setattr(runtime, "downloaded", lambda kind, name: False)
    monkeypatch.setattr(emb, "state", "loading (first use downloads the model)")
    assert client.get("/api/status", headers=token(client, "alice")).json()["search_model"] == "downloading"
    res = search(client, token(client, "alice"), "ERR-PAY-4012")
    assert "search_model_downloading" in res["reason_codes"]
    assert res["results"][0]["title"] == "Payments Service Error Code Reference"  # keywords still work

    monkeypatch.setattr(emb, "state", "error: could not download")
    with db.pool().connection() as c:
        doc = c.execute("SELECT id FROM documents WHERE status = 'indexed' LIMIT 1").fetchone()["id"]
        c.execute("UPDATE documents SET status = 'failed', error_code = 'embedding_failed' WHERE id = %s", (doc,))
        c.commit()
    monkeypatch.setattr(runtime, "download", lambda kind, name: None)
    warmed = []
    monkeypatch.setattr(emb, "warm", lambda: warmed.append(1))
    r = client.post("/api/admin/models/download", headers=h, json={"kind": "embed", "name": emb.spec.name})
    assert r.json() == {"state": "downloading"}
    for _ in range(100):
        with db.pool().connection() as c:
            st = c.execute("SELECT status FROM documents WHERE id = %s", (doc,)).fetchone()["status"]
        if st == "pending":
            break
        time.sleep(0.05)
    assert warmed and st == "pending"
    monkeypatch.setattr(emb, "state", "ready")
    drain()
    with db.pool().connection() as c:
        assert c.execute("SELECT status FROM documents WHERE id = %s", (doc,)).fetchone()["status"] == "indexed"


def test_delete_collection_removes_its_documents(client, drain):
    root = token(client, "root")
    cid = client.post("/api/admin/collections", headers=root, json={"name": "to-delete"}).json()["id"]
    doc = client.post("/api/documents", headers=root, data={"collection_id": cid},
                      files={"file": ("gone.md", b"# Gone\n\nZebra-quartz-77 lives here.\n")}).json()["document_id"]
    drain()
    assert client.delete(f"/api/admin/collections/{cid}", headers=token(client, "alice")).status_code == 404
    assert client.delete(f"/api/admin/collections/{cid}", headers=root).json() == {"deleted_documents": 1}
    assert client.get(f"/api/documents/{doc}", headers=root).status_code == 404
