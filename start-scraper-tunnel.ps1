#Requires -Version 5.1
# start-scraper-tunnel.ps1 - local scraper server on :9000 + the permanent ngrok
# domain so Render can offload Playwright scraping to this box. The scraper
# server also reverse-proxies /api/* to the pre-listing compute API on :8090
# through the SAME domain.
. "$PSScriptRoot\windows-common.ps1"
Import-DotEnv

# The watchdog may call this while old scraper processes are still bound to
# :9000 (SO_REUSEADDR allows duplicates). Keep only the newest instance.
$existingScrapers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*local_scraper_server.py*' })
if ($existingScrapers.Count -gt 1) {
    Write-Host "Found $($existingScrapers.Count) scraper servers - keeping the newest"
    $keep = ($existingScrapers | Sort-Object CreationDate -Descending | Select-Object -First 1).ProcessId
    $existingScrapers | Where-Object { $_.ProcessId -ne $keep } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}

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
