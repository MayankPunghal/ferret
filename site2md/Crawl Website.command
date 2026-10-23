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
echo
echo "Which browser should it use?"
echo "  1  Google Chrome (installed on this computer)"
echo "  2  Firefox (its own copy, downloaded once, about 100 MB)"
echo "  3  Chromium (its own copy, downloaded once, about 150 MB)"
echo "  4  Microsoft Edge (installed on this computer)"
read -r -p "Press 1, 2, 3 or 4, then Enter: " B
case "$B" in 1) CH=chrome ;; 2) CH=firefox ;; 4) CH=msedge ;; *) CH=chromium ;; esac
case "$CH" in firefox|chromium) run playwright install "$CH" >/dev/null ;; esac
LOGIN="--login"
if [ -d "$HOME/.site2md/profile-$CH" ]; then
  read -r -p "Sign in to the site again first? (y/N) " A
  [[ "$A" =~ ^[Yy] ]] || LOGIN=""
fi
[ -n "$LOGIN" ] && printf '\nA browser window will open. Sign in if the site asks you to - until you see the site -\nthen come back to this window and press Enter.\n'

echo
run site2md "$URL" --out "$OUT" --browser --channel "$CH" $LOGIN $RESUME
echo
echo "The pages are in: $(pwd)/$OUT"
(open "$OUT" || xdg-open "$OUT") >/dev/null 2>&1
read -r -p "Press Enter to close"
