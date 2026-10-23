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

echo.
echo Which browser should it use?
echo   1  Google Chrome (installed on this computer)
echo   2  Microsoft Edge (installed on this computer)
echo   3  Firefox (its own copy, downloaded once, about 100 MB)
echo   4  Chromium (its own copy, downloaded once, about 150 MB)
choice /c 1234 /n /m "Press 1, 2, 3 or 4: "
set "CH=chrome"
if errorlevel 2 set "CH=msedge"
if errorlevel 3 set "CH=firefox"
if errorlevel 4 set "CH=chromium"
if "%CH%"=="firefox" uv run --quiet --project "%~dp0." --extra browser playwright install firefox
if "%CH%"=="chromium" uv run --quiet --project "%~dp0." --extra browser playwright install chromium

set "LOGIN=--login"
if not exist "%USERPROFILE%\.site2md\profile-%CH%" goto :signin
choice /c YN /n /m "Sign in to the site again first? (Y/N) "
if errorlevel 2 set "LOGIN="
:signin
if defined LOGIN (
  echo.
  echo A browser window will open. Sign in if the site asks you to - until you see the site -
  echo then come back to this window and press Enter.
)

echo.
uv run --quiet --project "%~dp0." --extra browser site2md "%URL%" --out "%OUT%" --browser --channel %CH% %LOGIN% %RESUME%
echo.
echo The pages are in: %OUT%
start "" "%OUT%"

:end
pause
