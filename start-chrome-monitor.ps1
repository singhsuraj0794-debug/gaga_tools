#Requires -Version 5.1
# start-chrome-monitor.ps1 - Chrome on 9222, used by _lens_browser.py for the
# Price Mapper's FREE Google Lens reverse-image search (no SearchAPI credits).
# Uses the local IP (no proxy) — Lens is a public Google page.
. "$PSScriptRoot\windows-common.ps1"
Import-DotEnv

$chrome  = Get-ChromePath
$profile = Join-Path $env:TEMP "chrome-lens-profile-9222"

if (Test-PortOpen $MonitorChromePort) {
    Write-Host "Lens Chrome already running on $MonitorChromePort"
} else {
    Write-Host "Starting Lens Chrome on $MonitorChromePort (no proxy)"
    Start-Process -FilePath $chrome -ArgumentList @(
        "--remote-debugging-port=$MonitorChromePort",
        "--user-data-dir=$profile",
        "--no-first-run", "--no-default-browser-check"
    ) | Out-Null
    Start-Sleep -Seconds 5
}

if (Test-PortOpen $MonitorChromePort) { Write-Host "OK: Chrome CDP on $MonitorChromePort" }
else { Write-Warning "FAILED: Chrome CDP not reachable on $MonitorChromePort" }
