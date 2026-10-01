@echo off
cd /d "%~dp0"

echo [1/2] Installing packages...
python -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo [2/2] Building AinBooker.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name AinBooker --collect-all customtkinter --hidden-import truststore --hidden-import cryptography app.py
if errorlevel 1 goto fail

echo.
echo Build complete: dist\AinBooker.exe
pause
exit /b 0

:fail
echo.
echo Build failed. Check that Python 3.10+ is installed and on PATH.
pause
exit /b 1
