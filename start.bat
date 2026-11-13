@echo off
rem One command to run Job Radar: starts the web app and opens it in the browser.
rem Scan, match and apply all happen from the web page. Ctrl+C stops it.
cd /d "%~dp0"
rem Already running? Just open the page - a second copy can't use the same port.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  echo Job Radar is already running - opening it.
  start http://127.0.0.1:8765
  exit /b 0
)
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8765"
"C:\Program Files\Git\bin\bash.exe" ./run-web.sh
