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


def page(filename: str, text: str, plain: bool = False, download: str = "", links: dict | None = None) -> str:
    body = re.sub(r"(?s)^---\r?\n.*?\r?\n---\r?\n", "", text, count=1)  # front matter
    content = f"<pre>{html.escape(body)}</pre>" if plain else _relink(_MD.render(body), links or {})
    link = f'<a href="{html.escape(download)}">Download original</a>' if download else ""
    return (f"<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            f"content=\"width=device-width, initial-scale=1\"><title>{html.escape(filename)}</title>"
            f"<style>{STYLE}</style></head><body><header><strong>{html.escape(filename)}</strong>"
            f"<span class=\"meta\">readable view</span>{link}</header><main>{content}</main></body></html>")
