@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m tolivephoto.app %*
    exit /b 0
)
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process python -ArgumentList '-m','tolivephoto.app' -WindowStyle Hidden"
exit /b 0
