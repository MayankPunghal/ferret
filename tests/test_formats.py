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


def test_invalid_office_files_are_rejected():
    for data, code in [(b"\xd0\xcf\x11\xe0 legacy doc", "invalid_docx"), (b"PK\x03\x04 broken", "invalid_docx")]:
        try:
            parse_document(data, "x.docx")
        except ParseError as e:
            assert e.code == code
        else:
            raise AssertionError("accepted an invalid .docx")


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
