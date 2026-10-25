"""HTML → Markdown for documentation and wiki pages (MkDocs, Sphinx, Confluence, Google Sites, generated
wikis). Standard library only. When the page has <main>/<article>/role="main" elements only those are used
(unless they miss a good part of the page's own text), so menus and footers are not kept; <script>, <style>, <nav>,
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
_SECTION = {"thead": 0, "tbody": 1, "tfoot": 2}


def table_markdown(rows: list[list[tuple[str | None, int, int]]], caption: str = "") -> str | None:
    """A GFM table from rows of (cell text, rowspan, colspan), shared by the HTML, Word and PowerPoint converters.
    A merged cell's text is repeated into every row and column it covers, so each row reads on its own; a
    text of None repeats the cell above (Word's vertical merge). The first row is the header; short rows are
    padded. Line breaks in a cell become <br>. None when it is not a data table (one row or one column)."""
    grid: dict[tuple[int, int], str] = {}
    for r, row in enumerate(rows):
        c = 0
        for text, rs, cs in row:
            while (r, c) in grid:
                c += 1
            if text is None:
                text = grid.get((r - 1, c), "")
            rs = min(rs or len(rows) - r, len(rows) - r)  # rowspan=0: to the end of the table
            cs = max(1, min(cs, 1000))
            for dr in range(max(rs, 1)):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = text
            c += cs
    width = max((c for _, c in grid), default=-1) + 1
    if len(rows) < 2 or width < 2:
        return None

    def cell(t: str) -> str:
        return "<br>".join(x.strip() for x in t.split("\n") if x.strip()).replace("|", "\\|")

    lines = ["| " + " | ".join(cell(grid.get((r, c), "")) for c in range(width)) + " |" for r in range(len(rows))]
    lines.insert(1, "|" + "---|" * width)
    caption = " ".join(caption.split())
    return (f"**{caption}**\n\n" if caption else "") + "\n".join(lines)


class _Table:
    """A <table> being read: its rows of cells, each cell's Markdown collected separately."""

    def __init__(self, parent_out: list[str]):
        self.parent_out = parent_out  # where the finished table goes
        self.junk: list[str] = []  # text between cells (whitespace) is dropped
        self.rows: list[tuple[int, list[dict]]] = []  # (section order, cells)
        self.section = 1
        self.cell: dict | None = None
        self.caption: list[str] = []
        self.nested = False

    def markdown(self) -> str:
        rows = [cells for _, cells in sorted(self.rows, key=lambda r: r[0]) if cells]  # thead, tbody, tfoot
        texts = [[re.sub(r"[ \t]+", " ", "".join(x["out"])).strip() for x in row] for row in rows]
        caption = "".join(self.caption).strip()
        # GFM cells cannot hold headings, lists, code blocks or tables: such a table is layout, kept as text
        layout = self.nested or any(re.search(r"(?m)^\s*(#{1,6} |- |```|\|)", t) for row in texts for t in row)
        table = None if layout else table_markdown(
            [[(t, x["rs"], x["cs"]) for t, x in zip(trow, row)] for trow, row in zip(texts, rows)], caption)
        if table is None:  # one row, one column, or layout: the cells as paragraphs
            table = "\n\n".join(([f"**{' '.join(caption.split())}**"] if caption else []) + [t for row in texts for t in row if t])
        return f"\n\n{table}\n\n"


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
        self.tables: list[_Table] = []

    def _active(self) -> bool:
        return not self.skip and (not self.only_main or bool(self.main))

    def _close_cell(self, t: _Table) -> None:
        if t.cell:
            t.rows[-1][1].append(t.cell)
            t.cell = None
        self.out = t.junk

    def _table_tag(self, tag: str, a: dict, start: bool) -> bool:
        """Table structure; True when the tag was handled. End tags of cells and rows may be left out in HTML."""
        if start and tag == "table":
            if self.tables:
                self.tables[-1].nested = True
            self.tables.append(_Table(self.out))
            self.out = self.tables[-1].junk
            return True
        if not self.tables:
            return False
        t = self.tables[-1]
        if tag == "table":
            self.end_table()
        elif tag in _SECTION:
            self._close_cell(t)
            t.section = _SECTION[tag] if start else 1
        elif tag == "tr":
            self._close_cell(t)
            if start:
                t.rows.append((t.section, []))
        elif tag in ("td", "th"):
            self._close_cell(t)
            if start:
                if not t.rows:
                    t.rows.append((t.section, []))
                span = lambda k: int(a[k]) if a.get(k, "").strip().isdigit() else 1  # noqa: E731
                t.cell = {"rs": span("rowspan"), "cs": span("colspan"), "out": []}
                self.out = t.cell["out"]
        elif tag == "caption":
            self._close_cell(t)
            if start:
                self.out = t.caption
        else:
            return False
        return True

    def end_table(self) -> None:
        t = self.tables.pop()
        self._close_cell(t)
        self.out = t.parent_out
        self.out.append(t.markdown())

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "title":
            self._in_title = True
        if tag in VOID:
            pass
        elif self.skip:
            self.skip[1] += tag == self.skip[0]
            return
        elif (tag in SKIP or a.get("role") in SKIP_ROLES or SKIP_CLASSES & set(a.get("class", "").split())
              or (tag == "footer" and not self.only_main)):  # whole page: the site footer is not content
            self.skip = [tag, 1]
            return
        elif self.main:
            self.main[1] += tag == self.main[0]
        elif self.only_main and (tag in ("main", "article") or a.get("role") == "main"):
            self.main = [tag, 1]
        if not self._active() or self._table_tag(tag, a, True):
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
        elif tag == "a":
            href = a.get("href", "")
            self.href.append(href if href and not href.startswith(("#", "javascript:", "mailto:")) else None)
            if self.href[-1]:
                self.out.append("[")
        elif tag == "img":  # a picture: ![alt](src), so a crawl can save it; icons (under 48 px) only their alt
            alt = " ".join(re.sub(r"[\[\]]+", " ", a.get("alt", "")).split())
            src = a.get("src", "").strip()
            small = any(a.get(k, "").isdigit() and int(a[k]) < 48 for k in ("width", "height"))
            if src and not small and not src.startswith("data:"):
                self.out.append(f"\n\n![{alt}]({src.replace(' ', '%20').replace(')', '%29')})\n\n")
            elif alt:
                self.out.append(f" {alt} ")
        if tag in BLOCK:
            self.out.append("\n\n")

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
        if self._table_tag(tag, {}, False):
            return
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
    while p.tables:  # a page cut off inside a table
        p.end_table()
    return p


def _own_text(md: str) -> int:
    """Characters of text that are not links or markup (menus are mostly links)."""
    return len(re.sub(r"\[[^\]]*\]\([^)]*\)|[#*`|\s-]", "", md))


def html_to_markdown(html: str) -> tuple[str, str]:
    """(title, markdown)."""
    p = _convert(html, only_main=bool(_MAIN.search(html)))
    if p.only_main:  # text outside <main> that is not menus or footer (site builders put sections there):
        whole = _convert(html, only_main=False)  # keep the whole page rather than miss content
        if _own_text("".join(whole.out)) > 1.25 * _own_text("".join(p.out)) + 100:
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
