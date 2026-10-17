#Requires -Version 5.1
# windows-common.ps1 - shared config + helpers for the Gajab local stack on Windows.
#
# Dot-source this from every launcher:
#   . "$PSScriptRoot\windows-common.ps1"
#
# Paths can be overridden with environment variables before startup:
#   GAJAB_REPO_DIR, GAJAB_PYTHON, GAJAB_NGROK, GAJAB_CHROME

$ErrorActionPreference = "Stop"

# --- Layout ------------------------------------------------------------------
$RepoDir = if ($env:GAJAB_REPO_DIR) { $env:GAJAB_REPO_DIR } else { $PSScriptRoot }
$ApiDir  = Join-Path $RepoDir "artifacts\api-server"
$EnvFile = Join-Path $ApiDir ".env"

# --- Ports -------------------------------------------------------------------
$ScraperPort      = 9000
$ApiPort          = 8090
$ClipPort         = 8001
$AnalysisPort     = 8003
$ChromeScraperPort = 9223   # product scraping, via the Webshare proxy
$LocalProxyPort    = 9224   # local auth-forwarding proxy for Webshare
$ChromeExtractPort = 9225   # link extraction, no proxy
$MonitorChromePort = 9222   # Google Lens reverse search (Price Mapper), no proxy
$AppiumPort        = 4723

# --- Tunnels -----------------------------------------------------------------
$NgrokDomain = if ($env:GAJAB_NGROK_DOMAIN) { $env:GAJAB_NGROK_DOMAIN } else { "headphone-shudder-lavender.ngrok-free.dev" }

# --- Helpers -----------------------------------------------------------------

# Load artifacts/api-server/.env into the current process environment.
function Import-DotEnv {
    param([string]$Path = $EnvFile)
    if (-not (Test-Path $Path)) { Write-Warning "No .env at $Path"; return }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $i = $line.IndexOf("=")
        if ($i -lt 1) { return }
        $k = $line.Substring(0, $i).Trim()
        $v = $line.Substring($i + 1).Trim()
        if ($v.Length -ge 2) {
            $a = $v[0]; $b = $v[$v.Length - 1]
            if (($a -eq '"' -and $b -eq '"') -or ($a -eq "'" -and $b -eq "'")) {
                $v = $v.Substring(1, $v.Length - 2)
            }
        }
        [Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
}

# True if a TCP port is accepting connections locally.
function Test-PortOpen {
    param([int]$Port, [string]$Target = "127.0.0.1")
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($Target, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(1500)) { $c.Close(); return $false }
        $c.EndConnect($iar); $c.Close(); return $true
    } catch { return $false }
}

# PIDs of the Chrome instances launched with a given remote-debugging port.
# Matched on the command line because the port is the only thing that
# distinguishes the three profiles.
function Get-ChromePidOnPort {
    param([int]$Port)
    $pat = "*--remote-debugging-port=$Port*"
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "chrome.exe" -and $_.CommandLine -like $pat } |
        Select-Object -ExpandProperty ProcessId)
}

# The user-data-dir a CDP Chrome was launched with (start-chrome-*.ps1 name them
# chrome-<kind>-profile-<port> under TEMP).
function Get-ChromeProfileDir {
    param([int]$Port)
    $hit = Get-ChildItem $env:TEMP -Directory -Filter "*profile-$Port" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

# Delete a profile's registered service workers.
#
# amazon.in registers one, and Playwright's driver asserts on that target the next
# time it attaches (_onAttachedToTarget), killing the driver process: the CDP port
# keeps answering HTTP - so a port check calls it healthy - but every extraction
# then fails with "Connection closed while reading from the driver". Clearing them
# on restart makes a restart an actual recovery rather than a no-op.
function Clear-ChromeServiceWorkers {
    param([int]$Port)
    $dir = Get-ChromeProfileDir -Port $Port
    if (-not $dir -or -not (Test-Path $dir)) { return }
    Get-ChildItem $dir -Recurse -Directory -Filter "Service Worker" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

# Kill the Chrome on a port and wait for the port to actually close, so the
# caller can start a fresh one (start-chrome-*.ps1 refuses to start when the
# port is still open).
function Stop-ChromeOnPort {
    param([int]$Port)
    foreach ($procId in Get-ChromePidOnPort -Port $Port) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (-not (Test-PortOpen $Port)) { break }
    }
    Clear-ChromeServiceWorkers -Port $Port
    return $true
}

# Deep health check for a CDP Chrome. A wedged browser still accepts TCP and
# still answers /json/version, but Playwright cannot attach to it - and that is
# exactly how it fails for the scrapers ("BrowserType.connect_over_cdp:
# Connection closed while reading from the driver"). So ask the venv's Playwright
# to attach, which is the real contract. One process per port so a driver crash
# on a bad browser cannot poison the verdict for the healthy ones.
function Test-CdpHealthy {
    param([int]$Port)
    $probe = Join-Path $PSScriptRoot "check-cdp.py"
    if (-not (Test-Path $probe)) { return $true }
    try {
        $out = & (Get-Python) $probe $Port 2>$null
    } catch { return $true }
    $verdict = "$out".Trim()
    if ($verdict -eq "ok") { return $true }
    if ($verdict -eq "fail") { return $false }
    return $true   # unknown (python failed) -> leave the browser alone
}

# Resolve the Python interpreter for the local ML stack.
# Prefers a dedicated venv, then GAJAB_PYTHON, then the py launcher.
function Get-Python {
    $venv = if ($env:GAJAB_VENV) { $env:GAJAB_VENV } else { "C:\gajab\venvs\api" }
    $venvPy = Join-Path $venv "Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    if ($env:GAJAB_PYTHON -and (Test-Path $env:GAJAB_PYTHON)) { return $env:GAJAB_PYTHON }
    return "python"
}

# Resolve the Google Chrome executable.
function Get-ChromePath {
    if ($env:GAJAB_CHROME -and (Test-Path $env:GAJAB_CHROME)) { return $env:GAJAB_CHROME }
    $candidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    throw "Google Chrome not found. Set GAJAB_CHROME to chrome.exe."
}

# Resolve the ngrok executable.
function Get-NgrokPath {
    if ($env:GAJAB_NGROK -and (Test-Path $env:GAJAB_NGROK)) { return $env:GAJAB_NGROK }
    $p = "C:\gajab\bin\ngrok.exe"
    if (Test-Path $p) { return $p }
    $cmd = Get-Command ngrok -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "ngrok not found. Install it or set GAJAB_NGROK."
}

# Start a detached background process, logging stdout+stderr to a file.
function Start-Bg {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $RepoDir,
        [string]$LogName = "gajab-bg.log"
    )
    $log = Join-Path $env:TEMP $LogName
    Start-Process -FilePath $FilePath -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" | Out-Null
    return $log
}

Write-Host "[common] repo=$RepoDir python=$(Get-Python)"

# The API spawns python-backed scripts. On Windows a bare "python3" resolves to
# the Microsoft Store shim, which has none of our packages (it silently
# returned empty results), so point it at the venv interpreter explicitly.
$env:PYTHON_BIN = Get-Python

# Python's stdio defaults to the Windows locale encoding (cp1252), so a script
# printing product data that contains a non-ASCII character (a '?' in a
# description, an em dash) crashed with UnicodeEncodeError - that broke the
# Gajab export. Force UTF-8 for every python subprocess we spawn.
$env:PYTHONIOENCODING = "utf-8"

# PYTHONIOENCODING only covers stdio. open() uses the locale encoding too, and
# several detectors read JSON the API wrote as UTF-8 - that produced
#   UnicodeDecodeError: 'charmap' codec can't decode byte 0x8d
# (the duplicate check failed with "detector exited with code 1"). UTF-8 mode
# makes open() default to UTF-8 everywhere.
$env:PYTHONUTF8 = "1"

# The API's scraper routes branch on this: when it is unset, hasLocalScraper()
# is false and Meesho store extraction falls back to a SINGLE python subprocess
# that walks the whole store (MAX_EXTRACT_PAGES=200, 2.5s sleep/page) and gets
# killed by callPython's 900s timeout -> job.status="failed" -> "Extraction
# failed". Pointing at the threaded local server switches on the paginated
# path instead (per-page chunks with their own error handling). Loopback, so
# there is no ngrok 60s cap here.
if (-not $env:LOCAL_SCRAPER_URL) { $env:LOCAL_SCRAPER_URL = "http://localhost:9000" }

# Scraper parallelism. The local server is threaded and the browsers accept
# several concurrent CDP sessions; 6 mixed-platform workers ran cleanly here.
# Raised to 8 now that the CDP tab leak is fixed (_try_playwright closes its tab
# in a finally) - a 4k-product Meesho store is hours of wall-clock at 6. Dial
# back to 6 if the shared scrape Chrome starts contending.
if (-not $env:SCRAPE_CONCURRENCY) { $env:SCRAPE_CONCURRENCY = "8" }
if (-not $env:SCRAPE_BATCH_DELAY_MS) { $env:SCRAPE_BATCH_DELAY_MS = "300" }

# Price Mapper bulk mapping. Each product runs a Lens reverse search plus three
# platform searches (the platform three now run in parallel in the searcher).
# Raised from 3 to 6 for this box (12 cores / 64GB); the searcher's own
# SEARCH_PLATFORM_WORKERS=12 caps total concurrent Chromium instances, so
# products queue rather than oversubscribe. Dial back if Lens starts returning
# thin results (concurrent Lens pages get fewer URLs from Google).
if (-not $env:PRICE_MAPPER_CONCURRENCY) { $env:PRICE_MAPPER_CONCURRENCY = "6" }
if (-not $env:SEARCH_PLATFORM_WORKERS) { $env:SEARCH_PLATFORM_WORKERS = "12" }
# Concurrent Google Lens pages (searcher's own pool). Lens is the per-product
# floor at ~20s. 4 at once is ~4x faster but Google returns far fewer URLs
# (32 -> 9 across 4 images), which starves the platform search - so 2 balances
# speed and recall. Raise only if the match rate holds.
if (-not $env:LENS_CONCURRENCY) { $env:LENS_CONCURRENCY = "2" }

# ?? Model upgrades for the RTX 5060 Ti (17GB VRAM) ??????????????????????????
# Vision model for the image rules (overlay/moderation). The thresholds are
# calibrated for CLIP (a "BESTSELLER" badge scores 0.82, clean shots 0.53-0.75).
# SigLIP variants use a different score distribution and over-flag with these
# cutoffs, so CLIP-base is the default. Swap to openai/clip-vit-large-patch14-336
# for more accuracy at ~4x the cost.
if (-not $env:CLIP_MODEL) { $env:CLIP_MODEL = "openai/clip-vit-base-patch32" }
# VL model. Qwen2.5-VL-7B (4-bit, ~6GB) is the fastest good option here.
# Qwen3-VL-8B is newer/stronger but ~3.5x slower on this GPU (measured), so it is
# NOT the default - enable it with: $env:QWEN_MODEL="Qwen/Qwen3-VL-8B-Instruct"
# (needs transformers >= 4.57; Qwen3-VL-4B is the likely faster sweet spot).
if (-not $env:QWEN_MODEL) { $env:QWEN_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct" }
if (-not $env:QWEN_LOAD_IN_4BIT) { $env:QWEN_LOAD_IN_4BIT = "1" }
# Shared cross-process product-image disk cache.
if (-not $env:GAJAB_IMG_CACHE) { $env:GAJAB_IMG_CACHE = Join-Path $env:USERPROFILE ".cache\gajab_images" }

# Text embeddings: EmbeddingGemma via the batched llama.cpp server (768-d,
# stronger than MiniLM). Falls back to MiniLM if the server is down.
if (-not $env:EMBED_BACKEND) { $env:EMBED_BACKEND = "llama" }
if (-not $env:EMBED_LLAMA_URL) { $env:EMBED_LLAMA_URL = "http://127.0.0.1:8081" }
