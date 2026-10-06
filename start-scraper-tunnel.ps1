#Requires -Version 5.1
# start-scraper-tunnel.ps1 - local scraper server on :9000 + the permanent ngrok
# domain so Render can offload Playwright scraping to this box. The scraper
# server also reverse-proxies /api/* to the pre-listing compute API on :8090
# through the SAME domain.
. "$PSScriptRoot\windows-common.ps1"
Import-DotEnv

if (Test-PortOpen $ScraperPort) {
    Write-Host "Scraper server already running on $ScraperPort"
} else {
    Write-Host "Starting scraper server on $ScraperPort ..."
    Start-Bg -FilePath (Get-Python) -Arguments @("local_scraper_server.py") -WorkingDirectory $ApiDir -LogName "local-scraper.log" | Out-Null
    Start-Sleep -Seconds 3
}

$ngrok = Get-Process ngrok -ErrorAction SilentlyContinue
if ($ngrok) {
    Write-Host "ngrok already running (pid $($ngrok.Id -join ','))"
} else {
    Write-Host "Starting ngrok -> https://$NgrokDomain"
    Start-Process -FilePath (Get-NgrokPath) -ArgumentList @("http", "--url=$NgrokDomain", "$ScraperPort") -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 4
}

Write-Host "Permanent URL: https://$NgrokDomain"
Write-Host "Verify: curl -H 'ngrok-skip-browser-warning: true' https://$NgrokDomain/api/products/status"
