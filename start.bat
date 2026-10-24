@echo off
REM Starts Ferret and opens it in the browser. Close this window to stop the app.
title Ferret
cd /d "%~dp0"
where uv 1>NUL 2>NUL || set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv run --no-dev ferret serve --open
if errorlevel 1 pause
