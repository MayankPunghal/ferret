"""HTML, Word (.docx) and PowerPoint (.pptx) → Markdown, so every format goes through the same Markdown parser (headings →
sections and anchors, links → the document graph). Standard library only.

HTML: doc-site and wiki exports (MkDocs, Sphinx, Confluence, generated wikis). When the page has a
<main>/<article>/role="main" element only that is used, so menus and footers are not indexed; <script>,
<style>, <nav>, navigation roles and sidebars are always dropped. <a href> becomes a Markdown link
(→ graph edges).
DOCX: paragraphs with Heading 1–6 / Title styles become headings, list paragraphs become items, tables
become Markdown tables (merged cells repeated); hyperlinks keep their target.
PPTX: a section per slide (its title as the heading), the slide's text and tables, then its speaker notes.
"""

import re
import zipfile
from xml.etree import ElementTree

from site2md.html import (
    html_to_markdown,  # noqa: F401  (re-exported: HTML lives in the crawler package)
    table_markdown,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_XML = 200_000_000  # uncompressed size limit for document.xml (zip bomb guard)


def _xml(z: zipfile.ZipFile, name: str):
    info = z.getinfo(name)
    if info.file_size > MAX_XML:
        raise ValueError(f"{name} is too large")
    return ElementTree.fromstring(z.read(name))


def docx_to_markdown(data: bytes) -> tuple[str, str]:
    """(title, markdown). Raises zipfile.BadZipFile / KeyError / ValueError for invalid files."""
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        doc = _xml(z, "word/document.xml")
        rels = {}
        if "word/_rels/document.xml.rels" in z.namelist():
            for r in _xml(z, "word/_rels/document.xml.rels"):
                rels[r.get("Id")] = r.get("Target")
        title = ""
        if "docProps/core.xml" in z.namelist():
            t = _xml(z, "docProps/core.xml").find("{http://purl.org/dc/elements/1.1/}title")
            title = (t.text or "").strip() if t is not None else ""

    def text_of(el) -> str:
        out = []
        for node in el:
            if node.tag == f"{W}r":
                out.extend((t.text or "") if t.tag == f"{W}t" else "\t" if t.tag == f"{W}tab"
                           else " " if t.tag in (f"{W}br", f"{W}cr") else "" for t in node)
            elif node.tag == f"{W}hyperlink":
                label = text_of(node)
                target = rels.get(node.get(f"{R}id")) or ""
                out.append(f"[{label}]({target.replace(' ', '%20')})" if target and label.strip() else label)
            elif node.tag in (f"{W}ins", f"{W}smartTag", f"{W}sdtContent"):
                out.append(text_of(node))
        return "".join(out)

    def table(el) -> str:
        return _word_table(el, text_of)

    lines: list[str] = []
    body = doc.find(f"{W}body")
    for el in body if body is not None else []:
        if el.tag == f"{W}p":
            style = el.find(f"{W}pPr/{W}pStyle")
            style = (style.get(f"{W}val") or "") if style is not None else ""
            txt = text_of(el).strip()
            if not txt:
                continue
            m = re.fullmatch(r"(?i)heading\s*([1-6])", style)
            if style.lower() == "title":
                title = title or txt
                lines.append(f"# {txt}")
            elif m:
                lines.append("#" * int(m.group(1)) + " " + txt)
            elif el.find(f"{W}pPr/{W}numPr") is not None or style.lower().startswith("list"):
                lines.append(f"- {txt}")
            else:
                lines.append(txt)
        elif el.tag == f"{W}tbl":
            lines.append(table(el))
        lines.append("")
    return title, re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _word_table(tbl, text_of) -> str:
    """A Word table as a Markdown table: gridSpan is a merged cell across columns, vMerge one down rows. A
    table holding another table is layout: its cells become paragraphs (the inner table stays a table)."""
    def val(pr, name):
        x = pr.find(f"{W}{name}") if pr is not None else None
        return None if x is None else x.get(f"{W}val", "")

    rows, cells, layout = [], [], False
    for tr in tbl.findall(f"{W}tr"):
        row = []
        for tc in tr.findall(f"{W}tc"):
            blocks = [_word_table(x, text_of) if x.tag == f"{W}tbl" else text_of(x).strip()
                      for x in tc if x.tag in (f"{W}p", f"{W}tbl")]
            layout |= tc.find(f"{W}tbl") is not None
            text = "\n".join(b for b in blocks if b)
            pr = tc.find(f"{W}tcPr")
            span = val(pr, "gridSpan")
            merged = val(pr, "vMerge") in ("", "continue")  # continues the cell above
            row.append((None if merged else text, 1, int(span) if span and span.isdigit() else 1))
            cells += [] if merged else [b for b in blocks if b]
        if any(t is None or t for t, _, _ in row):
            rows.append(row)
    md = None if layout else table_markdown(rows)
    return md if md is not None else "\n\n".join(cells)


A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
DGM = "{http://schemas.openxmlformats.org/drawingml/2006/diagram}"
DSP = "{http://schemas.microsoft.com/office/drawing/2008/diagram}"


def rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """{relationship id: (type, part name)} of a package part."""
    import posixpath

    name = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if name not in z.namelist():
        return {}
    return {r.get("Id"): (r.get("Type", "").rsplit("/", 1)[-1],
                          posixpath.normpath(posixpath.join(posixpath.dirname(part), r.get("Target", ""))).lstrip("/"))
            for r in _xml(z, name) if r.tag == f"{PKG}Relationship" and r.get("TargetMode") != "External"}


def paragraphs(el) -> list[str]:
    """Text of each paragraph; a line break inside one (Shift+Enter, as in "COO" over a name in an org chart box)
    starts a new item, so the two lines never run together."""
    out = []
    for p in el.iter(f"{A}p"):
        lines = [""]
        for node in p.iter():
            if node.tag == f"{A}t":
                lines[-1] += node.text or ""
            elif node.tag == f"{A}br":
                lines.append("")
        out += [t for x in lines if (t := x.strip())]
    return out


def slide_parts(z: zipfile.ZipFile) -> list[str]:
    pres, pres_rels = _xml(z, "ppt/presentation.xml"), rels(z, "ppt/presentation.xml")
    return [pres_rels[s.get(f"{R}id")][1] for s in pres.iter(f"{P}sldId") if s.get(f"{R}id") in pres_rels]


def smartart_parts(z: zipfile.ZipFile, slide_part: str, frame) -> tuple[str | None, str | None]:
    """(data part, drawing part) of a SmartArt graphic frame; the drawing is the shapes PowerPoint last drew."""
    ids = frame.find(f".//{DGM}relIds")
    if ids is None:
        return None, None
    r = rels(z, slide_part)
    data = r.get(ids.get(f"{R}dm"), (None, None))[1]
    if not data or data not in z.namelist():
        return None, None
    ext = _xml(z, data).find(f".//{DSP}dataModelExt")
    drawing = r.get(ext.get("relId"), (None, None))[1] if ext is not None else None
    return data, drawing if drawing in z.namelist() else None


def _under(parent: str) -> str:
    return f" (under {parent[:80]})" if parent else ""


def smartart_lines(dm) -> list[str]:
    """SmartArt text as a nested list in its own order. In hierarchy layouts (organisation charts) every item
    below the top says what it is under, so a passage answers "who reports to X" on its own."""
    doc = dm.find(f"{DGM}ptLst/{DGM}pt[@type='doc']")
    if doc is None:
        return []
    pr = doc.find(f"{DGM}prSet")
    layout = ((pr.get("loCatId", "") + " " + pr.get("loTypeId", "")) if pr is not None else "").lower()
    hierarchy = "hierarchy" in layout or "orgchart" in layout
    text = {pt.get("modelId"): ", ".join(paragraphs(pt.find(f"{DGM}t"))) if pt.find(f"{DGM}t") is not None else ""
            for pt in dm.iter(f"{DGM}pt") if pt.get("type", "node") in ("node", "asst")}
    kids: dict[str, list] = {}
    for c in dm.iter(f"{DGM}cxn"):
        if c.get("type", "parOf") == "parOf":
            kids.setdefault(c.get("srcId"), []).append((int(c.get("srcOrd") or 0), c.get("destId")))
    lines, seen = [], set()

    def walk(node, depth, parent):
        for _, k in sorted(kids.get(node, [])):
            if k in seen or k not in text:
                continue
            seen.add(k)
            if text[k]:
                lines.append("  " * depth + "- " + text[k] + (_under(parent) if hierarchy else ""))
            walk(k, depth + bool(text[k]), text[k] or parent)

    walk(doc.get("modelId"), 0, "")
    return lines


def _connected_tree(slide) -> tuple[list[str], set]:
    """Shapes joined by connector lines (a hand-drawn organisation chart) as a nested list, the upper shape of
    each connection as the parent; and the ids of the shapes it used."""
    shapes = {}
    for sp in slide.iter(f"{P}sp"):
        nv, off = sp.find(f"{P}nvSpPr/{P}cNvPr"), sp.find(f"{P}spPr/{A}xfrm/{A}off")
        if nv is not None and off is not None and (txt := ", ".join(paragraphs(sp))):
            shapes[nv.get("id")] = (int(off.get("y", 0)), int(off.get("x", 0)), txt)
    parent = {}
    for c in slide.iter(f"{P}cxnSp"):
        st, en = c.find(f".//{A}stCxn"), c.find(f".//{A}endCxn")
        if st is None or en is None or st.get("id") not in shapes or en.get("id") not in shapes:
            continue
        a, b = sorted((st.get("id"), en.get("id")), key=lambda i: shapes[i][:2])
        if a != b:
            parent.setdefault(b, a)
    if not parent:
        return [], set()
    kids: dict[str, list] = {}
    for k, v in parent.items():
        kids.setdefault(v, []).append(k)
    lines, seen = [], set()

    def walk(node, depth, up):
        seen.add(node)
        lines.append("  " * depth + "- " + shapes[node][2] + _under(up))
        for k in sorted(kids.get(node, []), key=lambda i: shapes[i][:2]):
            if k not in seen:
                walk(k, depth + 1, shapes[node][2])

    for root in sorted({v for v in parent.values() if v not in parent}, key=lambda i: shapes[i][:2]):
        walk(root, 0, "")
    return lines, seen


def pptx_to_markdown(data: bytes) -> tuple[str, str]:
    """(title, markdown): one section per slide ("## Slide 3: Title"), its text as list items, then the
    speaker notes. SmartArt and shapes joined by connectors become nested lists that keep their hierarchy.
    Raises zipfile.BadZipFile / KeyError / ValueError for invalid files."""
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        title = ""
        if "docProps/core.xml" in z.namelist():
            t = _xml(z, "docProps/core.xml").find("{http://purl.org/dc/elements/1.1/}title")
            title = (t.text or "").strip() if t is not None else ""
        lines: list[str] = []
        for n, part in enumerate(slide_parts(z), 1):
            slide, heading, body = _xml(z, part), "", []
            tree, in_tree = _connected_tree(slide)
            for sp in slide.iter(f"{P}sp"):
                ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                nv = sp.find(f"{P}nvSpPr/{P}cNvPr")
                text = paragraphs(sp)
                if ph is not None and ph.get("type") in ("title", "ctrTitle") and not heading:
                    heading = " ".join(text)
                elif nv is None or nv.get("id") not in in_tree:
                    body += [f"- {x}" for x in text]
            body += tree
            for frame in slide.iter(f"{P}graphicFrame"):
                dm = smartart_parts(z, part, frame)[0]
                body += smartart_lines(_xml(z, dm)) if dm else []
            for tbl in slide.iter(f"{A}tbl"):  # tables; a covered cell of a merge is marked hMerge / vMerge
                rows = [[("\n".join(paragraphs(tc)), int(tc.get("rowSpan", 1)), int(tc.get("gridSpan", 1)))
                         for tc in tr.findall(f"{A}tc") if not (tc.get("hMerge") or tc.get("vMerge"))]
                        for tr in tbl.findall(f"{A}tr")]
                md = table_markdown(rows)
                body += ["", md, ""] if md else [f"- {t}" for row in rows for t, _, _ in row if t]
            notes = [rel[1] for rel in rels(z, part).values() if rel[0] == "notesSlide"]
            note_text = [t for t in paragraphs(_xml(z, notes[0])) if not t.isdigit()] if notes else []  # no slide numbers
            lines.append(f"## Slide {n}" + (f": {heading}" if heading else ""))
            lines += body + ([f"Notes: {' '.join(note_text)}"] if note_text else []) + [""]
        if title.lower() in ("powerpoint presentation", "presentation", "slide 1"):  # PowerPoint's default
            title = ""
        title = title or next((ln.split(": ", 1)[1] for ln in lines if ln.startswith("## Slide 1: ")), "")
    return title, "\n".join(lines).strip()
