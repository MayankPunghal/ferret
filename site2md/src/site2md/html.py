"""HTML → Markdown for documentation and wiki pages (MkDocs, Sphinx, Confluence, Google Sites, generated
wikis). Standard library only. When the page has <main>/<article>/role="main" elements only those are used
(unless most of the text is outside them), so menus and footers are not kept; <script>, <style>, <nav>,
navigation roles and sidebars are always dropped. <a href> becomes a Markdown link."""

import re
from html.parser import HTMLParser

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
    """Markdown-ish text of an HTML page. With ``only_main``, only the <main>/<article>/role=main
    elements are converted; navigation, permalinks and similar chrome are always skipped."""

    def __init__(self, only_main: bool):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.only_main = only_main
        self.main: list = []  # [tag, depth] while inside the main element
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
        elif self.only_main and (tag in ("main", "article") or a.get("role") == "main"):
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
                self.main = []
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


def _convert(html: str, only_main: bool) -> _Html:
    p = _Html(only_main)
    p.feed(html)
    p.close()
    return p


def _own_text(md: str) -> int:
    """Characters of text that are not links or markup (menus are mostly links)."""
    return len(re.sub(r"\[[^\]]*\]\([^)]*\)|[#*`|\s-]", "", md))


def html_to_markdown(html: str) -> tuple[str, str]:
    """(title, markdown)."""
    p = _convert(html, only_main=bool(_MAIN.search(html)))
    if p.only_main:  # a "main" element that holds only part of the text (site builders): use the whole page
        whole = _convert(html, only_main=False)
        if _own_text("".join(whole.out)) > 2 * _own_text("".join(p.out)) + 200:
            p = whole
    md = "".join(p.out)
    md = re.sub(r"[\ue000-\uf8ff¶]", "", md)  # icon-font glyphs and pilcrow permalinks
    md = re.sub(r"\[\s*\]\([^)]*\)", "", md)  # links without text (icons)
    # a heading or list item whose text sits in a nested block (<h3><div><p>…, <li><p>…: Google Sites,
    # Confluence) has its marker alone on a line: join it to the text; a marker with no text is dropped
    md = re.sub(r"(?m)^(#{1,6}|-)[ \t]*\n(?:[ \t]*\n)*(?=[^\s#-])", r"\1 ", md)
    md = re.sub(r"(?m)^(#{1,6}|-)[ \t]*$", "", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n[ \t]+(?=\S)", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    title = " ".join(p.title.split())
    return title, md
