@echo off
chcp 65001 >nul
cd /d "%~dp0"
pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --name AinBooker ^
  --collect-all customtkinter ^
  --hidden-import truststore ^
  --hidden-import cryptography ^
  app.py
echo.
echo 빌드 완료: dist\AinBooker.exe
pause
