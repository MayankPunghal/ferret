@echo off
REM One-click setup: double-click this file. Runs setup.ps1 with PowerShell (built into Windows).
REM Options: setup.bat -Demo ^| -NoDemo ^| -NoStart ^| -NoShortcut ^| -Summaries ^| -NoSummaries
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the message above, fix it, then run setup.bat again.
  pause
  exit /b 1
)
pause
