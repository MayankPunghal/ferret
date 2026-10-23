"""Read-only HTML page for a document the browser cannot show by itself (Markdown, text, Word, PowerPoint):
the text rendered from Markdown with raw HTML escaped, so nothing in a document can run or load."""

import html
import re

from markdown_it import MarkdownIt

from .parsing import normalize_target, slugify

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

STYLE = """
:root { color-scheme: light dark; --fg: #1d2330; --muted: #5b6475; --line: #d9dde5; --bg: #fff; --code: #f3f4f7; --a: #1f5fbf; }
@media (prefers-color-scheme: dark) { :root { --fg: #e6e9ef; --muted: #9aa3b2; --line: #343a46; --bg: #15181e; --code: #1f232b; --a: #7fb0ff; } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
header { position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--line); padding: 10px 16px;
         display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
header strong { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
header a, main a { color: var(--a); }
main { max-width: 820px; margin: 0 auto; padding: 16px 16px 64px; overflow-wrap: anywhere; }
pre, code { background: var(--code); border-radius: 4px; font: 14px/1.5 ui-monospace, Consolas, monospace; }
pre { padding: 10px 12px; overflow-x: auto; white-space: pre-wrap; } code { padding: 1px 4px; } pre code { padding: 0; }
table { border-collapse: collapse; display: block; overflow-x: auto; } th, td { border: 1px solid var(--line); padding: 4px 8px; }
blockquote { margin: 0; padding-left: 12px; border-left: 3px solid var(--line); color: var(--muted); }
hr { border: 0; border-top: 1px solid var(--line); } img { max-width: 100%; }
.meta { color: var(--muted); font-size: 13px; }
.slides { max-width: 1120px; margin: 0 auto; padding: 16px 16px 64px; display: grid; gap: 28px; }
.slides h2 { font-size: 13px; color: var(--muted); margin: 0 0 6px; font-weight: 600; }
.slide { --sans: Calibri, Carlito, "Segoe UI", Arial, sans-serif; position: relative; container-type: inline-size;
         overflow: hidden; box-shadow: 0 1px 5px rgba(0,0,0,.3); color: #000; font-family: var(--sans); line-height: 1.2; }
.slide .s { position: absolute; box-sizing: border-box; }
.slide .fill { position: absolute; inset: 0; }
.slide svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.slide .t { position: absolute; inset: 0; display: flex; flex-direction: column; box-sizing: border-box; overflow-wrap: break-word; }
.slide p { margin: 0; white-space: pre-wrap; }
.slide .bu { display: inline-block; text-indent: 0; min-width: .8em; }
.slide .missing, .slide .note { display: flex; align-items: center; justify-content: center; border: 1px dashed #999;
         background: repeating-linear-gradient(45deg, #f4f4f4 0 8px, #eaeaea 8px 16px); color: #555; font: 1.3cqw/1.4 system-ui; }
.slide .note ul { text-align: left; list-style: none; padding: 1cqw; margin: 0; }
.slide table.tbl { display: table; width: 100%; height: 100%; border-collapse: collapse; table-layout: fixed; }
.slide .tbl td { border: 1px solid #fff; padding: .4cqw .8cqw; vertical-align: top; }
.notes { font-size: 14px; color: var(--muted); margin: 6px 2px 0; }
.hidden-slide { opacity: .55; }
"""


def _relink(content: str, links: dict[str, str]) -> str:
    """Links to other documents open their view (`links`: normalised target -> document id); other relative
    links lead nowhere here, so they become plain text. Web links and in-page anchors are kept."""
    def one(m):
        href = html.unescape(m.group(2))
        if re.match(r"(?i)[a-z][a-z0-9+.-]*:|#", href):
            return m.group(0)
        path, _, anchor = href.partition("#")
        doc = links.get(normalize_target(path))
        if not doc:
            return "<a>"
        return f'<a href="/api/documents/{doc}/view' + (f"#{html.escape(slugify(anchor))}" if anchor else "") + '">'
    return re.sub(r'<a( [^>]*?)?href="([^"]*)"[^>]*>', one, content)


def _heading_ids(content: str) -> str:
    """Headings get the anchors the parser gave their sections, so links to `#section` (and search results)
    open at the section."""
    used: dict[str, int] = {}

    def one(m):
        text = html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))
        custom = re.search(r"\s*\{#([\w-]+)\}\s*$", text)
        slug = custom.group(1).lower() if custom else slugify(text)
        n = used.get(slug, 0)
        used[slug] = n + 1
        return f'<h{m.group(1)} id="{html.escape(slug if n == 0 else f"{slug}-{n}")}">{m.group(2)}</h{m.group(1)}>'
    return re.sub(r"<h([1-6])>(.*?)</h\1>", one, content)


def _page(filename: str, kind: str, content: str, links: str, main: str = "main") -> str:
    return (f"<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            f"content=\"width=device-width, initial-scale=1\"><title>{html.escape(filename)}</title>"
            f"<style>{STYLE}</style></head><body><header><strong>{html.escape(filename)}</strong>"
            f"<span class=\"meta\">{kind}</span>{links}</header><{main}>{content}</{main.split()[0]}></body></html>")


def _links(*pairs) -> str:
    return " ".join(f'<a href="{html.escape(href)}">{label}</a>' for label, href in pairs if href)


def page(filename: str, text: str, plain: bool = False, download: str = "", links: dict | None = None,
         other_view: tuple[str, str] | None = None) -> str:
    body = re.sub(r"(?s)^---\r?\n.*?\r?\n---\r?\n", "", text, count=1)  # front matter
    content = f"<pre>{html.escape(body)}</pre>" if plain else _heading_ids(_relink(_MD.render(body), links or {}))
    return _page(filename, "readable view", content, _links(other_view or ("", ""), ("Download original", download)))


def slides_page(filename: str, slides: list[dict], download: str = "", text_view: str = "") -> str:
    """Slides drawn by slides.py (escaped text and generated styles only), each with its speaker notes."""
    content = "".join(
        f'<section id="{html.escape(s["anchor"])}" class="{"hidden-slide" if s["hidden"] else ""}">'
        f'<h2>Slide {s["n"]}{" (hidden)" if s["hidden"] else ""}</h2>{s["html"]}'
        + (f'<p class="notes">Notes: {html.escape(s["notes"])}</p>' if s["notes"] else "") + "</section>"
        for s in slides)
    return _page(filename, f"{len(slides)} slides", content,
                 _links(("Text view", text_view), ("Download original", download)), main='main class="slides"')


def pdf_page(filename: str, source: str, download: str) -> str:
    """The PDF viewer (static/pdfview.js with the vendored PDF.js); the browser's own viewer stays a link away."""
    name = html.escape(filename)
    links = _links(("Browser's PDF viewer", source), ("Download original", download))
    zoom = ('<span class="zoom" role="group" aria-label="Zoom"><button id="zoom-out" title="Zoom out">&minus;</button>'
            '<button id="zoom-fit" title="Fit to width">Fit</button><button id="zoom-in" title="Zoom in">+</button></span>')
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><title>{name}</title><link rel="stylesheet" href="/pdfview.css"></head><body>'
            f'<header><strong>{name}</strong><span id="pages" class="meta"></span>{zoom}{links}</header>'
            f'<main id="doc" data-src="{html.escape(source)}"><p id="status">Loading&hellip; '
            "<noscript>This viewer needs JavaScript: use the links above.</noscript></p></main>"
            '<script type="module" src="/pdfview.js"></script></body></html>')
