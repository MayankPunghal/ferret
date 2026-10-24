#!/bin/bash
# Double-click (macOS) or run (Linux: bash "Crawl Website.command") to copy a website or intranet as Markdown
# files. Opens a page in your browser; the copies are kept in the "output" folder next to this file.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  echo "Installing uv, which runs this tool - one time only..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv run --quiet --project . --extra browser site2md ui --copies "$(pwd)/output"
