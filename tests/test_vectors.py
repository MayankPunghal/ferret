"""In-memory vector search (laptop profile) vs pgvector in SQL, and database-failure handling."""

import psycopg

from knowledge_search import db, runtime, vectors
from knowledge_search.api import state

from .conftest import search, token

QUERIES = ["how do I roll back the orders service", "why was checkout unavailable in March 2025",
           "how long are raw events kept", "deadline for submitting expense claims",
           "what does the Orders Service depend on"]  # names a linked wiki page: exercises link expansion


def test_memory_and_database_vector_search_agree(client, corpus, monkeypatch):
    alice = token(client, "alice")
    assert runtime.base().vector_search == "memory"
    mem = [[r["chunk_id"] for r in search(client, alice, q)["results"]] for q in QUERIES]
    mem_sims = [search(client, alice, q)["results"][0]["signals"]["cosine_similarity"] for q in QUERIES]
    monkeypatch.setattr(runtime.base(), "vector_search", "database")
    sql = [[r["chunk_id"] for r in search(client, alice, q)["results"]] for q in QUERIES]
    sql_sims = [search(client, alice, q)["results"][0]["signals"]["cosine_similarity"] for q in QUERIES]
    assert mem == sql
    assert any("linked_from" in r["signals"] for r in search(client, alice, QUERIES[-1])["results"])
    assert all(abs(a - b) < 1e-3 for a, b in zip(mem_sims, sql_sims))


def test_memory_index_drops_deleted_chunks(client, drain):
    sam = token(client, "sam")
    from .conftest import collection_id

    r = client.post("/api/documents", headers=sam, data={"collection_id": collection_id("scratch")},
                    files={"file": ("vec.md", b"# Vec\n\nThe marmalade relay restarts nightly at 02:00.\n")})
    doc = r.json()["document_id"]
    drain()
    assert search(client, sam, "when does the marmalade relay restart")["results"][0]["document_id"] == doc
    client.delete(f"/api/documents/{doc}", headers=sam)
    res = search(client, sam, "when does the marmalade relay restart")
    assert all(x["document_id"] != doc for x in res["results"])
    with db.pool().connection() as c:
        n = c.execute("SELECT count(*) AS n FROM chunks WHERE embedding_model = %s",
                      (state.embedder.spec.version_key,)).fetchone()["n"]
    assert len(vectors.index._spaces[state.embedder.spec.version_key].ids) == n


def test_database_failure_is_a_clean_503(client, corpus, monkeypatch):
    alice = token(client, "alice")

    def boom(*a, **k):
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr("knowledge_search.auth.load_principal", boom)
    r = client.post("/api/search", headers=alice, json={"query": "ERR-PAY-4012"})
    assert r.status_code == 503 and r.json()["outcome"] == "retrieval_failure"
    r = client.get("/api/documents", headers=alice)
    assert r.status_code == 503 and "temporarily unavailable" in r.json()["detail"]
