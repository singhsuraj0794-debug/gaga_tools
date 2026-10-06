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
