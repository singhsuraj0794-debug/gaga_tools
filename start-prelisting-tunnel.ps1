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
    if (-not $env:CLIP_VERIFY_WORKERS) { $env:CLIP_VERIFY_WORKERS = "6" }
    Write-Host "Starting CLIP verify server on $ClipPort ..."
    Start-Bg -FilePath $python -Arguments @("_clip_verify_server.py") -WorkingDirectory $ApiDir -LogName "clip-verify.log" | Out-Null
    Start-Sleep -Seconds 2
}

if (Test-PortOpen $AnalysisPort) {
    Write-Host "Analysis server already running on $AnalysisPort"
} else {
    $env:ANALYSIS_PORT = "$AnalysisPort"
    # Products run concurrently. Text correction no longer touches Marqo or the
    # CLIP taxonomy (removed), so the shared non-reentrant state that deadlocked
    # threaded runs is gone; only SigLIP (thread-safe forwards) + embeddings remain.
    if (-not $env:ANALYSIS_WORKERS) { $env:ANALYSIS_WORKERS = "3" }
    Write-Host "Starting analysis server on $AnalysisPort ..."
    Start-Bg -FilePath $python -Arguments @("_analysis_server.py") -WorkingDirectory $ApiDir -LogName "analysis.log" | Out-Null
    Start-Sleep -Seconds 2
}

# Embedding server (standalone llama.cpp) — EmbeddingGemma for HSN/claim text
# embeddings. The pip llama-cpp-python build is too old to load this model, but
# the winget llama.cpp does. Optional: skipped if llama-server isn't on PATH
# (the analysis server then falls back to MiniLM).
$LlamaPort = 8081
$llama = Get-Command llama-server -ErrorAction SilentlyContinue
if (-not $llama) {
    # The Scheduled Task's PATH may not include the winget shim dir; locate the
    # binary under the WinGet packages folder.
    $exe = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($exe) { $llama = @{ Source = $exe.FullName } }
}
if ($llama) {
    $llamaExe = if ($llama.Source) { $llama.Source } else { $llama.Path }
    if (Test-PortOpen $LlamaPort) {
        Write-Host "Embedding server already running on $LlamaPort"
    } else {
        Write-Host "Starting llama.cpp embedding server on $LlamaPort ..."
        Start-Bg -FilePath $llamaExe -Arguments @(
            "-hf", "unsloth/embeddinggemma-2-GGUF:UD-Q4_K_XL",
            "--embedding", "-ngl", "99", "--port", "$LlamaPort", "--host", "127.0.0.1"
        ) -WorkingDirectory $ApiDir -LogName "llama-embed.log" | Out-Null
        # Wait until the embedding server answers /health before the analysis
        # server makes embedding calls (else the first call would fall back to
        # MiniLM and cache mismatched vectors).
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Seconds 2
            try {
                $h = Invoke-WebRequest -Uri "http://127.0.0.1:$LlamaPort/health" -TimeoutSec 3 -UseBasicParsing
                if ($h.StatusCode -eq 200) { Write-Host "Embedding server ready"; break }
            } catch { }
        }
    }
} else {
    Write-Warning "llama-server not found - HSN/claim embeddings fall back to MiniLM"
}

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Warning "cloudflared not on PATH - skipping compute tunnel (install it first)"
    return
}

$log = Join-Path $env:TEMP "cloudflared-prelisting.log"
Remove-Item $log, "$log.err" -ErrorAction SilentlyContinue

# Single instance. A quick tunnel is cheap to restart and its URL changes anyway,
# so kill any existing cloudflared first - otherwise every watchdog restart
# stacked another tunnel (two were running, one from the previous day, each
# publishing its own URL that then accumulates in storage).
Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  stopping existing cloudflared $($_.Id)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

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

    # Keep only the newest few published URLs. Every tunnel restart adds one, and
    # 194 had accumulated; only the newest is ever read.
    $dir = Join-Path $DbRoot "storage\monitoring"
    Get-ChildItem $dir -Filter "prelisting-api-url-*.txt" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 5 |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}
