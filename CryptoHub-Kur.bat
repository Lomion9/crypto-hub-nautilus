@echo off
cd /d "%~dp0"
python -m pip install -r requirements-desktop.txt pyinstaller
python -m PyInstaller --noconfirm --windowed --name CryptoHub --clean --exclude-module nautilus_trader --exclude-module pandas --exclude-module PySide6.QtWebEngine --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets run_desktop.py
echo.
echo Calistirilacak dosya: dist\CryptoHub\CryptoHub.exe
pause
