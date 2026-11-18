@echo off
rem One command to run Job Radar: stops any copy already running, starts the
rem web app with the latest code and opens it in the browser.
rem Scan, match and apply all happen from the web page. Ctrl+C stops it.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 1 (
  echo Keeping the running scan - opening the page.
  start http://127.0.0.1:8765
  exit /b 0
)
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8765"
"C:\Program Files\Git\bin\bash.exe" ./run-web.sh
