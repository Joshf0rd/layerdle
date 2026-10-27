@echo off
rem Double-click to turn the songs in .\incoming into game clips and publish them.
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

rem Get clip fixes sent from the phone (review page -> GitHub fixes/ folder).
git pull -q --rebase --autostash
if errorlevel 1 echo Could not get fixes from GitHub (offline?). Carrying on with what's here.

"%PY%" process_songs.py %*

rem Publish whatever got added (songs are saved one by one, so even a partial run is safe to push).
git add songs songs.json
git add -A fixes 2>nul
git diff --cached --quiet
if not errorlevel 1 (
  echo.
  echo Nothing new to publish.
  goto end
)
echo.
echo Publishing to the website...
git commit -q -m "Add songs (add_songs.cmd)"
git push -q
if errorlevel 1 (
  echo PUSH FAILED. The songs are saved on this PC; ask Claude to push them.
) else (
  echo Published! The app updates in about a minute.
)

:end
echo.
pause
