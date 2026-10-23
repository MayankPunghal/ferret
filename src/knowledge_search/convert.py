"""HTML, Word (.docx) and PowerPoint (.pptx) → Markdown, so every format goes through the same Markdown parser (headings →
sections and anchors, links → the document graph). Standard library only.

HTML: doc-site and wiki exports (MkDocs, Sphinx, Confluence, generated wikis). When the page has a
<main>/<article>/role="main" element only that is used, so menus and footers are not indexed; <script>,
<style>, <nav>, navigation roles and sidebars are always dropped. <a href> becomes a Markdown link
(→ graph edges).
DOCX: paragraphs with Heading 1–6 / Title styles become headings, list paragraphs become items, tables
become rows of cells; hyperlinks keep their target.
PPTX: a section per slide (its title as the heading), the slide's text and tables, then its speaker notes.
"""

import re
import zipfile
from xml.etree import ElementTree

from site2md.html import (
    html_to_markdown,  # noqa: F401  (re-exported: HTML lives in the crawler package)
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


A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def pptx_to_markdown(data: bytes) -> tuple[str, str]:
    """(title, markdown): one section per slide ("## Slide 3: Title"), its text as list items, then the
    speaker notes. Raises zipfile.BadZipFile / KeyError / ValueError for invalid files."""
    import io
    import posixpath

    def rels(z, part):  # {relationship id: (type, part name)}
        name = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
        if name not in z.namelist():
            return {}
        return {r.get("Id"): (r.get("Type", "").rsplit("/", 1)[-1],
                              posixpath.normpath(posixpath.join(posixpath.dirname(part), r.get("Target", ""))).lstrip("/"))
                for r in _xml(z, name) if r.tag == f"{PKG}Relationship"}

    def paragraphs(el) -> list[str]:
        return [t for p in el.iter(f"{A}p") if (t := "".join(x.text or "" for x in p.iter(f"{A}t")).strip())]

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        title = ""
        if "docProps/core.xml" in z.namelist():
            t = _xml(z, "docProps/core.xml").find("{http://purl.org/dc/elements/1.1/}title")
            title = (t.text or "").strip() if t is not None else ""
        pres, pres_rels = _xml(z, "ppt/presentation.xml"), rels(z, "ppt/presentation.xml")
        slides = [pres_rels[s.get(f"{R}id")][1] for s in pres.iter(f"{P}sldId") if s.get(f"{R}id") in pres_rels]
        lines: list[str] = []
        for n, part in enumerate(slides, 1):
            slide, heading, body = _xml(z, part), "", []
            for sp in slide.iter(f"{P}sp"):
                ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                text = paragraphs(sp)
                if ph is not None and ph.get("type") in ("title", "ctrTitle") and not heading:
                    heading = " ".join(text)
                else:
                    body += text
            body += [" | ".join(" ".join(paragraphs(tc)) for tc in tr.iter(f"{A}tc"))
                     for tr in slide.iter(f"{A}tr")]  # tables, a row per line
            notes = [rel[1] for rel in rels(z, part).values() if rel[0] == "notesSlide"]
            note_text = [t for t in paragraphs(_xml(z, notes[0])) if not t.isdigit()] if notes else []  # no slide numbers
            lines.append(f"## Slide {n}" + (f": {heading}" if heading else ""))
            lines += [f"- {t}" for t in body] + ([f"Notes: {' '.join(note_text)}"] if note_text else []) + [""]
        if title.lower() in ("powerpoint presentation", "presentation", "slide 1"):  # PowerPoint's default
            title = ""
        title = title or next((ln.split(": ", 1)[1] for ln in lines if ln.startswith("## Slide 1: ")), "")
    return title, "\n".join(lines).strip()
