"""PDF and Word (.docx) → Markdown, for the documents a site links to or embeds. Word: paragraphs with
Heading 1–6 / Title styles become headings, list paragraphs items, tables rows of cells, hyperlinks keep their
target. PDF (pypdf): the text layer, one "## Page N" section per page; scanned PDFs have none (no OCR)."""

import io
import re
import zipfile
from xml.etree import ElementTree

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
                out.extend((t.text or "") if t.tag == f"{W}t" else "\t" if t.tag == f"{W}tab" else ""
                           for t in node)
            elif node.tag == f"{W}hyperlink":
                label = text_of(node)
                target = rels.get(node.get(f"{R}id")) or ""
                out.append(f"[{label}]({target.replace(' ', '%20')})" if target and label.strip() else label)
            elif node.tag in (f"{W}ins", f"{W}smartTag", f"{W}sdtContent"):
                out.append(text_of(node))
        return "".join(out)

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
            for tr in el.iter(f"{W}tr"):
                cells = [" ".join(text_of(p).strip() for p in tc.iter(f"{W}p")).strip() for tc in tr.iter(f"{W}tc")]
                if any(cells):
                    lines.append(" | ".join(cells))
        lines.append("")
    return title, re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def pdf_to_markdown(data: bytes) -> tuple[str, str]:
    """(title from the PDF's metadata, markdown); markdown is empty when the PDF has no text layer."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        reader.decrypt("")  # "no open password" PDFs; others raise below
    title = str((reader.metadata or {}).get("/Title") or "").strip()
    title = re.sub(r"(?i)^microsoft (word|powerpoint) - |\.(docx?|pptx?|pdf)$", "", title).strip()
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = re.sub(r"[ \t]+\n", "\n", (page.extract_text() or "").strip())
        if text:
            pages.append(f"## Page {i}\n\n{text}")
    return title, "\n\n".join(pages)


def document_to_markdown(data: bytes, kind: str) -> tuple[str, str]:
    """(title, markdown) of a pdf or docx file."""
    return pdf_to_markdown(data) if kind == "pdf" else docx_to_markdown(data)
