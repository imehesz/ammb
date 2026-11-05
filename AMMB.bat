@echo off
rem Double-click to open the AMMB window (uses the .venv next to this file).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo .venv not found - see README for install steps.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m ammb.gui
