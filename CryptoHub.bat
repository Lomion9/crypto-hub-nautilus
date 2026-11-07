@echo off
cd /d "%~dp0"
pythonw run_desktop.py
if errorlevel 1 (
  echo Uygulama acilamadi.
  python run_desktop.py
  pause
)
