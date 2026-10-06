#Requires -Version 5.1
# start-prelisting-tunnel.ps1 - local compute stack for the Pre-Listing
# Validator on Windows:
#   API          :8090  (node dist/index.mjs)
#   CLIP verify  :8001  (_clip_verify_server.py)
#   analysis     :8003  (_analysis_server.py - HSN + Qwen + Marqo)
#   cloudflared quick tunnel -> the API, URL published to Supabase so the
#   deployed validator can auto-discover it.
. "$PSScriptRoot\windows-common.ps1"
Import-DotEnv

$python = Get-Python

if (Test-PortOpen $ApiPort) {
    Write-Host "API already running on $ApiPort"
} else {
    $env:PORT = "$ApiPort"
    Write-Host "Starting API on $ApiPort ..."
    Start-Bg -FilePath "node" -Arguments @("--enable-source-maps", ".\dist\index.mjs") -WorkingDirectory $ApiDir -LogName "prelisting-api.log" | Out-Null
    Start-Sleep -Seconds 3
}

if (Test-PortOpen $ClipPort) {
    Write-Host "CLIP verify already running on $ClipPort"
} else {
    $env:CLIP_VERIFY_PORT = "$ClipPort"
    Write-Host "Starting CLIP verify server on $ClipPort ..."
    Start-Bg -FilePath $python -Arguments @("_clip_verify_server.py") -WorkingDirectory $ApiDir -LogName "clip-verify.log" | Out-Null
    Start-Sleep -Seconds 2
}

if (Test-PortOpen $AnalysisPort) {
    Write-Host "Analysis server already running on $AnalysisPort"
} else {
    $env:ANALYSIS_PORT = "$AnalysisPort"
    if (-not $env:ANALYSIS_WORKERS) { $env:ANALYSIS_WORKERS = "1" }
    Write-Host "Starting analysis server on $AnalysisPort ..."
    Start-Bg -FilePath $python -Arguments @("_analysis_server.py") -WorkingDirectory $ApiDir -LogName "analysis.log" | Out-Null
    Start-Sleep -Seconds 2
}

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Warning "cloudflared not on PATH - skipping compute tunnel (install it first)"
    return
}

$log = Join-Path $env:TEMP "cloudflared-prelisting.log"
Remove-Item $log, "$log.err" -ErrorAction SilentlyContinue
Write-Host "Starting cloudflared tunnel -> http://127.0.0.1:$ApiPort"
Start-Process -FilePath $cloudflared.Source `
    -ArgumentList @("tunnel", "--url", "http://127.0.0.1:$ApiPort", "--no-autoupdate") `
    -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err" | Out-Null

$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    foreach ($f in @($log, "$log.err")) {
        if (Test-Path $f) {
            $m = Select-String -Path $f -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($m) { $url = $m.Matches[0].Value; break }
        }
    }
}

if (-not $url) {
    Write-Warning "cloudflared URL not found yet - check $log"
    return
}

Set-Content -Path (Join-Path $env:TEMP "prelisting-tunnel-url.txt") -Value $url -Encoding ascii
Write-Host "Compute API (tunnel): $url"

if ($env:SUPABASE_URL -and $env:SUPABASE_KEY) {
    try {
        $ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $remote = "prelisting-api-url-$ts.txt"
        Invoke-RestMethod -Method Put -Uri "$($env:SUPABASE_URL)/storage/v1/object/monitoring/$remote" `
            -Headers @{ apikey = $env:SUPABASE_KEY; Authorization = "Bearer $($env:SUPABASE_KEY)" } `
            -ContentType "text/plain" -Body $url | Out-Null
        Write-Host "Published to Supabase ($remote)"
    } catch {
        Write-Warning "Supabase publish failed: $_"
    }
}
