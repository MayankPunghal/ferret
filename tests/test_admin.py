"""Admin features: first-run setup, runtime settings (chunking/reranker), reindex, users and access."""

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
    for path in ("/api/admin/settings", "/api/admin/users", "/api/admin/collections", "/api/admin/status"):
        assert client.get(path, headers=alice).status_code == 404
    assert client.put("/api/admin/settings", headers=alice, json={"chunk_size": 500}).status_code == 404


def test_settings_validation(client, corpus):
    root = token(client, "root")
    for bad in ({"chunk_size": 50}, {"chunk_overlap": 5000}, {"embed_model": "evil/model"},
                {"rerank_model": "x"}, {"gate_mode": "magic"}, {"nonsense": 1},
                {"chunk_size": 300, "chunk_overlap": 300}):
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
        assert all(r["signals"]["rerank_score"] is not None for r in res["results"])
        assert res["results"][0]["title"] == "Orders Service Deployment Runbook"
        top = res["results"][0]
        assert top["text"] and top["passage_url"] == f"/api/chunks/{top['chunk_id']}"
        assert top["source_url"].startswith(f"/api/documents/{top['document_id']}/source")
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
    assert client.put(f"/api/admin/collections/{cid}/grants", headers=root,
                      json={"group": "tmpgrp", "can_write": True}).status_code == 200
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
    me_root = client.get("/api/me", headers=root).json()
    root_id = next(u["id"] for u in client.get("/api/admin/users", headers=root).json() if u["username"] == "root")
    assert client.patch(f"/api/admin/users/{root_id}", headers=root, json={"disabled": True}).status_code == 409
    assert me_root["is_admin"]
    with db.pool().connection() as c:
        c.execute("DELETE FROM users WHERE username = 'tmpuser'")
        c.execute("DELETE FROM collections WHERE name = 'tmpcol'")
        c.commit()
