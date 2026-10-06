#Requires -Version 5.1
# start-chrome-extract.ps1 - dedicated Chrome on 9225 for link extraction and
# Amazon work, using the local IP (no proxy).
. "$PSScriptRoot\windows-common.ps1"
Import-DotEnv

$chrome  = Get-ChromePath
$profile = Join-Path $env:TEMP "chrome-extract-profile-$ChromeExtractPort"

if (Test-PortOpen $ChromeExtractPort) {
    Write-Host "Extraction Chrome already running on $ChromeExtractPort"
} else {
    Write-Host "Starting extraction Chrome on $ChromeExtractPort (no proxy)"
    Start-Process -FilePath $chrome -ArgumentList @(
        "--remote-debugging-port=$ChromeExtractPort",
        "--user-data-dir=$profile",
        "--no-first-run", "--no-default-browser-check"
    ) | Out-Null
    Start-Sleep -Seconds 5
}

if (Test-PortOpen $ChromeExtractPort) { Write-Host "OK: Chrome CDP on $ChromeExtractPort" }
else { Write-Warning "FAILED: Chrome CDP not reachable on $ChromeExtractPort" }
