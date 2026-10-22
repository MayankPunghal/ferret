"""Deterministic link graph: extraction, resolution, incoming links, blast radius and permission safety."""

from knowledge_search.parsing import chunk_document, parse_document

from .conftest import collection_id, token

WIKI = {
    "service-a.md": b"---\ntitle: Service A\ndepends_on: [service-b.md]\n---\n# Service A\n\n"
                    b"Calls the [B API](../svc/service-b.md#api) and is described in [[Platform Overview|overview]]. "
                    b"Docs: [external](https://example.com/x) ![diagram](a.png) [missing](nowhere.md)\n",
    "service-b.md": b"# Service B\n\nOwns the ledger.\n\n## API\n\nPOST /b/charge creates a charge.\n",
    "frontend.md": b"---\ntitle: Frontend\ndepends_on: service-a\n---\n# Frontend\n\nUses Service A for checkout.\n",
    "platform-overview.md": b"# Platform Overview\n\nThe platform consists of several services.\n",
}


def test_link_extraction():
    doc = parse_document(WIKI["service-a.md"], "service-a.md")
    links = [(lk.kind, lk.target, lk.anchor) for c in chunk_document(doc) for lk in c.links]
    assert ("depends_on", "service-b.md", None) in links
    assert ("links_to", "service-b.md", "api") in links
    assert ("links_to", "platform overview", None) in links
    assert ("links_to", "nowhere.md", None) in links
    assert not any("example.com" in t or t == "a.png" for _, t, _ in links)  # external URLs and images ignored


def _upload_wiki(client, drain, headers):
    ids = {}
    for name, body in WIKI.items():
        r = client.post("/api/documents", headers=headers, data={"collection_id": collection_id("scratch")},
                        files={"file": (name, body)})
        ids[name] = r.json()["document_id"]
    drain()
    return ids


def test_links_impact_and_permissions(client, drain):
    sam, root = token(client, "sam"), token(client, "root")
    ids = _upload_wiki(client, drain, sam)
    # a restricted document (hr collection, unreadable for sam) that also depends on service B
    secret = client.post("/api/documents", headers=root, data={"collection_id": collection_id("hr")},
                         files={"file": ("secret-project.md",
                                         b"---\ntitle: Secret Project\ndepends_on: service-b.md\n---\n# Secret\n\nx\n")})
    secret_id = secret.json()["document_id"]
    drain()
    try:
        out = client.get(f"/api/documents/{ids['service-a.md']}/links", headers=sam).json()
        got = {(o["kind"], o["title"], o["anchor"]) for o in out["outgoing"]}
        assert ("depends_on", "Service B", None) in got
        assert ("links_to", "Service B", "api") in got
        assert ("links_to", "Platform Overview", None) in got
        api_link = next(o for o in out["outgoing"] if o["anchor"] == "api")
        passage = client.get(f"/api/chunks/{api_link['chunk_id']}", headers=sam).json()
        assert passage["heading_path"].endswith("API")  # anchor resolves to the right section
        assert [u["target"] for u in out["unresolved"]] == ["nowhere.md"]

        inc_sam = client.get(f"/api/documents/{ids['service-b.md']}/links", headers=sam).json()["incoming"]
        assert {i["title"] for i in inc_sam} == {"Service A"}  # the restricted Secret Project is invisible
        inc_root = client.get(f"/api/documents/{ids['service-b.md']}/links", headers=root).json()["incoming"]
        assert {i["title"] for i in inc_root} == {"Service A", "Secret Project"}

        imp = client.get(f"/api/documents/{ids['service-b.md']}/impact?depth=2", headers=sam).json()
        assert {(n["title"], n["depth"]) for n in imp["nodes"]} == {("Service A", 1), ("Frontend", 2)}
        imp1 = client.get(f"/api/documents/{ids['service-b.md']}/impact?depth=1", headers=sam).json()
        assert [n["title"] for n in imp1["nodes"]] == ["Service A"]
        only_dep = client.get(f"/api/documents/{ids['platform-overview.md']}/impact?kinds=depends_on",
                              headers=sam).json()
        assert only_dep["nodes"] == []  # only a links_to edge points at the overview
        assert client.get(f"/api/documents/{secret_id}/links", headers=sam).status_code == 404
        assert client.get(f"/api/documents/{secret_id}/impact", headers=sam).status_code == 404

        # deleting a document removes its edges; re-uploading restores them
        client.delete(f"/api/documents/{ids['frontend.md']}", headers=sam)
        imp = client.get(f"/api/documents/{ids['service-b.md']}/impact?depth=3", headers=sam).json()
        assert [n["title"] for n in imp["nodes"]] == ["Service A"]
    finally:
        client.delete(f"/api/documents/{secret_id}", headers=root)
        for d in ids.values():
            client.delete(f"/api/documents/{d}", headers=sam)
