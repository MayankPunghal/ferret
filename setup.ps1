# One-command setup for Windows (called by setup.bat). Mirrors setup.sh.
#   setup.bat            install deps, start PostgreSQL, download the model, migrate, create an admin
#   setup.bat -Demo      ...and load the FICTIONAL sample corpus with demo users (alice/bob/hannah)
#   setup.bat -Start     ...and start the server at http://127.0.0.1:8000 when done
# Safe to re-run: every step is idempotent.
param([switch]$Demo, [switch]$Start)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Run { & $args[0] $args[1..($args.Length - 1)]; if ($LASTEXITCODE -ne 0) { Die "command failed: $args" } }

Step "Checking prerequisites"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die "Docker Desktop is required (https://docs.docker.com/desktop/)." }
docker info *> $null; if ($LASTEXITCODE -ne 0) { Die "Docker is installed but not running. Start Docker Desktop." }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found - installing it (https://docs.astral.sh/uv/)"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Run uv --version

Step "Starting PostgreSQL + pgvector (docker compose service 'db')"
Run docker compose up -d db
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    docker compose exec -T db pg_isready -U ks -d ks *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { Die "database did not become ready" }

Step "Installing Python dependencies (uv sync)"
Run uv sync
Step "Downloading the embedding model (one-time, ~70 MB)"
Run uv run knowledge-search download-model
Step "Applying database migrations"
Run uv run knowledge-search migrate

function UserExists($name) {
    $r = docker compose exec -T db psql -U ks -d ks -tAc "SELECT 1 FROM users WHERE username = '$name'"
    return ($r -match "1")
}

Step "Creating the admin account"
if (UserExists "admin") { Write-Host "user 'admin' already exists - keeping it" }
else {
    uv run knowledge-search create-user admin --admin --password - | Tee-Object -FilePath .admin-password.txt
    Write-Host "(saved to .admin-password.txt - delete it after you have stored the password)"
}

if ($Demo) {
    Step "Loading the FICTIONAL demo corpus and demo users"
    $ks = "uv run knowledge-search"
    foreach ($g in @("engineering staff", "engineering hr", "finance staff", "finance hr", "hr hr")) {
        Invoke-Expression "$ks grant $g" | Out-Null
    }
    Invoke-Expression "$ks grant engineering eng-writers --write" | Out-Null
    foreach ($u in @("alice", "bob", "hannah")) {
        if (-not (UserExists $u)) { Invoke-Expression "$ks create-user $u --password demo-password-$u" | Out-Null }
    }
    foreach ($m in @("alice staff", "alice eng-writers", "bob staff", "hannah hr")) {
        Invoke-Expression "$ks add-to-group $m" | Out-Null
    }
    foreach ($c in @("engineering", "finance", "hr")) { Invoke-Expression "$ks ingest $c fixtures/corpus/$c --wait" }
    Write-Host "`nDemo users (password = demo-password-<name>): alice, bob (read-only), hannah (sees 'hr')"
}

Step "Done"
Write-Host "Start the server:  uv run knowledge-search serve   ->  http://127.0.0.1:8000"
Write-Host "Run the tests:     uv run pytest -q"
if ($Start) { uv run knowledge-search serve }
