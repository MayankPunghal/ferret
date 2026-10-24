@echo off
rem Double-click to copy a website or intranet as Markdown files. Opens a page in your browser; the copies are
rem kept in the "output" folder next to this file.
setlocal
cd /d "%~dp0"
title site2md - keep this window open while you copy

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv, which runs this tool - one time only...
  powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

uv run --quiet --project "%~dp0." --extra browser site2md ui --copies "%~dp0output"
pause
