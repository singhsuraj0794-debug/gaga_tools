#Requires -Version 5.1
# run-stack.ps1 - scheduled-task entry point. Starts the whole local stack and
# then STAYS ALIVE, so the child processes it launched are not reaped when the
# task instance completes (on Windows, background processes started from an SSH
# session or a short-lived task are killed when that session/task ends).
#
# Registered as scheduled task "Gajab-Stack" (trigger: at logon).
. "$PSScriptRoot\windows-common.ps1"

& "$PSScriptRoot\start-all.ps1"

Write-Host "[run-stack] stack started; staying alive to keep children alive."

# Self-heal the Chromes. They are the fragile part - they crash under load - and
# start-all only launches them at boot, so a dead CDP port silently turned every
# extract/scrape into "Chrome CDP not available" (which surfaced as a job
# completing with 0 products). Re-check every minute and bring them back.
#
# A port check is NOT enough: a wedged Chrome keeps listening AND keeps answering
# /json/version, but Playwright can no longer attach, so every extract failed with
# "BrowserType.connect_over_cdp: Connection closed while reading from the driver"
# and a store scrape quietly ended after a fraction of its products. So verify by
# actually attaching (Test-CdpHealthy) and, when unhealthy, KILL the browser
# first - start-chrome-*.ps1 refuses to start while the port is still open.
function Restart-ChromeIfUnhealthy {
    param([int]$Port, [string]$Label, [string]$Script)
    if ((Test-PortOpen $Port) -and (Test-CdpHealthy $Port)) { return }
    Write-Host "[run-stack] $Label Chrome ($Port) is unhealthy - killing and restarting"
    Stop-ChromeOnPort -Port $Port | Out-Null
    & (Join-Path $PSScriptRoot $Script)
}

while ($true) {
    Start-Sleep -Seconds 60
    Restart-ChromeIfUnhealthy -Port $ChromeExtractPort -Label "extraction" -Script "start-chrome-extract.ps1"
    Restart-ChromeIfUnhealthy -Port $ChromeScraperPort -Label "scraping"   -Script "start-chrome-scraper.ps1"
    Restart-ChromeIfUnhealthy -Port $MonitorChromePort -Label "Lens"       -Script "start-chrome-monitor.ps1"
}
