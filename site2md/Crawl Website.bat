@echo off
rem Double-click to save a website or intranet as Markdown files (one per page) in the "output" folder.
setlocal
cd /d "%~dp0"
title Website to Markdown (site2md)

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv, which runs this tool - one time only...
  powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo.
set "URL="
set /p "URL=Website address (paste it, then press Enter): "
if not defined URL goto :end
set "NAME=site"
set /p "NAME=Name for this copy [site]: "
set "OUT=%~dp0output\%NAME%"

set "RESUME="
if exist "%OUT%\manifest.json" (
  echo This copy already exists: it will be continued and its failed pages tried again.
  set "RESUME=--resume"
)

set "LOGIN=--login"
if not exist "%USERPROFILE%\.site2md\browser-profile" goto :signin
choice /c YN /n /m "Sign in to the site again first? (Y/N) "
if errorlevel 2 set "LOGIN="
:signin
if defined LOGIN (
  echo.
  echo An Edge window will open. Sign in if the site asks you to - until you see the site -
  echo then come back to this window and press Enter.
)

echo.
uv run --quiet --project "%~dp0." --extra browser site2md "%URL%" --out "%OUT%" --browser --channel msedge %LOGIN% %RESUME%
echo.
echo The pages are in: %OUT%
start "" "%OUT%"

:end
pause
