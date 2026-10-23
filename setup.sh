#!/usr/bin/env bash
# One-command setup for Linux/macOS. No Docker, no root: installs uv for the current user if missing,
# then uv installs Python 3.13, all dependencies and an embedded PostgreSQL + pgvector; downloads the
# offline AI models; initialises the database; optionally loads sample data; starts the app.
#
#   ./setup.sh               interactive (asks about sample data), then starts the app in your browser
#   ./setup.sh --demo        load the FICTIONAL sample corpus without asking
#   ./setup.sh --no-demo     skip sample data      ./setup.sh --no-start   do not start the app
#   ./setup.sh --summaries   also install the optional local language model for document summaries (~0.6 GB)
#   ./setup.sh --no-summaries  skip it without asking
#
# Safe to re-run. Server deployments with an external PostgreSQL: see docs/deployment.md.
set -euo pipefail
cd "$(dirname "$0")"

DEMO=ask; START=1; SUMMARIES=ask
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=yes ;;
    --no-demo) DEMO=no ;;
    --no-start) START=0 ;;
    --summaries) SUMMARIES=yes ;;
    --no-summaries) SUMMARIES=no ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nSETUP FAILED: %s\nFix the problem and run ./setup.sh again (it is safe to re-run).\n' "$*" >&2; exit 1; }

step "Checking uv (Python package manager)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  command -v curl >/dev/null || die "curl is required to install uv"
  echo "Installing uv for the current user from https://astral.sh/uv ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || die "uv could not be installed (network/proxy?)"
fi
uv --version

step "Installing Python 3.13, dependencies and the embedded database (first run downloads ~600 MB)"
uv sync --no-dev || die "dependency installation failed"

step "Downloading the offline AI models (embedding model + reranker; one-time)"
uv run --no-dev knowledge-search download-model || die "model download failed (network/proxy?)"

if [ "$SUMMARIES" = ask ]; then
  if [ -t 0 ]; then
    read -r -p "Also install a small local language model (~0.6 GB) that writes a summary and topics for each document in the background? Nothing leaves this computer. [Y/n] " ans
    case "${ans:-y}" in [Yy]*) SUMMARIES=yes ;; *) SUMMARIES=no ;; esac
  else
    SUMMARIES=no
  fi
fi
if [ "$SUMMARIES" = yes ]; then
  step "Downloading the optional local language model for document summaries (one-time)"
  uv run --no-dev knowledge-search download-llm \
    || echo "WARNING: the summary model could not be downloaded; search works without it. Retry later with: uv run knowledge-search download-llm"
fi

step "Initialising the local database"
uv run --no-dev knowledge-search migrate || die "database initialisation failed"

if [ "$DEMO" = ask ]; then
  if [ -t 0 ]; then
    read -r -p "Load the FICTIONAL sample documents so you can try searching right away? [Y/n] " ans
    case "${ans:-y}" in [Yy]*) DEMO=yes ;; *) DEMO=no ;; esac
  else
    DEMO=no
  fi
fi
if [ "$DEMO" = yes ]; then
  step "Loading fictional sample documents and demo users"
  uv run --no-dev knowledge-search demo || die "loading sample data failed"
fi

step "Setup complete"
echo "Start the app any time with ./start.sh  ->  http://127.0.0.1:8000"
echo "On first start the browser asks you to create the administrator account."
if [ "$START" = 1 ]; then
  echo "Starting now (Ctrl+C to stop)..."
  exec uv run --no-dev knowledge-search serve --open
fi
