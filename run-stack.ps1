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

# Self-heal the Chromes. They are the fragile part — they crash under load — and
# start-all only launches them at boot, so a dead CDP port silently turned every
# extract/scrape into "Chrome CDP not available" (which surfaced as a job
# completing with 0 products). Re-check every minute and bring them back.
while ($true) {
    Start-Sleep -Seconds 60
    if (-not (Test-PortOpen $ChromeExtractPort)) {
        Write-Host "[run-stack] extraction Chrome ($ChromeExtractPort) is down — restarting"
        & "$PSScriptRoot\start-chrome-extract.ps1"
    }
    if (-not (Test-PortOpen $ChromeScraperPort)) {
        Write-Host "[run-stack] scraping Chrome ($ChromeScraperPort) is down — restarting"
        & "$PSScriptRoot\start-chrome-scraper.ps1"
    }
}
