#Requires -Version 5.1
# stop-all.ps1 - stop the local stack (node API, python servers, cloudflared,
# ngrok). Chrome windows are left open so logins/cookies survive a restart.
. "$PSScriptRoot\windows-common.ps1"

$patterns = @(
    # 'index.mjs' is distinctive for the API bundle. A pattern like
    # "dist\\index.mjs" would be a literal double-backslash in PowerShell and
    # never match ".\dist\index.mjs", so the API process survived every
    # restart and kept serving stale code.
    "index.mjs",
    "local_scraper_server.py",
    "_clip_verify_server.py",
    "_analysis_server.py",
    "local_proxy.py"
)

foreach ($pat in $patterns) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$pat*" } |
        ForEach-Object {
            Write-Host "stopping pid $($_.ProcessId) ($pat)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

foreach ($name in @("cloudflared", "ngrok")) {
    Get-Process $name -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "stopping $($_.ProcessName) $($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Local services stopped."
