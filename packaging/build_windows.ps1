# Builds dist\Ferret: a portable Python 3.13 (uv's standalone build) with Ferret and its locked dependencies
# installed into it, so the native parts (ONNX Runtime, the embedded PostgreSQL) run exactly as from pip.
# Then: iscc /DAppVersion=<version> packaging\installer.iss  ->  dist\Ferret-Setup-<version>.exe
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot
$build = "$root\build\windows"
$out = "$root\dist\Ferret"
Remove-Item -Recurse -Force $build, $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $build, $out | Out-Null

uv python install 3.13 --install-dir "$build\python"
if ($LASTEXITCODE) { throw "uv python install failed" }
$py = Get-ChildItem "$build\python" -Directory -Filter "cpython-3.13*" | Select-Object -First 1
Copy-Item -Recurse $py.FullName "$out\python"
Remove-Item -Force "$out\python\Lib\EXTERNALLY-MANAGED" -ErrorAction SilentlyContinue  # ours to install into

Push-Location $root
uv build --all-packages -o "$build\wheels"
uv export --frozen --no-dev --extra tray --no-emit-workspace --no-hashes -o "$build\requirements.txt"
Pop-Location
if ($LASTEXITCODE) { throw "uv build or export failed" }
$wheels = Get-ChildItem "$build\wheels\*.whl" | ForEach-Object FullName
uv pip install --python "$out\python\python.exe" -r "$build\requirements.txt" @wheels
if ($LASTEXITCODE) { throw "installing into the portable Python failed" }
Copy-Item "$PSScriptRoot\ferret.ico", "$PSScriptRoot\stop.ps1" $out
"{0:N0} MB" -f ((Get-ChildItem -Recurse $out | Measure-Object Length -Sum).Sum / 1MB)
