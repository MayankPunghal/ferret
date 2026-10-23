"""Gate 1: search-only vertical slice over the fictional fixture corpus."""

import socket

from knowledge_search.config import Settings

from .conftest import CORPUS, collection_id, search, token


def test_exact_identifier_query(client, corpus):
    res = search(client, token(client, "alice"), "ERR-PAY-4012")
    assert res["outcome"] == "evidence_found"
    top = res["results"][0]
    assert top["title"] == "Payments Service Error Code Reference"
    assert "ERR-PAY-4012" in top["heading_path"]
    assert "exact_identifier" in top["match_basis"]
    assert top["anchor"] == "err-pay-4012-card-authorization-timeout"


def test_api_path_query(client, corpus):
    res = search(client, token(client, "alice"), "POST /api/v2/invoices/{id}/void")
    assert res["outcome"] == "evidence_found"
    assert res["results"][0]["heading_path"].endswith("Void an invoice")


def test_conceptual_paraphrase_query(client, corpus):
    res = search(client, token(client, "alice"), "what should I do when the card processor is too slow to respond")
    titles = [r["title"] for r in res["results"][:3]]
    assert "Payments Service Error Code Reference" in titles


def test_pdf_result_opens_real_source_page(client, corpus):
    h = token(client, "alice")
    res = search(client, h, "why was checkout unavailable in March 2025")
    pdf = [r for r in res["results"] if r["media_type"] == "application/pdf"]
    assert pdf and pdf[0]["rank"] <= 3 and pdf[0]["page"] in (1, 2)
    src = client.get(f"/api/documents/{pdf[0]['document_id']}/source", headers=h)
    assert src.status_code == 200 and src.headers["content-type"] == "application/pdf"
    assert src.content == (CORPUS / "engineering" / "incident-2025-031-postmortem.pdf").read_bytes()
    passage = client.get(f"/api/chunks/{pdf[0]['chunk_id']}", headers=h).json()
    assert passage["page"] == pdf[0]["page"] and passage["context"]
    src = client.get(f"/api/documents/{pdf[0]['document_id']}/source", headers=h)
    # Chrome's PDF viewer refuses sandboxed documents; nosniff keeps it a PDF
    assert src.headers["content-type"] == "application/pdf" and src.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" not in src.headers["content-security-policy"]  # the app's own policy (object-src 'self')


def test_markdown_source_is_served_inert(client, corpus):
    h = token(client, "alice")
    r = search(client, h, "ERR-PAY-5001")["results"][0]
    src = client.get(f"/api/documents/{r['document_id']}/source", headers=h)
    assert src.status_code == 200
    assert src.headers["content-type"].startswith("text/plain")
    assert src.headers["content-security-policy"] == "sandbox"
    assert src.content == (CORPUS / "engineering" / "payments-error-codes.md").read_bytes()


def test_unknown_identifier_abstains(client, corpus):
    res = search(client, token(client, "alice"), "ERR-PAY-4013")
    assert res["outcome"] == "no_adequate_evidence"
    assert "query_identifier_not_found" in res["reason_codes"]
    assert not any(r["supported"] for r in res["results"])


def test_unrelated_query_abstains(client, corpus):
    res = search(client, token(client, "alice"), "what is the budget for trade show booths next year")
    assert res["outcome"] == "no_adequate_evidence"
    assert "couldn't find adequate supporting evidence" in res["message"]


def test_conflicting_versions_flagged(client, corpus):
    res = search(client, token(client, "alice"), "daily meal allowance per diem for domestic business travel")
    assert res["outcome"] == "conflicting_evidence"
    assert "same_series_different_versions" in res["reason_codes"]


def test_restricted_collection_is_invisible(client, corpus):
    bob, hannah = token(client, "bob"), token(client, "hannah")
    q = "how many weeks of paid parental leave"
    assert all(r["collection"] != "hr" for r in search(client, bob, q)["results"])
    hr_res = search(client, hannah, q)
    assert hr_res["outcome"] == "evidence_found" and hr_res["results"][0]["collection"] == "hr"
    leaked = hr_res["results"][0]
    # Direct object access by an unauthorized user looks exactly like a missing object.
    for path in (f"/api/documents/{leaked['document_id']}", f"/api/documents/{leaked['document_id']}/source",
                 f"/api/chunks/{leaked['chunk_id']}"):
        r = client.get(path, headers=bob)
        assert r.status_code == 404
        assert r.json() == client.get(path.replace(leaked["document_id"], "00000000-0000-0000-0000-000000000000")
                                      .replace(str(leaked["chunk_id"]), "999999999"), headers=bob).json()
    # Restricted identifiers are not even exact-matchable.
    res = search(client, bob, "PROJECT-KESTREL-7")
    assert res["outcome"] == "no_adequate_evidence"
    assert all(r["collection"] != "hr" and not r["supported"] for r in res["results"])
    # Listing and status never include the restricted collection.
    assert all(d["collection"] != "hr" for d in client.get("/api/documents", headers=bob).json())
    assert "hr" not in [c["name"] for c in client.get("/api/status", headers=bob).json()["collections"]]
    assert client.get("/api/admin/status", headers=bob).status_code == 404


def test_filtering_to_unauthorized_collection_equals_nonexistent(client, corpus):
    bob = token(client, "bob")
    a = search(client, bob, "parental leave", collection_ids=[collection_id("hr")])
    b = search(client, bob, "parental leave", collection_ids=[987654])
    assert a["outcome"] == b["outcome"] == "no_searchable_scope"
    assert a["results"] == b["results"] == []


def test_permission_revocation_applies_immediately(client, corpus):
    from knowledge_search import auth, db

    bob = token(client, "bob")
    assert search(client, bob, "ERR-PAY-4012")["outcome"] == "evidence_found"
    with db.pool().connection() as c:
        auth.remove_user_from_group(c, "bob", "staff")
        c.commit()
    try:
        res = search(client, bob, "ERR-PAY-4012")
        assert res["outcome"] == "no_searchable_scope" and res["results"] == []
    finally:
        with db.pool().connection() as c:
            auth.add_user_to_group(c, "bob", "staff")
            c.commit()


def test_authentication_and_csrf(client, corpus):
    assert client.post("/api/search", json={"query": "x"}).status_code == 401
    assert client.post("/api/search", json={"query": "x"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-battery"})
    assert r.status_code == 200 and "ks_session" in client.cookies
    assert client.post("/api/search", json={"query": "ERR-PAY-4012"}).status_code == 403  # cookie without CSRF header
    ok = client.post("/api/search", json={"query": "ERR-PAY-4012"}, headers={"x-ks-csrf": "1"})
    assert ok.status_code == 200
    client.cookies.clear()


def test_search_makes_no_outbound_network_calls(client, corpus, monkeypatch):
    """No cloud/provider settings exist, the local language model is off unless installed or configured, and a
    search completes with outbound sockets blocked."""
    fields = " ".join(Settings.model_fields).lower()
    assert not any(w in fields for w in ("openai", "api_key", "provider", "token"))
    assert Settings.model_fields["llm_url"].default == ""

    def blocked(*a, **k):
        raise AssertionError("outbound network call attempted")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    res = search(client, token(client, "alice"), "how do I roll back the orders service")
    assert "Orders Service Deployment Runbook" in [r["title"] for r in res["results"][:3]]


def test_gate_accepts_keyword_searches_only_when_every_term_matches():
    from knowledge_search.search import GateConfig, gate_basis

    g = GateConfig(0.78, 0.64, 0.2, 0, "similarity", "coverage", all_terms_min=0.3)
    s = {"identifier_matches": [], "cosine_similarity": 0.55, "query_term_coverage": 1.0, "idf_coverage": 1.0}
    assert gate_basis(g, s, False) == ["all_query_terms_and_semantic"]  # "parental leave policy"
    assert gate_basis(g, {**s, "query_term_coverage": 0.67}, False) == []  # "sick leave policy": "sick" missing
    assert gate_basis(g, {**s, "cosine_similarity": 0.2}, False) == []
    assert gate_basis(GateConfig(0.78, 0.64, 0.2, 0, "similarity", "coverage"), s, False) == []  # rule off
