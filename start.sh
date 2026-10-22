#!/usr/bin/env bash
# Start Knowledge Search and open it in the browser (Ctrl+C to stop).
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
exec uv run --no-dev knowledge-search serve --open "$@"
