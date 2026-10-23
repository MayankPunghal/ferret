"""HTML and Word documents: structure, links into the graph, safe source serving, invalid files."""

import io
import zipfile

from knowledge_search.parsing import (
    ParseError,
    chunk_document,
    normalize_target,
    parse_document,
)

from .conftest import collection_id, search, token

HTML = b"""<!doctype html><html><head><title>Orders API | Docs</title><style>p{color:red}</style>
<script>alert(1)</script></head><body><nav><a href="/">Home</a> Navigation menu</nav>
<main><h1 id="orders-api">Orders API</h1><p>The <code>orders-svc</code> exposes <b>POST /v1/orders</b>.
See the <a href="../payments/">Payments service</a> and the <a href="runbook.html#rollback">rollback steps</a>.</p>
<h2>Errors</h2><ul><li>ERR-ORD-4001 invalid basket</li><li>ERR-ORD-4002 out of stock</li></ul>
<table><tr><th>Code</th><th>Meaning</th></tr><tr><td>409</td><td>duplicate order</td></tr></table>
<pre>curl -X POST /v1/orders</pre></main><footer>Copyright</footer></body></html>"""


def _docx(paragraphs: list[tuple[str, str]], title: str = "") -> bytes:
    """Minimal valid .docx: (style, text) paragraphs; style '' = normal, 'Heading1', 'ListParagraph'."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(
        f'<w:p>{f"<w:pPr><w:pStyle w:val=\"{s}\"/></w:pPr>" if s else ""}<w:r><w:t>{t}</w:t></w:r></w:p>'
        for s, t in paragraphs)
    table = "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Port</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>8412</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}{table}</w:body></w:document>')
        if title:
            z.writestr("docProps/core.xml", '<cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                       f"<dc:title>{title}</dc:title></cp:coreProperties>")
    return buf.getvalue()


def _pptx(slides: list[tuple[str, list[str], str]]) -> bytes:
    """Minimal valid .pptx: (title, body paragraphs, speaker notes) per slide, in presentation order."""
    ns = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    rel = '<Relationship Id="{}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{}" Target="{}"/>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{}</Relationships>'

    def shape(text, ph=""):
        paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in text)
        return (f'<p:sp><p:nvSpPr><p:nvPr>{f"<p:ph type={chr(34)}{ph}{chr(34)}/>" if ph else ""}</p:nvPr></p:nvSpPr>'
                f"<p:txBody>{paras}</p:txBody></p:sp>")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # stored in reverse file order: the presentation's own order must win
        ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i}"/>' for i in range(len(slides)))
        z.writestr("ppt/presentation.xml", f"<p:presentation {ns}><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>")
        z.writestr("ppt/_rels/presentation.xml.rels", rels.format("".join(
            rel.format(f"rId{i}", "slide", f"slides/slide{len(slides) - i}.xml") for i in range(len(slides)))))
        for i, (title, body, notes) in enumerate(slides):
            n = len(slides) - i
            z.writestr(f"ppt/slides/slide{n}.xml", f"<p:sld {ns}><p:cSld><p:spTree>{shape([title], 'title')}"
                       f"{shape(body)}</p:spTree></p:cSld></p:sld>")
            if notes:
                z.writestr(f"ppt/slides/_rels/slide{n}.xml.rels",
                           rels.format(rel.format("rId1", "notesSlide", f"../notesSlides/notesSlide{n}.xml")))
                z.writestr(f"ppt/notesSlides/notesSlide{n}.xml", f"<p:notes {ns}><p:cSld><p:spTree>"
                           f"{shape([notes])}{shape([str(n)])}</p:spTree></p:cSld></p:notes>")
    return buf.getvalue()


def test_html_structure_and_links():
    doc = parse_document(HTML, "orders-api.html")
    assert doc.title == "Orders API | Docs"
    text = "\n".join(s.text for s in doc.sections)
    assert "Navigation menu" not in text and "alert" not in text and "Copyright" not in text and "color" not in text
    assert [s.heading_path for s in doc.sections] == ["Orders API", "Orders API > Errors"]
    assert "- ERR-ORD-4001 invalid basket" in text and "409 | duplicate order" in text and "curl -X POST" in text
    links = {(lk.target, lk.anchor) for c in chunk_document(doc) for lk in c.links}
    assert links == {("payments", None), ("runbook.html", "rollback")}
    assert "err-ord-4001" in chunk_document(doc)[1].idents


def test_link_targets_of_doc_sites():
    assert normalize_target("../payments/") == "payments"
    assert normalize_target("payments/index.html") == "payments"
    assert normalize_target("Payments%20Service.md?x=1") == "payments service.md"


def test_docx_headings_lists_tables():
    data = _docx([("Title", "Orders Runbook"), ("Heading1", "Rollback"), ("", "Run hctl rollback orders-svc."),
                  ("ListParagraph", "Check the error rate"), ("Heading2", "Ports")], title="")
    doc = parse_document(data, "runbook.docx")
    assert doc.title == "Orders Runbook"
    paths = [s.heading_path for s in doc.sections]
    assert paths == ["Rollback", "Rollback > Ports"]  # Title and Heading 1 are both level 1
    text = "\n".join(s.text for s in doc.sections)
    assert "- Check the error rate" in text and "Port | 8412" in text
    assert parse_document(_docx([("", "x")], title="Core Title"), "a.docx").title == "Core Title"


def test_pptx_slides_become_sections_with_notes():
    data = _pptx([("IT Induction", ["Welcome to the IT team"], ""),
                  ("VPN access", ["Install FortiClient", "Use your SSO login"], "Tickets go to the IT Helpdesk in Jira"),
                  ("", ["Questions?"], "")])
    doc = parse_document(data, "it-induction.pptx")
    assert doc.title == "IT Induction"
    assert [s.heading_path for s in doc.sections] == ["Slide 1: IT Induction", "Slide 2: VPN access", "Slide 3"]
    vpn = doc.sections[1].text
    assert "- Install FortiClient" in vpn and "Notes: Tickets go to the IT Helpdesk in Jira" in vpn
    assert "2" not in vpn.split("Notes:")[1]  # the notes page's slide number is not text


def test_invalid_office_files_are_rejected():
    for data, name, code in [(b"\xd0\xcf\x11\xe0 legacy doc", "x.docx", "invalid_docx"),
                             (b"PK\x03\x04 broken", "x.docx", "invalid_docx"),
                             (b"\xd0\xcf\x11\xe0 legacy ppt", "x.pptx", "invalid_pptx")]:
        try:
            parse_document(data, name)
        except ParseError as e:
            assert e.code == code
        else:
            raise AssertionError(f"accepted an invalid {name}")


def test_html_and_docx_upload_search_and_safe_source(client, drain):
    sam = token(client, "sam")
    ids = {}
    for name, data in [("orders-api.html", HTML), ("runbook.docx", _docx([("Heading1", "Rollback"),
                                                                           ("", "Run hctl rollback orders-svc.")]))]:
        r = client.post("/api/documents", headers=sam, data={"collection_id": collection_id("scratch")},
                        files={"file": (name, data)})
        assert r.status_code == 202, r.text
        ids[name] = r.json()["document_id"]
    drain()
    try:
        assert search(client, sam, "ERR-ORD-4002")["results"][0]["document_id"] == ids["orders-api.html"]
        assert search(client, sam, "hctl rollback")["results"][0]["document_id"] == ids["runbook.docx"]
        html = client.get(f"/api/documents/{ids['orders-api.html']}/source", headers=sam)
        assert html.headers["content-type"].startswith("text/html")
        assert html.headers["content-security-policy"].startswith("sandbox; default-src 'none'")
        docx = client.get(f"/api/documents/{ids['runbook.docx']}/source", headers=sam)
        assert docx.headers["content-disposition"].startswith("attachment")
    finally:
        for d in ids.values():
            client.delete(f"/api/documents/{d}", headers=sam)


def test_loose_front_matter_is_read_line_by_line():
    """Hand-written front matter that is not strict YAML must not reject the document."""
    from knowledge_search.parsing import parse_document

    doc = parse_document(b"---\ntitle: Example: Deploying Cassandra\nweight: 30\n---\n# Example\n\nBody.\n", "c.md")
    assert doc.title == "Example: Deploying Cassandra" and "Body." in doc.sections[0].text


def test_custom_heading_anchor_and_generic_version_numbers():
    from knowledge_search.parsing import extract_idents, parse_document

    doc = parse_document(b"# T\n\n## Version support {#os-support}\n\nText.\n", "t.md")
    assert [(s.heading_path, s.anchor) for s in doc.sections] == [("T > Version support", "os-support")]
    assert extract_idents("Kubernetes 2.0 release date") == []  # a bare x.y number is not an identifier
    assert extract_idents("orders-svc v3.8.2 and 1.29.3") == ["1.29.3", "v3.8.2"]
    # lowerCamelCase keys count, also inside field paths; capitalised words (JavaScript, GitHub) do not
    assert extract_idents("set `.spec.concurrencyPolicy` like concurrencyPolicy in JavaScript") == [
        "concurrencypolicy", "spec.concurrencypolicy"]


def test_document_summary_in_passage_embeddings_is_a_setting():
    from knowledge_search import runtime
    from knowledge_search.parsing import (
        chunk_document,
        doc_summary,
        embedding_text,
        parse_document,
    )

    doc = parse_document(b"# Assets\n\nThe asset service tracks equipment.\n\n## API\n\nGET /assets lists them.\n", "a.md")
    assert doc_summary(doc, 0) == "" and doc_summary(doc, 300) == "The asset service tracks equipment."
    described = parse_document(b"---\ndescription: Tracks equipment.\n---\n# Assets\n\nText.\n", "b.md")
    assert doc_summary(described, 300) == "Tracks equipment."
    api = chunk_document(doc, 900, 0)[-1]
    assert "tracks equipment" in embedding_text(doc.title, api, doc_summary(doc, 300))
    rt = runtime.defaults()
    plain = rt.signature()
    rt.context_chars = 300
    assert rt.signature() != plain  # changing it marks documents outdated until reindexed


def test_crawled_page_and_its_deck_are_searchable_and_connected(client, drain):
    """A site2md copy: the page keeps its web address, the attached deck is searched slide by slide and
    linked from the page."""
    sam = token(client, "sam")
    page = ("---\ntitle: IT Induction\nsource_url: https://sites.google.com/acme.com/intranet/it/induction\n"
            "crawled_at: 2026-10-23T10:00:00+00:00\n---\n\n# IT Induction\n\nEverything new joiners need.\n\n"
            "## Attached documents\n\n- [IT Induction deck](IT%20Induction%20deck.pptx)\n").encode()
    deck = _pptx([("IT Induction", ["Welcome"], ""), ("VPN access", ["Install FortiClient on day one"], "")])
    ids = {}
    for name, data in [("it-induction.md", page), ("IT Induction deck.pptx", deck)]:
        r = client.post("/api/documents", headers=sam, data={"collection_id": collection_id("scratch")},
                        files={"file": (name, data)})
        assert r.status_code == 202, r.text
        ids[name] = r.json()["document_id"]
    drain()
    try:
        doc = client.get(f"/api/documents/{ids['it-induction.md']}", headers=sam).json()
        assert doc["source_uri"] == "https://sites.google.com/acme.com/intranet/it/induction"
        hit = search(client, sam, "FortiClient")["results"][0]
        assert hit["document_id"] == ids["IT Induction deck.pptx"] and hit["heading_path"] == "Slide 2: VPN access"
        links = client.get(f"/api/documents/{ids['it-induction.md']}/links", headers=sam).json()
        assert [o["document_id"] for o in links["outgoing"]] == [ids["IT Induction deck.pptx"]]
        src = client.get(f"/api/documents/{ids['IT Induction deck.pptx']}/source", headers=sam)
        assert src.headers["content-disposition"].startswith("attachment")
    finally:
        for d in ids.values():
            client.delete(f"/api/documents/{d}", headers=sam)
