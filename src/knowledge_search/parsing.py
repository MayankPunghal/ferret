"""Document validation, text extraction and structure-aware chunking.

Supported in this release: Markdown (.md), plain text (.txt) and text-layer PDF (.pdf, via
pypdf). Scanned PDFs without a text layer are rejected with ``no_extractable_text`` rather
than silently indexed as empty (OCR is out of scope).
"""

import io
import re
from dataclasses import dataclass, field

import yaml
from markdown_it import MarkdownIt

PARSER_VERSION = "parse-v1"
CHUNKER_VERSION = "chunk-v2"  # size/overlap are runtime settings, recorded in the index signature

MEDIA_TYPES = {".md": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf"}



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


@dataclass
class Chunk:
    ordinal: int
    heading_path: str
    text: str
    page: int | None
    anchor: str | None
    idents: list[str]


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
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ParseError("invalid_encoding", "text files must be UTF-8") from e


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            try:
                meta = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError as e:
                raise ParseError("invalid_frontmatter", str(e)[:200]) from e
            if not isinstance(meta, dict):
                raise ParseError("invalid_frontmatter", "front matter must be a mapping")
            body = text[end + 4 :].lstrip("\n")
            return {str(k): (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in meta.items()}, body
    return {}, text


def parse_markdown(data: bytes, filename: str) -> ParsedDocument:
    meta, body = _split_frontmatter(_decode_text(data))
    tokens = MarkdownIt("commonmark").enable("table").parse(body)
    lines = body.splitlines()
    # Heading positions from the token stream (avoids treating '#' in code blocks as headings).
    heads: list[tuple[int, int, str]] = []
    for i, t in enumerate(tokens):
        if t.type == "heading_open" and t.map:
            heads.append((t.map[0], int(t.tag[1]), tokens[i + 1].content.strip()))
    title = str(meta.get("title") or next((h[2] for h in heads if h[1] == 1), "") or filename)
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
        stack = [s for s in stack if s[0] < level] + [(level, htext)]
        end = bounds[idx + 1][0]
        text = "\n".join(lines[start + 1 : end]).strip()
        slug = slugify(htext)
        n = used_anchors.get(slug, 0)
        used_anchors[slug] = n + 1
        anchor = slug if n == 0 else f"{slug}-{n}"
        if text:
            sections.append(Section(heading_path=" > ".join(s[1] for s in stack), text=text, anchor=anchor))
    return ParsedDocument(title=title, metadata=meta, sections=sections)


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


def parse_document(data: bytes, filename: str, max_pages: int = 2000, max_chars: int = 5_000_000) -> ParsedDocument:
    if not data:
        raise ParseError("empty_file", "file is empty")
    mt = media_type_for(filename)
    if mt == "application/pdf":
        doc = parse_pdf(data, filename, max_pages)
    elif mt == "text/markdown":
        doc = parse_markdown(data, filename)
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


def extract_idents(text: str) -> list[str]:
    out = set()
    for m in _IDENT_RE.finditer(text):
        tok = m.group(1).strip(".:").lower()
        if len(tok) < 3 or len(tok) > 120:
            continue
        has_digit = any(c.isdigit() for c in tok)
        structured = any(c in tok for c in "_/") or ("-" in tok and has_digit) or ("." in tok and not tok.endswith("."))
        if has_digit and not tok.isdigit() or structured:
            out.add(tok)
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
                )
            )
    return chunks


def embedding_text(doc_title: str, chunk: Chunk) -> str:
    """Text passed to the document encoder: cheap contextual header + passage."""
    head = doc_title if not chunk.heading_path else f"{doc_title} — {chunk.heading_path}"
    return f"{head}\n{chunk.text}"
