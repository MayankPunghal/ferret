"""HTML and Word (.docx) → Markdown, so every format goes through the same Markdown parser (headings →
sections and anchors, links → the document graph). Standard library only.

HTML: doc-site and wiki exports (MkDocs, Sphinx, Confluence, generated wikis). When the page has a
<main>/<article>/role="main" element only that is used, so menus and footers are not indexed; <script>,
<style>, <nav>, navigation roles and sidebars are always dropped. <a href> becomes a Markdown link
(→ graph edges).
DOCX: paragraphs with Heading 1–6 / Title styles become headings, list paragraphs become items, tables
become rows of cells; hyperlinks keep their target.
"""

import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

SKIP = {"script", "style", "noscript", "template", "svg", "nav", "head", "iframe", "object", "button", "form",
        "select", "aside"}
SKIP_ROLES = {"navigation", "banner", "contentinfo", "search", "complementary"}
SKIP_CLASSES = {"headerlink", "sidebar", "toc", "breadcrumb", "breadcrumbs", "related", "navbar", "md-sidebar",
                "md-header", "md-footer", "sphinxsidebar", "skip-link"}
BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "ul", "ol", "table", "tr", "blockquote",
         "dl", "dt", "dd", "figure", "figcaption", "br", "hr"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_MAIN = re.compile(r"""<(main|article)\b|\brole\s*=\s*["']?main\b""", re.IGNORECASE)


class _Html(HTMLParser):
    """Markdown-ish text of an HTML page. With ``only_main``, only the first <main>/<article>/role=main
    element is converted; navigation, permalinks and similar chrome are always skipped."""

    def __init__(self, only_main: bool):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.only_main = only_main
        self.main: list = []  # [tag, depth] while inside the main element
        self.main_done = False
        self.skip: list = []  # [tag, depth] while inside a skipped element
        self.pre = 0
        self.title = ""
        self._in_title = False
        self.href: list[str | None] = []
        self.cells = 0

    def _active(self) -> bool:
        return not self.skip and (not self.only_main or bool(self.main))

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "title":
            self._in_title = True
        if tag in VOID:
            pass
        elif self.skip:
            self.skip[1] += tag == self.skip[0]
            return
        elif tag in SKIP or a.get("role") in SKIP_ROLES or SKIP_CLASSES & set(a.get("class", "").split()):
            self.skip = [tag, 1]
            return
        elif self.main:
            self.main[1] += tag == self.main[0]
        elif self.only_main and not self.main_done and (tag in ("main", "article") or a.get("role") == "main"):
            self.main = [tag, 1]
        if not self._active():
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "pre":
            self.pre += 1
            self.out.append("\n\n```\n")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag in ("td", "th"):
            self.out.append(" | " if self.cells else "")
            self.cells += 1
        elif tag == "a":
            href = a.get("href", "")
            self.href.append(href if href and not href.startswith(("#", "javascript:", "mailto:")) else None)
            if self.href[-1]:
                self.out.append("[")
        elif tag == "img" and a.get("alt"):
            self.out.append(a["alt"])
        if tag in BLOCK:
            self.out.append("\n\n" if tag != "tr" else "\n")
            self.cells = 0 if tag == "tr" else self.cells

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self.skip:
            if tag == self.skip[0]:
                self.skip[1] -= 1
                if not self.skip[1]:
                    self.skip = []
            return
        if not self._active():
            return
        if self.main and tag == self.main[0]:
            self.main[1] -= 1
            if not self.main[1]:
                self.main, self.main_done = [], True
        if re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n")
        elif tag == "pre":
            self.pre = max(self.pre - 1, 0)
            self.out.append("\n```\n\n")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag == "a" and self.href:
            href = self.href.pop()
            if href:
                self.out.append(f"]({href.replace(' ', '%20')})")
        elif tag in BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._active():
            self.out.append(data if self.pre else re.sub(r"\s+", " ", data))


def html_to_markdown(html: str) -> tuple[str, str]:
    """(title, markdown)."""
    p = _Html(only_main=bool(_MAIN.search(html)))
    p.feed(html)
    p.close()
    md = "".join(p.out)
    md = re.sub(r"[\ue000-\uf8ff¶]", "", md)  # icon-font glyphs and pilcrow permalinks
    md = re.sub(r"\[\s*\]\([^)]*\)", "", md)  # links without text (icons)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n[ \t]+(?=\S)", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    title = " ".join(p.title.split())
    return title, md


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
