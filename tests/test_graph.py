"""Deterministic link graph: extraction, resolution, incoming links, blast radius and permission safety."""

from knowledge_search.parsing import chunk_document, parse_document

from .conftest import collection_id, search, token

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

        g = client.get(f"/api/documents/{ids['service-b.md']}/graph?depth=2", headers=sam).json()
        titles = {n["title"]: n["depth"] for n in g["nodes"]}
        assert titles == {"Service B": 0, "Service A": 1, "Frontend": 2, "Platform Overview": 2}
        by_id = {n["id"]: n["title"] for n in g["nodes"]}
        edges = {(by_id[e["source"]], by_id[e["target"]], e["kind"]) for e in g["edges"]}
        assert ("Service A", "Service B", "depends_on") in edges and ("Frontend", "Service A", "depends_on") in edges
        g_root = client.get(f"/api/documents/{ids['service-b.md']}/graph", headers=root).json()
        assert "Secret Project" in {n["title"] for n in g_root["nodes"]}  # readable for root only
        assert client.get(f"/api/documents/{secret_id}/graph", headers=sam).status_code == 404

        # deleting a document removes its edges; re-uploading restores them
        client.delete(f"/api/documents/{ids['frontend.md']}", headers=sam)
        imp = client.get(f"/api/documents/{ids['service-b.md']}/impact?depth=3", headers=sam).json()
        assert [n["title"] for n in imp["nodes"]] == ["Service A"]
    finally:
        client.delete(f"/api/documents/{secret_id}", headers=root)
        for d in ids.values():
            client.delete(f"/api/documents/{d}", headers=sam)


def test_windows_line_endings_front_matter_and_links():
    """Files checked out on Windows have CRLF endings; front matter must still be parsed, not indexed."""
    crlf = WIKI["service-a.md"].replace(b"\n", b"\r\n")
    doc = parse_document(crlf, "service-a.md")
    assert doc.title == "Service A" and doc.metadata["title"] == "Service A"
    chunks = chunk_document(doc)
    assert not any("---" in c.text or "depends_on" in c.text for c in chunks)
    assert ("depends_on", "service-b.md") in [(lk.kind, lk.target) for c in chunks for lk in c.links]
    old_mac = parse_document(b"---\rtitle: T\rseries: s\r---\r# T\r\rBody text.\r", "t.md")
    assert old_mac.metadata == {"title": "T", "series": "s"} and "Body text." in old_mac.sections[0].text


SECRET = b"---\ntitle: Secret Project\ndepends_on: service-b.md\n---\n# Secret\n\nBudget codename aurora.\n"


def test_link_expansion_in_search_is_permission_safe(client, drain):
    """Search follows links of the documents a query names; restricted neighbours are never reached."""
    sam, root = token(client, "sam"), token(client, "root")
    ids = _upload_wiki(client, drain, sam)
    secret = client.post("/api/documents", headers=root, data={"collection_id": collection_id("hr")},
                         files={"file": ("secret-project.md", SECRET)}).json()["document_id"]
    drain()

    def linked(headers, query):
        return {(r["title"], r["signals"]["linked_from"]) for r in search(client, headers, query)["results"]
                if "linked_from" in r["signals"]}

    try:
        assert client.get("/api/admin/settings", headers=root).json()["values"]["graph_expansion"] is True
        assert ("Service A", "Service B") in linked(sam, "What depends on Service B?")  # incoming link
        assert ("Service B", "Frontend") not in linked(sam, "Frontend")  # two hops away: not expanded
        assert ("Service A", "Frontend") in linked(sam, "Frontend")
        assert not any(t == "Secret Project" for t, _ in linked(sam, "What depends on Service B?"))
        assert ("Secret Project", "Service B") in linked(root, "What depends on Service B?")
        client.put("/api/admin/settings", headers=root, json={"graph_expansion": False})
        assert linked(root, "What depends on Service B?") == set()
    finally:
        client.put("/api/admin/settings", headers=root, json={"graph_expansion": True})
        client.delete(f"/api/documents/{secret}", headers=root)
        for d in ids.values():
            client.delete(f"/api/documents/{d}", headers=sam)


PLAIN = {  # crawled-style pages without declared links: connected only by naming each other's titles
    "workorders.md": b"# Work Orders Service\n\nCreates maintenance work orders. Each order references equipment "
                     b"looked up in the Assets Service.\n",
    "assets.md": b"# Asset Service\n\nKeeps the register of equipment.\n\n## Runtime\n\nRuns on port 8410.\n",
    "overview.md": b"# Overview\n\nWork orders and assets are part of maintenance.\n",
}


def test_mentions_link_pages_without_declared_links(client, drain):
    """A passage naming another page's title (stemmed: "Assets Service" ~ "Asset Service") links the pages
    for search expansion and the graph view; one-word titles and restricted pages are never linked."""
    sam, root = token(client, "sam"), token(client, "root")
    ids = {}
    for name, body in PLAIN.items():
        ids[name] = client.post("/api/documents", headers=sam, data={"collection_id": collection_id("scratch")},
                                files={"file": (name, body)}).json()["document_id"]
    secret = client.post("/api/documents", headers=root, data={"collection_id": collection_id("hr")},
                         files={"file": ("audit.md", b"# Audit Plan\n\nReview the Work Orders Service.\n")}
                         ).json()["document_id"]
    drain()
    try:
        g = client.get(f"/api/documents/{ids['workorders.md']}/graph", headers=sam).json()
        by_id = {n["id"]: n["title"] for n in g["nodes"]}
        assert set(by_id.values()) == {"Work Orders Service", "Asset Service"}  # not "Overview", not restricted
        assert {(by_id[e["source"]], by_id[e["target"]], e["kind"]) for e in g["edges"]} == {
            ("Work Orders Service", "Asset Service", "mentions")}
        g_root = client.get(f"/api/documents/{ids['workorders.md']}/graph", headers=root).json()
        assert "Audit Plan" in {n["title"] for n in g_root["nodes"]}  # incoming mention, readable for root
        men = client.get(f"/api/documents/{ids['assets.md']}/links", headers=sam).json()["mentions"]
        assert [(m["title"], m["how"]) for m in men] == [("Work Orders Service", "mentions this")]
        res = search(client, sam, "Which port does the work orders service run on?")["results"]
        assert any(r["title"] == "Asset Service" and r["signals"].get("linked_from") == "Work Orders Service"
                   for r in res)
    finally:
        client.delete(f"/api/documents/{secret}", headers=root)
        for d in ids.values():
            client.delete(f"/api/documents/{d}", headers=sam)
