# One-click setup for Windows (double-click setup.bat). No Docker, no admin rights, no manual installs:
#   1. installs uv (Python package manager) for the current user if missing
#   2. uv installs Python 3.13, all dependencies and an embedded PostgreSQL + pgvector
#   3. downloads the offline AI models, initialises the database
#   4. optionally loads sample documents, creates a desktop shortcut, starts the app in your browser
# Options: -Demo (load sample data without asking), -NoDemo, -NoStart, -NoShortcut. Safe to re-run.
param([switch]$Demo, [switch]$NoDemo, [switch]$NoStart, [switch]$NoShortcut)

# Windows PowerShell 5.1 turns stderr output of native commands into terminating errors when
# ErrorActionPreference is "Stop" (uv writes progress to stderr), so check exit codes explicitly.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$Host.UI.RawUI.WindowTitle = "Knowledge Search setup"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Die($msg) {
    Write-Host "`nSETUP FAILED: $msg" -ForegroundColor Red
    Write-Host "Fix the problem above and double-click setup.bat again (it is safe to re-run)."
    exit 1
}
function Run {
    param([string]$Exe, [Parameter(ValueFromRemainingArguments = $true)]$Rest)
    & $Exe @Rest
    if ($LASTEXITCODE -ne 0) { Die "command failed ($LASTEXITCODE): $Exe $Rest" }
}

Step "Checking uv (Python package manager)"
$uvDir = Join-Path $env:USERPROFILE ".local\bin"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Test-Path (Join-Path $uvDir "uv.exe")) { $env:Path = "$uvDir;$env:Path" }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv for the current user (no administrator rights needed) from https://astral.sh/uv ..."
    & powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$uvDir;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Die "uv could not be installed. Check the internet connection / proxy, or install it manually: https://docs.astral.sh/uv/getting-started/installation/"
    }
}
Run uv --version

Step "Installing Python 3.13, dependencies and the embedded database (first run downloads ~600 MB)"
Run uv sync --no-dev

Step "Downloading the offline AI models (embedding model + reranker; one-time)"
Run uv run --no-dev knowledge-search download-model

Step "Initialising the local database"
Run uv run --no-dev knowledge-search migrate

$loadDemo = $Demo
if (-not $Demo -and -not $NoDemo) {
    $ans = Read-Host "Load the FICTIONAL sample documents so you can try searching right away? [Y/n]"
    $loadDemo = ($ans -eq "" -or $ans -match "^[Yy]")
}
if ($loadDemo) {
    Step "Loading fictional sample documents and demo users"
    Run uv run --no-dev knowledge-search demo
}

if (-not $NoShortcut) {
    Step "Creating a 'Knowledge Search' shortcut on the desktop"
    try {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Knowledge Search.lnk"))
        $lnk.TargetPath = Join-Path $PSScriptRoot "start.bat"
        $lnk.WorkingDirectory = $PSScriptRoot
        $lnk.Description = "Start Knowledge Search and open it in the browser"
        $lnk.Save()
        Write-Host "Shortcut created."
    } catch { Write-Host "Could not create the shortcut ($_). Use start.bat in this folder instead." -ForegroundColor Yellow }
}

Step "Setup complete"
Write-Host "Start the app any time with the desktop shortcut or start.bat. It opens http://127.0.0.1:8000"
Write-Host "On first start the browser asks you to create the administrator account."
if (-not $NoStart) {
    Write-Host "Starting now - keep this window open while you use the app (close it to stop)."
    & uv run --no-dev knowledge-search serve --open
}
