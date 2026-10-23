#!/bin/bash
# Double-click (macOS) or run (Linux: bash "Crawl Website.command") to save a website or intranet as Markdown
# files, one per page, in the "output" folder.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  echo "Installing uv, which runs this tool - one time only..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
run() { uv run --quiet --project . --extra browser "$@"; }

echo
read -r -p "Website address (paste it, then press Enter): " URL
[ -z "$URL" ] && exit 0
read -r -p "Name for this copy [site]: " NAME
OUT="output/${NAME:-site}"
RESUME=""
if [ -f "$OUT/manifest.json" ]; then
  echo "This copy already exists: it will be continued and its failed pages tried again."
  RESUME="--resume"
fi
LOGIN="--login"
if [ -d "$HOME/.site2md/browser-profile" ]; then
  read -r -p "Sign in to the site again first? (y/N) " A
  [[ "$A" =~ ^[Yy] ]] || LOGIN=""
fi
[ -n "$LOGIN" ] && printf '\nA browser window will open. Sign in if the site asks you to - until you see the site -\nthen come back to this window and press Enter.\n'

run playwright install chromium >/dev/null  # the browser it uses (downloaded once)
echo
run site2md "$URL" --out "$OUT" --browser $LOGIN $RESUME
echo
echo "The pages are in: $(pwd)/$OUT"
(open "$OUT" || xdg-open "$OUT") >/dev/null 2>&1
read -r -p "Press Enter to close"
