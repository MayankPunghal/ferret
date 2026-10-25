# Stops Ferret before an upgrade or uninstall: the database shuts down cleanly first, then anything still
# running from the install folder (the tray app, the model server) is ended, until nothing is left.
param([string]$App = $PSScriptRoot)
$pgctl = Get-ChildItem "$App\python\Lib\site-packages\pixeltable_pgserver" -Recurse -Filter pg_ctl.exe -ErrorAction SilentlyContinue | Select-Object -First 1
$data = Join-Path $env:USERPROFILE "Ferret\pg"
if ($pgctl -and (Test-Path "$data\postmaster.pid")) { & $pgctl.FullName stop -D $data -m fast -w -t 30 2>$null | Out-Null }
foreach ($i in 1..10) {
    $left = Get-Process | Where-Object { $_.Path -like "$App\*" }
    if (-not $left) { break }
    $left | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}
