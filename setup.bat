@echo off
REM Windows entry point: runs setup.ps1 (PowerShell ships with Windows). Usage: setup.bat [-Demo] [-Start]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 (
  echo Setup failed. See the messages above.
  pause
  exit /b 1
)
