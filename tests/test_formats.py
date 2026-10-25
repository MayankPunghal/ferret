"""HTML and Word documents: structure, links into the graph, safe source serving, invalid files."""

import io
import zipfile

import pytest

from knowledge_search.parsing import (
    ParseError,
    chunk_document,
    normalize_target,
    parse_document,
)
from knowledge_search.slides import render

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
                  ("ListParagraph", "Check the error rate"), ("", "On call</w:t><w:br/><w:t>Asha Rao"),
                  ("Heading2", "Ports")], title="")
    doc = parse_document(data, "runbook.docx")
    assert doc.title == "Orders Runbook"
    paths = [s.heading_path for s in doc.sections]
    assert paths == ["Rollback", "Rollback > Ports"]  # Title and Heading 1 are both level 1
    text = "\n".join(s.text for s in doc.sections)
    assert "- Check the error rate" in text and "On call Asha Rao" in text
    assert "Port\n\n8412" in text  # a one-row table is layout, kept as text (tables: tests/test_tables.py)
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


def _org_chart_pptx() -> bytes:
    """Slide 1: a SmartArt organisation chart (data part only, as PowerPoint stores it). Slide 2: the same drawn by
    hand, boxes joined by connector lines (begin/end glued to the boxes)."""
    ns = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    rel = '<Relationship Id="{}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{}" Target="{}"/>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{}</Relationships>'
    people = {"1": "Asha Rao, CEO", "2": "Chander Mohan, Chief Financial Officer", "3": "Neelu Singh, Accounts",
              "4": "Ravi Kumar, CTO"}
    pts = '<dgm:pt modelId="0" type="doc"><dgm:prSet loTypeId="urn:microsoft.com/office/officeart/2005/8/layout/orgChart1" loCatId="hierarchy"/></dgm:pt>'
    pts += "".join(f'<dgm:pt modelId="{k}"><dgm:t><a:p><a:r><a:t>{v.split(", ")[0]}</a:t></a:r></a:p>'
                   f'<a:p><a:r><a:t>{v.split(", ")[1]}</a:t></a:r></a:p></dgm:t></dgm:pt>' for k, v in people.items())
    pts += '<dgm:pt modelId="9" type="pres"><dgm:t><a:p><a:r><a:t>layout box</a:t></a:r></a:p></dgm:t></dgm:pt>'
    cxns = "".join(f'<dgm:cxn modelId="c{d}" srcId="{s}" destId="{d}" srcOrd="{o}"/>'
                   for s, d, o in [("0", "1", 0), ("1", "4", 1), ("1", "2", 0), ("2", "3", 0)])
    cxns += '<dgm:cxn modelId="p" type="presOf" srcId="1" destId="9"/>'
    data = (f'<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
            f'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><dgm:ptLst>{pts}</dgm:ptLst>'
            f"<dgm:cxnLst>{cxns}</dgm:cxnLst></dgm:dataModel>")
    frame = ('<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Diagram"/></p:nvGraphicFramePr>'
             '<p:xfrm><a:off x="0" y="0"/><a:ext cx="9000000" cy="5000000"/></p:xfrm><a:graphic><a:graphicData>'
             '<dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" r:dm="rId2"/>'
             "</a:graphicData></a:graphic></p:graphicFrame>")

    def box(i, x, y, text):
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="{i}" name="b{i}"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm>'
                f'<a:off x="{x}" y="{y}"/><a:ext cx="2000000" cy="600000"/></a:xfrm></p:spPr>'
                f"<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>")

    def line(a, b):  # drawn from the lower box up, to check the upper box becomes the parent anyway
        return (f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{a}{b}" name="c"/><p:cNvCxnSpPr><a:stCxn id="{b}" idx="0"/>'
                f'<a:endCxn id="{a}" idx="2"/></p:cNvCxnSpPr><p:nvPr/></p:nvCxnSpPr><p:spPr><a:xfrm><a:off x="2000000" '
                f'y="600000"/><a:ext cx="3000000" cy="900000"/></a:xfrm><a:prstGeom prst="line"/></p:spPr></p:cxnSp>')

    title = '<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>{}</a:t></a:r></a:p></p:txBody></p:sp>'
    # the top box has a line break (Shift+Enter) between the name and the role, not a comma
    drawn = (box(10, 4000000, 0, "Asha Rao</a:t></a:r><a:br/><a:r><a:t>CEO") + box(11, 1000000, 1500000, "Chander Mohan, CFO")
             + box(12, 7000000, 1500000, "Ravi Kumar, CTO") + box(13, 1000000, 3000000, "Neelu Singh, Accounts")
             + box(14, 0, 4500000, "Questions: hr@example.com") + line(10, 11) + line(10, 12) + line(11, 13))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/presentation.xml", f'<p:presentation {ns}><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
                   '<p:sldId id="257" r:id="rId2"/></p:sldIdLst></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels", rels.format(rel.format("rId1", "slide", "slides/slide1.xml")
                                                                  + rel.format("rId2", "slide", "slides/slide2.xml")))
        z.writestr("ppt/slides/slide1.xml", f"<p:sld {ns}><p:cSld><p:spTree>{title.format('Leadership')}{frame}"
                   "</p:spTree></p:cSld></p:sld>")
        z.writestr("ppt/slides/_rels/slide1.xml.rels", rels.format(rel.format("rId2", "diagramData", "../diagrams/data1.xml")))
        z.writestr("ppt/diagrams/data1.xml", data)
        z.writestr("ppt/slides/slide2.xml", f"<p:sld {ns}><p:cSld><p:spTree>{title.format('Finance team')}{drawn}"
                   "</p:spTree></p:cSld></p:sld>")
    return buf.getvalue()


def test_org_charts_keep_their_hierarchy():
    doc = parse_document(_org_chart_pptx(), "org-chart.pptx")
    smart, drawn = doc.sections[0].text, doc.sections[1].text
    assert smart.splitlines() == [
        "- Asha Rao, CEO",
        "  - Chander Mohan, Chief Financial Officer (under Asha Rao, CEO)",
        "    - Neelu Singh, Accounts (under Chander Mohan, Chief Financial Officer)",
        "  - Ravi Kumar, CTO (under Asha Rao, CEO)"]  # SmartArt order; its layout boxes are not text
    assert drawn.splitlines() == [
        "- Questions: hr@example.com",
        "- Asha Rao, CEO",
        "  - Chander Mohan, CFO (under Asha Rao, CEO)",
        "    - Neelu Singh, Accounts (under Chander Mohan, CFO)",
        "  - Ravi Kumar, CTO (under Asha Rao, CEO)"]


def test_slides_are_drawn_with_their_text_escaped():
    title = "Rollout &lt;script&gt;alert(1)&lt;/script&gt;"  # the text <script>..., escaped in the XML
    deck = render(_pptx([(title, ["Step one", "Step two"], "Say hello"), ("", ["Questions?"], "")]))
    assert [(s["n"], s["notes"]) for s in deck] == [(1, "Say hello"), (2, "")]
    assert "<script" not in deck[0]["html"] and "&lt;script&gt;alert(1)&lt;/script&gt;" in deck[0]["html"]
    assert "Step two" in deck[0]["html"] and "Questions?" in deck[1]["html"]
    # anchors are the parser's section anchors, so a search result opens at its slide
    sections = parse_document(_pptx([(title, ["x"], ""), ("", ["y"], "")]), "d.pptx").sections
    assert [s.anchor for s in sections] == [s["anchor"] for s in deck] and deck[1]["anchor"] == "slide-2"


def test_org_charts_are_drawn():
    smart, drawn = render(_org_chart_pptx())
    # SmartArt without a stored drawing: its hierarchy as a nested list; boxes and connector lines drawn
    assert "Ravi Kumar, CTO (under Asha Rao, CEO)" in smart["html"]
    assert drawn["html"].count('class="line"') == 3 and "Neelu Singh, Accounts" in drawn["html"]


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
        # readable view: Word rendered as a page (no scripts allowed), HTML and PDF go to the original
        page = client.get(f"/api/documents/{ids['runbook.docx']}/view", headers=sam)
        assert page.headers["content-type"].startswith("text/html") and '<h1 id="rollback">Rollback</h1>' in page.text
        assert "script-src" not in page.headers["content-security-policy"]  # default-src 'none': no scripts
        assert "default-src 'none'" in page.headers["content-security-policy"] and "source?download=1" in page.text
        moved = client.get(f"/api/documents/{ids['orders-api.html']}/view", headers=sam, follow_redirects=False)
        assert moved.status_code == 303 and "/source" in moved.headers["location"]
        down = client.get(f"/api/documents/{ids['orders-api.html']}/source?download=1", headers=sam)
        assert down.headers["content-disposition"].startswith("attachment")
    finally:
        for d in ids.values():
            client.delete(f"/api/documents/{d}", headers=sam)


def test_deck_view_shows_slides_and_a_text_view(client, drain):
    sam = token(client, "sam")
    r = client.post("/api/documents", headers=sam, data={"collection_id": collection_id("scratch")},
                    files={"file": ("org.pptx", _org_chart_pptx())})
    doc = r.json()["document_id"]
    drain()
    try:
        hit = search(client, sam, "Neelu Singh Accounts")["results"][0]
        view, anchor = hit["source_url"].split("#")
        assert view == f"/api/documents/{doc}/view" and anchor.startswith("slide-")  # opens at its slide
        slides = client.get(f"/api/documents/{doc}/view", headers=sam)
        assert slides.headers["content-security-policy"].startswith("default-src 'none'")
        assert slides.text.count('class="slide"') == 2 and f'id="{anchor}"' in slides.text
        assert f'href="/api/documents/{doc}/view?version=indexed&amp;text=1">Text view' in slides.text
        text = client.get(f"/api/documents/{doc}/view?text=1", headers=sam).text
        assert slides.text.endswith("</main></body></html>")
        assert 'class="slide"' not in text and "Chander Mohan, CFO (under Asha Rao, CEO)" in text
        assert f'href="/api/documents/{doc}/view?version=indexed">Slide view' in text
    finally:
        client.delete(f"/api/documents/{doc}", headers=sam)


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
    page = (b"---\ntitle: IT Induction\nsource_url: https://sites.google.com/acme.com/intranet/it/induction\n"
            b"crawled_at: 2026-10-23T10:00:00+00:00\n---\n\n# IT Induction\n\nEverything new joiners need.\n\n"
            b"## Attached documents\n\n- [IT Induction deck](IT%20Induction%20deck.pptx)\n")
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


@pytest.mark.parametrize("meta,filename,title", [
    ("Microsoft Word - Group Health Policy", "x.pdf", "Group Health Policy"),
    ("Microsoft Word- Group Health Policy", "x.pdf", "Group Health Policy"),
    ("US HR Employee Handbook 2026.docx.docx", "h.pdf", "US HR Employee Handbook 2026"),
    ("PowerPoint Presentation", "Leave User Manual.pdf", "Leave User Manual"),
    ("", "6 1Password Usage.pdf", "6 1Password Usage"),
    ("Incident 2025-031 Postmortem: Checkout outage", "a.pdf", "Incident 2025-031 Postmortem: Checkout outage")])
def test_pdf_titles_without_what_the_exporting_program_added(meta, filename, title):
    from knowledge_search.parsing import pdf_title
    assert pdf_title(meta, filename) == title


def test_markdown_view_is_rendered_and_inert():
    from knowledge_search.viewer import page

    out = page("x.md", "---\ntitle: X\n---\n# Heading\n\nText <script>alert(1)</script> [a](javascript:alert(1))\n")
    assert '<h1 id="heading">Heading</h1>' in out and "title: X" not in out
    assert "<script>" not in out and "&lt;script&gt;" in out and 'href="javascript' not in out
    assert "<pre>a &lt;b&gt;</pre>" in page("x.txt", "a <b>", plain=True)


def test_links_in_the_readable_view_open_the_linked_document(client, corpus):
    """ledger-service.md links ledger-config.txt; the view links to that document's view. Unresolvable
    relative links become plain text; web links stay."""
    from knowledge_search.viewer import _relink

    root = token(client, "root")
    docs = {d["source_key"]: d["id"] for d in client.get("/api/documents", headers=root).json()}
    page = client.get(f"/api/documents/{docs['ledger-service.md']}/view", headers=root).text
    assert f'href="/api/documents/{docs["ledger-config.txt"]}/view"' in page
    out = _relink('<a href="missing.md">x</a> <a href="https://example.com/a">y</a> <a href="#top">z</a>', {})
    assert out == '<a>x</a> <a href="https://example.com/a">y</a> <a href="#top">z</a>'


def test_whole_document_text_in_reading_order_for_readers_only(client, corpus):
    alice, bob = token(client, "alice"), token(client, "bob")
    root = token(client, "root")
    docs = {d["source_key"]: d["id"] for d in client.get("/api/documents", headers=root).json()}
    doc = client.get(f"/api/documents/{docs['payments-error-codes.md']}/text", headers=alice).json()
    assert doc["title"] == "Payments Service Error Code Reference" and len(doc["passages"]) >= 2
    assert "ERR-PAY-4012" in " ".join(x["text"] for x in doc["passages"])
    assert client.get(f"/api/documents/{docs['compensation-bands.md']}/text", headers=bob).status_code == 404


def test_pdfs_open_in_the_built_in_viewer(client, corpus):
    root = token(client, "root")
    docs = {d["source_key"]: d["id"] for d in client.get("/api/documents", headers=root).json()}
    pdf = docs["incident-2025-031-postmortem.pdf"]
    page = client.get(f"/api/documents/{pdf}/view", headers=root)
    csp = page.headers["content-security-policy"]
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp and "unsafe-eval'" not in csp.replace("wasm-unsafe-eval", "")
    assert "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert f'data-src="/api/documents/{pdf}/source?version=indexed"' in page.text
    assert '<script type="module" src="/pdfview.js">' in page.text and "Download original" in page.text
    # the viewer and PDF.js are served by the app (offline), as JavaScript modules
    for path in ("/pdfview.js", "/vendor/pdfjs/pdf.min.mjs", "/vendor/pdfjs/pdf.worker.min.mjs"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript"), path
    assert client.get(f"/api/documents/{pdf}/source", headers=root).headers["content-type"] == "application/pdf"
