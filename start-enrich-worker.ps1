#Requires -Version 5.1
# start-enrich-worker.ps1 - long-running background enrichment worker.
#
# Scraping never waits for it: finished scrape jobs are queued (detached) and the
# worker classifies + maps attributes at 2 workers, then the export reads the
# cache. Survives restarts because the queue itself is persisted.
. "$PSScriptRoot\windows-common.ps1"

$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like '*_enrich_queue.py*worker*' })
if ($running.Count -gt 0) {
    Write-Host "Enrichment worker already running (pid $($running[0].ProcessId))"
    return
}

Write-Host "Starting enrichment worker (EnrichmentWorkers=$(if ($env:ENRICH_WORKERS) { $env:ENRICH_WORKERS } else { 2 }))"
Start-Bg -FilePath (Get-Python) -Arguments @("_enrich_queue.py", "worker", "--wait") `
    -WorkingDirectory $ApiDir -LogName "enrich-worker.log" | Out-Null
# Below-normal priority so Chrome always wins the CPU while scraping.
Start-Sleep -Seconds 3
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*_enrich_queue.py*worker*' } |
    ForEach-Object {
        try {
            (Get-Process -Id $_.ProcessId).PriorityClass = 'BelowNormal'
            Write-Host "enrichment worker $($_.ProcessId) set to BelowNormal priority"
        } catch { }
    }
