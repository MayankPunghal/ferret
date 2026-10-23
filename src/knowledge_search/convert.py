"""HTML and Word (.docx) → Markdown, so every format goes through the same Markdown parser (headings →
sections and anchors, links → the document graph). The converters live in the crawler package (site2md),
which saves crawled pages and documents as Markdown the same way."""

from site2md.docs import docx_to_markdown  # noqa: F401  (re-exported)
from site2md.html import html_to_markdown  # noqa: F401  (re-exported)
