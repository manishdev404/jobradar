# Stops a running Job Radar web app, asking first when a scan is in the middle.
# start.bat runs this before starting, so a start always gets the latest code.
# Exit code 1: you chose to keep the running scan.
try {
  $state = (Invoke-WebRequest http://127.0.0.1:8765/api/state -UseBasicParsing -TimeoutSec 3).Content | ConvertFrom-Json
} catch { $state = $null }
if ($state -and $state.scanning) {
  $answer = Read-Host "A scan is running. Stop it and restart? (y/n)"
  if ($answer -notmatch '^[yY]') { exit 1 }
}
# Only the web app: uvicorn serving jobradar.web.server, and run-web.sh around it.
$procs = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'jobradar\.web\.server|run-web\.sh' }
foreach ($p in $procs) { taskkill /PID $p.ProcessId /T /F 2>$null | Out-Null }
if ($procs) {
  Write-Host "Stopped the running Job Radar."
  Start-Sleep -Seconds 2
}
exit 0
