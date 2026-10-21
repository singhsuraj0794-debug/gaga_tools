#Requires -Version 5.1
# run-stack.ps1 - scheduled-task entry point. Starts the whole local stack and
# then STAYS ALIVE, so the child processes it launched are not reaped when the
# task instance completes (on Windows, background processes started from an SSH
# session or a short-lived task are killed when that session/task ends).
#
# Registered as scheduled task "Gajab-Stack" (trigger: at logon).
. "$PSScriptRoot\windows-common.ps1"

$stage = Join-Path $env:TEMP "run-stack.stage"
function Stage([string]$m) {
    Add-Content -Path $stage -Value ((Get-Date -Format "HH:mm:ss") + " " + $m) -ErrorAction SilentlyContinue
}
Stage "run-stack start"
& "$PSScriptRoot\start-all.ps1"
Stage "start-all returned"

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

$heartbeat = Join-Path $env:TEMP "run-stack.heartbeat"
while ($true) {
    # Heartbeat first: without it a stalled iteration is invisible and looks
    # exactly like a watchdog that is running fine.
    Set-Content -Path $heartbeat -Value (Get-Date -Format "o") -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 60
    # Reap leaked Playwright drivers BEFORE the Chrome checks: a slow Chrome
    # stop/start must not delay the one job that prevents a page-file OOM.
    $drivers = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -like '*playwright*run-driver*' })
    if ($drivers.Count -gt 40) {
        Write-Output "[run-stack] reaping $($drivers.Count) leaked Playwright drivers"
        foreach ($d in $drivers) { Stop-Process -Id $d.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    Restart-ChromeIfUnhealthy -Port $ChromeExtractPort -Label "extraction" -Script "start-chrome-extract.ps1"
    Restart-ChromeIfUnhealthy -Port $ChromeScraperPort -Label "scraping"   -Script "start-chrome-scraper.ps1"
    Restart-ChromeIfUnhealthy -Port $MonitorChromePort -Label "Lens"       -Script "start-chrome-monitor.ps1"
    # Postgres/PostgREST are started here (this process stays alive) so they are
    # not reaped when whatever launched them ends. Re-run the idempotent starter
    # if either port has gone quiet.
    if (-not (Test-PortOpen $PgPort) -or -not (Test-PortOpen $PostgrestPort) -or -not (Test-PortOpen $ShimPort)) {
        Write-Host "[run-stack] database stack is down - restarting"
        & "$PSScriptRoot\start-db.ps1"
    }
    # The API and the local scraper were only ever started once by start-all, so a
    # crash took the whole UI down while the DB, the shim and the tunnels stayed
    # up - exactly what happened here: /api/healthz returned 502 through ngrok
    # while /healthz, /rest/v1 and /storage/v1 all answered. Their starters are
    # idempotent, so re-running them brings back just the dead one.
    if (-not (Test-PortOpen $ApiPort) -or -not (Test-PortOpen $ScraperPort)) {
        Write-Host "[run-stack] API ($ApiPort) or scraper ($ScraperPort) is down - restarting"
        & "$PSScriptRoot\start-prelisting-tunnel.ps1"
        & "$PSScriptRoot\start-scraper-tunnel.ps1"
    }
}
