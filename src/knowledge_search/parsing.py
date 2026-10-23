"""Document validation, text extraction and structure-aware chunking.

Supported: Markdown (.md), plain text (.txt), HTML (.html/.htm), Word (.docx) and text-layer PDF (.pdf, via
pypdf). HTML and Word are converted to Markdown first (convert.py), so headings, anchors and links work the
same way for all of them. Scanned PDFs without a text layer are rejected with ``no_extractable_text``
rather than silently indexed as empty (OCR is out of scope).
"""

import io
import re
from dataclasses import dataclass, field

import yaml
from markdown_it import MarkdownIt

PARSER_VERSION = "parse-v4"  # v2: CRLF/CR line endings normalised; v3: HTML/DOCX, directory-style links; v4: {#anchor} headings, loose front matter
CHUNKER_VERSION = "chunk-v2"  # size/overlap are runtime settings, recorded in the index signature

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MEDIA_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain",
               ".html": "text/html", ".htm": "text/html", ".docx": DOCX, ".pdf": "application/pdf"}


class ParseError(Exception):
    """Raised for files we refuse or fail to parse. ``code`` is a stable reason code."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class Section:
    heading_path: str
    text: str
    page: int | None = None
    anchor: str | None = None


@dataclass
class ParsedDocument:
    title: str
    metadata: dict
    sections: list[Section] = field(default_factory=list)
    relations: list["Link"] = field(default_factory=list)  # typed links declared in front matter


@dataclass
class Link:
    kind: str
    target: str  # normalised (see normalize_target)
    anchor: str | None = None
    label: str = ""


@dataclass
class Chunk:
    ordinal: int
    heading_path: str
    text: str
    page: int | None
    anchor: str | None
    idents: list[str]
    links: list[Link] = field(default_factory=list)


def media_type_for(filename: str) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in MEDIA_TYPES:
        raise ParseError("unsupported_type", f"extension {ext or '(none)'} is not supported")
    return MEDIA_TYPES[ext]


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)[:80] or "section"


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise ParseError("binary_content", "file looks binary, not text")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ParseError("invalid_encoding", "text files must be UTF-8") from e
    # Windows (CRLF) and old Mac (CR) line endings: without this, front matter ("---\r\n") was not
    # recognised on Windows and got indexed as text, losing title/series/version/depends_on metadata.
    return text.replace("\r\n", "\n").replace("\r", "\n")


RELATION_KEYS = ("depends_on", "related", "calls", "owned_by", "part_of", "see_also")
_MD_LINK = re.compile(r"(?<!!)\[([^\]\n]{1,200})\]\(([^)\s]{1,500})(?:\s+\"[^\"]*\")?\)")
_WIKI_LINK = re.compile(r"\[\[([^\]|#\n]{1,200})(?:#([^\]|\n]{1,200}))?(?:\|([^\]\n]{1,200}))?\]\]")


def normalize_target(target: str) -> str:
    """'../api/Orders.md' -> 'orders.md'; 'Payments Service' -> 'payments service';
    doc-site style '../orders/' or 'orders/index.html' -> 'orders'; '?query' is dropped."""
    parts = [x for x in target.strip().split("?")[0].replace("\\", "/").replace("%20", " ").split("/")
             if x not in ("", ".", "..")]
    if len(parts) > 1 and re.fullmatch(r"(?i)index\.html?", parts[-1]):
        parts.pop()
    return parts[-1].lower() if parts else ""


def extract_links(text: str) -> list[Link]:
    """Internal Markdown links and [[wiki links]]; external URLs and images are ignored."""
    out = []
    for label, href in _MD_LINK.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.IGNORECASE):  # http:, mailto:, ...
            continue
        path, _, anchor = href.partition("#")
        if normalize_target(path):
            out.append(Link("links_to", normalize_target(path), slugify(anchor) if anchor else None, label.strip()))
    for page, anchor, label in _WIKI_LINK.findall(text):
        out.append(Link("links_to", normalize_target(page), slugify(anchor) if anchor else None,
                        (label or page).strip()))
    return out


def _relations(meta: dict) -> list[Link]:
    out = []
    for key in RELATION_KEYS:
        value = meta.get(key)
        items = value if isinstance(value, list) else str(value).split(",") if value else []
        for item in items:
            if str(item).strip():
                path, _, anchor = str(item).strip().partition("#")
                out.append(Link(key, normalize_target(path), slugify(anchor) if anchor else None, str(item).strip()))
    return out


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            try:
                meta = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                meta = None
            if not isinstance(meta, dict):
                # hand-written front matter is often not strict YAML ("title: Example: Deploying X");
                # read top-level `key: value` lines instead of rejecting the whole document
                meta = dict(m.groups() for m in re.finditer(r"(?m)^([A-Za-z_][\w-]*):[ \t]*(\S.*?)\s*$", text[4:end]))
                if not meta:
                    raise ParseError("invalid_frontmatter", "front matter is neither YAML nor key: value lines")
            body = text[end + 4 :].lstrip("\n")
            return {str(k): v for k, v in meta.items()}, body
    return {}, text


def parse_markdown(data: bytes, filename: str) -> ParsedDocument:
    return _parse_markdown_text(_decode_text(data), filename)


def _parse_markdown_text(text: str, filename: str, title: str = "") -> ParsedDocument:
    raw_meta, body = _split_frontmatter(text)
    relations = _relations(raw_meta)
    meta = {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in raw_meta.items()}
    tokens = MarkdownIt("commonmark").enable("table").parse(body)
    lines = body.splitlines()
    # Heading positions from the token stream (avoids treating '#' in code blocks as headings).
    heads: list[tuple[int, int, str]] = []
    for i, t in enumerate(tokens):
        if t.type == "heading_open" and t.map:
            heads.append((t.map[0], int(t.tag[1]), tokens[i + 1].content.strip()))
    title = str(meta.get("title") or title or next((h[2] for h in heads if h[1] == 1), "") or filename)
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    used_anchors: dict[str, int] = {}
    bounds = [(h[0], h) for h in heads] + [(len(lines), None)]
    if not heads or heads[0][0] > 0:
        pre = "\n".join(lines[: heads[0][0] if heads else len(lines)]).strip()
        if pre:
            sections.append(Section(heading_path="", text=pre, anchor=None))
    for idx, (start, h) in enumerate(bounds[:-1]):
        _, level, htext = h
        custom = re.search(r"\s*\{#([\w-]+)\}\s*$", htext)  # "## Title {#anchor}" (Hugo, Pandoc, MkDocs)
        if custom:
            htext = htext[: custom.start()]
        stack = [s for s in stack if s[0] < level] + [(level, htext)]
        end = bounds[idx + 1][0]
        text = "\n".join(lines[start + 1 : end]).strip()
        slug = custom.group(1).lower() if custom else slugify(htext)
        n = used_anchors.get(slug, 0)
        used_anchors[slug] = n + 1
        anchor = slug if n == 0 else f"{slug}-{n}"
        if text:
            sections.append(Section(heading_path=" > ".join(s[1] for s in stack), text=text, anchor=anchor))
    return ParsedDocument(title=title, metadata=meta, sections=sections, relations=relations)


def parse_text(data: bytes, filename: str) -> ParsedDocument:
    text = _decode_text(data)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    title = filename
    return ParsedDocument(title=title, metadata={}, sections=[Section("", "\n\n".join(paras))] if paras else [])


def parse_pdf(data: bytes, filename: str, max_pages: int) -> ParsedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not data.startswith(b"%PDF-"):
        raise ParseError("invalid_pdf", "missing %PDF- header")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ParseError("encrypted_pdf", "encrypted PDFs are not supported")
        if len(reader.pages) > max_pages:
            raise ParseError("too_many_pages", f"{len(reader.pages)} pages > limit {max_pages}")
        info = reader.metadata or {}
        sections = []
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            text = re.sub(r"[ \t]+\n", "\n", text)
            if text:
                sections.append(Section(heading_path=f"Page {i}", text=text, page=i, anchor=f"page={i}"))
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise ParseError("invalid_pdf", f"{type(e).__name__}: {str(e)[:200]}") from e
    if not sections:
        raise ParseError("no_extractable_text", "PDF has no text layer (OCR is not supported)")
    title = str(info.get("/Title") or "").strip() or filename
    return ParsedDocument(title=title, metadata={}, sections=sections)


def _decode_html(data: bytes) -> str:
    """UTF-8, else the charset the page declares, else Windows-1252 (old wiki exports)."""
    try:
        return _decode_text(data)
    except ParseError as e:
        if e.code != "invalid_encoding":
            raise
    m = re.search(rb"""charset=["']?([\w-]{2,40})""", data[:4096], re.IGNORECASE)
    for enc in ([m.group(1).decode()] if m else []) + ["cp1252"]:
        try:
            return data.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
        except (LookupError, UnicodeDecodeError):
            continue
    raise ParseError("invalid_encoding", "cannot decode the HTML page")


def parse_html(data: bytes, filename: str) -> ParsedDocument:
    from .convert import html_to_markdown

    title, md = html_to_markdown(_decode_html(data))
    return _parse_markdown_text(md, filename, title)


def parse_docx(data: bytes, filename: str) -> ParsedDocument:
    import zipfile

    from .convert import docx_to_markdown

    if not data.startswith(b"PK"):
        raise ParseError("invalid_docx", "not a .docx file (legacy .doc is not supported; save as .docx)")
    try:
        title, md = docx_to_markdown(data)
    except (zipfile.BadZipFile, KeyError, ValueError, SyntaxError) as e:  # ElementTree.ParseError ⊂ SyntaxError
        raise ParseError("invalid_docx", f"{type(e).__name__}: {str(e)[:200]}") from e
    return _parse_markdown_text(md, filename, title)


def parse_document(data: bytes, filename: str, max_pages: int = 2000, max_chars: int = 5_000_000) -> ParsedDocument:
    if not data:
        raise ParseError("empty_file", "file is empty")
    mt = media_type_for(filename)
    if mt == "application/pdf":
        doc = parse_pdf(data, filename, max_pages)
    elif mt == "text/markdown":
        doc = parse_markdown(data, filename)
    elif mt == "text/html":
        doc = parse_html(data, filename)
    elif mt == DOCX:
        doc = parse_docx(data, filename)
    else:
        doc = parse_text(data, filename)
    if not doc.sections:
        raise ParseError("no_extractable_text", "document contains no text")
    if sum(len(s.text) for s in doc.sections) > max_chars:
        raise ParseError("too_large", f"extracted text exceeds {max_chars} characters")
    return doc


# Identifier-like tokens: things with digits, underscores, dots, slashes or inner dashes
# (error codes, API paths, version strings, filenames, config keys). Matched exactly.
_IDENT_RE = re.compile(r"(?<![\w/])(/?[A-Za-z0-9][\w.\-/{}:]*[\w}])")


_CAMEL = re.compile(r"[a-z]+(?:[A-Z][a-z0-9]*)+")  # lowerCamelCase config keys / fields: concurrencyPolicy


def extract_idents(text: str) -> list[str]:
    out = set()
    for m in _IDENT_RE.finditer(text):
        raw = m.group(1).strip(".:")
        tok = raw.lower()
        if len(tok) < 3 or len(tok) > 120 or re.fullmatch(r"\d+\.\d+", tok):  # "2.0", "1.5": too generic
            continue
        has_digit = any(c.isdigit() for c in tok)
        structured = any(c in tok for c in "_/") or ("-" in tok and has_digit) or ("." in tok and not tok.endswith("."))
        if has_digit and not tok.isdigit() or structured or _CAMEL.fullmatch(raw):
            out.add(tok)
        if "." in raw:  # field paths: .spec.concurrencyPolicy also yields concurrencypolicy
            out.update(p.lower() for p in raw.split(".") if len(p) >= 3 and _CAMEL.fullmatch(p))
            if "/" in tok and tok.startswith("/"):
                out.add(tok.rstrip("/"))
    return sorted(out)


def _windows(text: str, target: int, overlap: int) -> list[str]:
    """Split a section into ~target-sized windows on paragraph/sentence boundaries, carrying ``overlap``
    characters (rounded to a word boundary) from the end of one window into the next."""
    max_len = int(target * 1.5)
    if len(text) <= max_len:
        return [text]
    paras = re.split(r"(\n\s*\n)", text)
    pieces: list[str] = []
    for p in paras:
        if len(p) > max_len:  # very long paragraph: split on sentences
            pieces.extend(re.split(r"(?<=[.!?])\s+", p))
        else:
            pieces.append(p)
    out: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + len(p) > target:
            out.append(cur.strip())
            cur = cur[-overlap:] if overlap else ""
            cut = cur.find(" ")
            cur = cur[cut + 1 :] if cut != -1 else cur
        cur += p
        while len(cur) > max_len:  # pathological text without breaks
            out.append(cur[:max_len])
            cur = cur[max_len - overlap :] if overlap else cur[max_len:]
    if cur.strip():
        out.append(cur.strip())
    return [o for o in out if o]


def chunk_document(doc: ParsedDocument, size: int = 900, overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    for s in doc.sections:
        for w in _windows(s.text, size, overlap):
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    heading_path=s.heading_path,
                    text=w,
                    page=s.page,
                    anchor=s.anchor,
                    idents=extract_idents(w + " " + s.heading_path),
                    links=extract_links(w),
                )
            )
    if chunks:
        chunks[0].links = doc.relations + chunks[0].links
    return chunks


def doc_summary(doc: ParsedDocument, limit: int) -> str:
    """Front-matter description/summary, else the document's first paragraph, cut to `limit` characters."""
    if limit <= 0:
        return ""
    s = str(doc.metadata.get("description") or doc.metadata.get("summary") or "")
    for sec in doc.sections if not s else []:
        s = next((p for p in sec.text.split("\n\n") if p.strip() and not p.lstrip().startswith(("```", "|"))), "")
        if s:
            break
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + " …"


def embedding_text(doc_title: str, chunk: Chunk, summary: str = "") -> str:
    """Text passed to the document encoder: cheap contextual header (title, heading path and, optionally,
    what the document is about) + passage. The summary is skipped for the passage that contains it."""
    head = doc_title if not chunk.heading_path else f"{doc_title} — {chunk.heading_path}"
    if summary and summary.removesuffix(" …")[:80] not in " ".join(chunk.text.split()):
        head += f"\n{summary}"
    return f"{head}\n{chunk.text}"
