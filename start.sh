#!/usr/bin/env bash
# Start Ferret and open it in the browser (Ctrl+C to stop).
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
exec uv run --no-dev ferret serve --open "$@"
