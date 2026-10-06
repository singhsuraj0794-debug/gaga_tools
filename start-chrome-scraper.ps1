#Requires -Version 5.1
# start-chrome-scraper.ps1 - Chrome on 9223 routed through the local Webshare
# auth-forwarding proxy on 9224 (Chrome cannot take proxy credentials directly).
# Ports: 9223 scraping (proxy) | 9225 extraction (no proxy) | 9222 monitoring.
. "$PSScriptRoot\windows-common.ps1"
Import-DotEnv

$chrome  = Get-ChromePath
$python  = Get-Python
$profile = Join-Path $env:TEMP "chrome-scraper-profile-$ChromeScraperPort"

if (-not (Test-PortOpen $LocalProxyPort)) {
    Write-Host "Starting local Webshare proxy on $LocalProxyPort ..."
    Start-Bg -FilePath $python -Arguments @((Join-Path $RepoDir "local_proxy.py"), "$LocalProxyPort") -LogName "local-proxy.log" | Out-Null
    Start-Sleep -Seconds 3
}

if (Test-PortOpen $ChromeScraperPort) {
    Write-Host "Scraping Chrome already running on $ChromeScraperPort"
} else {
    Write-Host "Starting scraping Chrome on $ChromeScraperPort (via proxy $LocalProxyPort)"
    Start-Process -FilePath $chrome -ArgumentList @(
        "--remote-debugging-port=$ChromeScraperPort",
        "--user-data-dir=$profile",
        "--proxy-server=http://localhost:$LocalProxyPort",
        "--no-first-run", "--no-default-browser-check"
    ) | Out-Null
    Start-Sleep -Seconds 5
}

if (Test-PortOpen $ChromeScraperPort) { Write-Host "OK: Chrome CDP on $ChromeScraperPort" }
else { Write-Warning "FAILED: Chrome CDP not reachable on $ChromeScraperPort" }
