@echo off
chcp 65001 >nul
cd /d "%~dp0"
python assets\generate_icon.py
python -m PyInstaller --noconfirm --onefile --windowed --icon="assets/icon.ico" --add-data "assets;assets" --name "悬浮剪贴板" main.py
copy /Y "dist\悬浮剪贴板.exe" "悬浮剪贴板.exe" >nul
echo Build complete: %~dp0悬浮剪贴板.exe