@echo off
cd /d "%~dp0"
python -m tolivephoto.app %*
if errorlevel 1 pause
