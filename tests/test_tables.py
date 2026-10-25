"""Tables from web pages, Word and PowerPoint stay tables (merged cells repeated, a header row), and passages
never cut a table in the middle (docs/plan-tables.md)."""

import io
import zipfile

from site2md.html import html_to_markdown

from knowledge_search.parsing import chunk_document, parse_document

AWS = """<main><h2>Quotas</h2><table><caption>Transform quotas</caption>
<thead><tr><th>Workload</th><th>Dimension</th><th>Quota</th><th>Adjustable</th></tr></thead>
<tbody><tr><td rowspan="3">Mainframe</td><td>Jobs</td><td>10</td><td>Yes</td></tr>
<tr><td>Lines of code for each job</td><td>30,000,000</td><td>No</td></tr>
<tr><td>Files | parts</td><td><p>One</p><p>Two</p></td><td><a href="/x">see</a> <code>q</code></td></tr>
<tr><td colspan="2">Windows</td><td>5<td>Yes</tr></tbody></table><p>After.</p></main>"""


def md(html: str) -> str:
    return html_to_markdown(html)[1]


def test_html_table_with_merged_cells_header_and_caption():
    assert md(AWS) == """## Quotas

**Transform quotas**

| Workload | Dimension | Quota | Adjustable |
|---|---|---|---|
| Mainframe | Jobs | 10 | Yes |
| Mainframe | Lines of code for each job | 30,000,000 | No |
| Mainframe | Files \\| parts | One<br>Two | [see](/x) `q` |
| Windows | Windows | 5 | Yes |

After."""


def test_header_row_without_thead_or_th_and_short_rows_padded():
    assert md("<table><tr><td>a</td><td>b</td></tr><tr><td>1</td></tr></table>") == "| a | b |\n|---|---|\n| 1 |  |"
    assert md("<table><tbody><tr><td>1</td><td>2</td></tr></tbody><thead><tr><th>a</th><th>b</th></tr></thead></table>") \
        .startswith("| a | b |")  # the header comes first wherever <thead> is in the source


def test_layout_tables_stay_text():
    one_column = md("<table><tr><td>Only</td></tr><tr><td>row 2</td></tr></table>")
    assert one_column == "Only\n\nrow 2"
    assert md("<table><tr><td>a</td><td>b</td></tr></table>") == "a\n\nb"  # one row
    nested = md("<table><tr><td><table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table></td>"
                "<td>side</td></tr></table>")
    assert nested == "| a | b |\n|---|---|\n| 1 | 2 |\n\nside"
    with_list = md("<table><tr><td><ul><li>x</li></ul></td><td>y</td></tr><tr><td>1</td><td>2</td></tr></table>")
    assert "|" not in with_list and "- x" in with_list


def _docx_table(rows: str) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body><w:tbl>{rows}</w:tbl></w:body></w:document>')
    return buf.getvalue()


def _tc(text: str, pr: str = "") -> str:
    return f"<w:tc>{f'<w:tcPr>{pr}</w:tcPr>' if pr else ''}<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def test_word_table_with_merged_cells():
    rows = ("<w:tr>" + _tc("Workload") + _tc("Dimension") + _tc("Quota") + "</w:tr>"
            "<w:tr>" + _tc("Mainframe", '<w:vMerge w:val="restart"/>') + _tc("Jobs") + _tc("10") + "</w:tr>"
            "<w:tr>" + _tc("", "<w:vMerge/>") + _tc("Lines") + _tc("30,000,000") + "</w:tr>"
            "<w:tr>" + _tc("Windows", '<w:gridSpan w:val="2"/>') + _tc("5") + "</w:tr>")
    text = parse_document(_docx_table(rows), "q.docx").sections[0].text
    assert text == ("| Workload | Dimension | Quota |\n|---|---|---|\n| Mainframe | Jobs | 10 |\n"
                    "| Mainframe | Lines | 30,000,000 |\n| Windows | Windows | 5 |")


def test_powerpoint_table_with_a_merged_cell():
    ns = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    tc = lambda t, a="": f"<a:tc{a}><a:txBody><a:p><a:r><a:t>{t}</a:t></a:r></a:p></a:txBody></a:tc>"  # noqa: E731
    tbl = ("<a:tbl><a:tr>" + tc("Region") + tc("Owner") + "</a:tr><a:tr>" + tc("EU", ' rowSpan="2"') + tc("Asha")
           + "</a:tr><a:tr>" + tc("", ' vMerge="1"') + tc("Ben") + "</a:tr></a:tbl>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/presentation.xml", f'<p:presentation {ns}><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>')
        z.writestr("ppt/slides/slide1.xml", f"<p:sld {ns}><p:cSld><p:spTree><p:graphicFrame><a:graphic><a:graphicData>{tbl}"
                   "</a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>")
    text = parse_document(buf.getvalue(), "owners.pptx").sections[0].text
    assert "| Region | Owner |\n|---|---|\n| EU | Asha |\n| EU | Ben |" in text


def test_a_long_table_is_split_between_rows_with_its_header():
    rows = "".join(f"<tr><td>row {i}</td><td>value {i} of the quota table</td></tr>" for i in range(200))
    html = f"<main><h1>Limits</h1><p>Intro text.</p><table><caption>Limits</caption><tr><th>Name</th><th>Value</th></tr>{rows}</table></main>"
    doc = parse_document(html.encode(), "limits.html")
    parts = [c.text for c in chunk_document(doc, size=900, overlap=150) if "row " in c.text]
    assert len(parts) > 3
    for p in parts:
        table = p[p.index("**Limits**"):]
        lines = table.split("\n")
        assert lines[2:4] == ["| Name | Value |", "|---|---|"]  # caption, blank, header
        assert all(x.startswith("| row ") and x.endswith(" of the quota table |") for x in lines[4:])  # whole rows only
    seen = [x for p in parts for x in p.split("\n") if x.startswith("| row ")]
    assert len(seen) == 200 == len(set(seen))  # every row once: no overlap inside the table


def test_a_table_that_fits_stays_in_one_passage_without_overlap_before_it():
    rows = "".join(f"<tr><td>r{i}</td><td>v{i}</td></tr>" for i in range(30))
    prose = " ".join(f"Sentence number {i} about the service." for i in range(40))
    html = f"<main><h1>T</h1><p>{prose}</p><table><tr><th>K</th><th>V</th></tr>{rows}</table></main>"
    chunks = [c.text for c in chunk_document(parse_document(html.encode(), "t.html"), size=900, overlap=150)]
    with_table = [c for c in chunks if "| K | V |" in c]
    assert len(with_table) == 1 and with_table[0].count("| r") == 30


def test_reading_view_shows_line_breaks_in_cells_but_no_other_html():
    from knowledge_search.viewer import page

    out = page("t.md", "| a | b |\n|---|---|\n| one<br>two | <b>x</b> |\n")
    assert "<th>a</th>" in out and "one<br>two" in out and "&lt;b&gt;x&lt;/b&gt;" in out
