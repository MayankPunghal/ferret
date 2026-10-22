#!/usr/bin/env bash
# One-command setup for Linux/macOS (Windows: run setup.bat).
#
#   ./setup.sh            install deps, start PostgreSQL, download the model, migrate, create an admin
#   ./setup.sh --demo     ...and load the FICTIONAL sample corpus with demo users (alice/bob/hannah)
#   ./setup.sh --start    ...and start the server at http://127.0.0.1:8000 when done
#
# Safe to re-run: every step is idempotent (existing users/collections/documents are kept).
set -euo pipefail
cd "$(dirname "$0")"

DEMO=0; START=0
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=1 ;;
    --start) START=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

step "Checking prerequisites"
command -v docker >/dev/null || die "Docker is required (https://docs.docker.com/get-docker/)."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop / the docker service."
if ! command -v uv >/dev/null; then
  echo "uv not found - installing it (https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

step "Starting PostgreSQL + pgvector (docker compose service 'db')"
docker compose up -d db
for _ in $(seq 1 60); do
  docker compose exec -T db pg_isready -U ks -d ks >/dev/null 2>&1 && break
  sleep 1
done
docker compose exec -T db pg_isready -U ks -d ks >/dev/null || die "database did not become ready"

step "Installing Python dependencies (uv sync)"
uv sync

KS="uv run knowledge-search"
step "Downloading the embedding model (one-time, ~70 MB)"
$KS download-model
step "Applying database migrations"
$KS migrate

user_exists() {
  docker compose exec -T db psql -U ks -d ks -tAc "SELECT 1 FROM users WHERE username = '$1'" | grep -q 1
}

step "Creating the admin account"
if user_exists admin; then
  echo "user 'admin' already exists - keeping it"
else
  $KS create-user admin --admin --password - | tee .admin-password.txt
  chmod 600 .admin-password.txt
  echo "(saved to .admin-password.txt - delete it after you have stored the password)"
fi

if [ "$DEMO" = 1 ]; then
  step "Loading the FICTIONAL demo corpus and demo users"
  $KS grant engineering staff >/dev/null; $KS grant engineering hr >/dev/null
  $KS grant finance staff >/dev/null;     $KS grant finance hr >/dev/null
  $KS grant hr hr >/dev/null;             $KS grant engineering eng-writers --write >/dev/null
  for u in alice bob hannah; do
    user_exists "$u" || $KS create-user "$u" --password "demo-password-$u" >/dev/null
  done
  $KS add-to-group alice staff >/dev/null; $KS add-to-group alice eng-writers >/dev/null
  $KS add-to-group bob staff >/dev/null;   $KS add-to-group hannah hr >/dev/null
  for c in engineering finance hr; do
    $KS ingest "$c" "fixtures/corpus/$c" --wait 2>/dev/null | grep -E "queued|unchanged|rejected|processed" || true
  done
  cat <<'EOF'

Demo users (password = demo-password-<name>):
  alice   reads engineering + finance, can upload to engineering
  bob     reads engineering + finance (read-only)
  hannah  reads everything including the confidential 'hr' collection
Try: ERR-PAY-4012 | how do I undo a bad release of the orders service |
     per diem for domestic travel (conflicting versions) | parental leave (as bob vs hannah)
EOF
fi

step "Done"
echo "Start the server:  uv run knowledge-search serve      ->  http://127.0.0.1:8000"
echo "Run the tests:     uv run pytest -q"
if [ "$START" = 1 ]; then
  exec uv run knowledge-search serve
fi
