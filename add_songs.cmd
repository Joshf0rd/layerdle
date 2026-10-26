@echo off
rem Double-click to turn the songs in .\incoming into game clips.
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" process_songs.py %*
echo.
pause
