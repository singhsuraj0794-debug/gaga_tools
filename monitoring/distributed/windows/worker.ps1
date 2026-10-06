<#
.SYNOPSIS
  Plug-and-play load-test worker: headless Chromium (Playwright) sessions for gajab staging.

.DESCRIPTION
  Copy this script to any Windows PC and run it. It will:
    1. Ensure Python 3 is installed (winget, or the official installer as fallback)
    2. Create a virtualenv and install Playwright
    3. Download the Chromium + headless-shell browsers
    4. Run N concurrent browser sessions against the target URL

  It needs its companion runner `concurrency_test.py`. Place it next to this
  script, pass -RunnerPath, or give -RunnerUrl to download it.

  Scale to 100 concurrent by running the same command on several PCs, e.g.
  10 PCs x "-Sessions 10" = 100 concurrent sessions. Watch server load on AWS.

.PARAMETER Sessions
  Number of sessions (tabs) this PC should drive.

.PARAMETER Peak
  Max sessions active at once. Use on low-RAM PCs, e.g. -Sessions 20 -Peak 10.

.PARAMETER Duration
  Keep each session reloading for this many seconds (0 = single load each).

.PARAMETER FullPayload
  Do NOT block images/media/fonts (heavier, more realistic). Default blocks them.

.PARAMETER Engine
  chromium (bundled headless-shell, lightest, default) or chrome (installed Google Chrome).

.PARAMETER AuthStateUrl
  URL of a saved Playwright storage_state (.gajab_session.json) to load for authenticated runs.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\worker.ps1 -Sessions 10 -Duration 120

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\worker.ps1 -Sessions 20 -Peak 10 -Duration 120 `
      -AuthStatePath C:\gajab\.gajab_session.json
#>
[CmdletBinding()]
param(
    [string]$Url = "https://stg.gajab.com/product-list/all?widgetId=13&position=WP4",
    [int]$Sessions = 10,
    [int]$Peak = 0,
    [int]$Duration = 0,
    [int]$ThinkTime = 0,
    [int]$MaxIterations = 1,
    [int]$Timeout = 30000,
    [int]$Rounds = 1,
    [switch]$FullPayload,
    [switch]$Headed,
    [ValidateSet("chromium", "chrome")]
    [string]$Engine = "chromium",
    [string]$AuthStateUrl = "",
    [string]$AuthStatePath = "",
    [string]$AuthDir = "",
    [string]$RunnerPath = "",
    [string]$RunnerUrl = "",
    [string]$WorkDir = "$env:LOCALAPPDATA\gajab-loadtest",
    [string]$PythonInstallerUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe",
    [switch]$Flow,
    [switch]$Staging,
    [string]$ProductFile = "",
    [string]$Products = "",
    [int]$ProductOffset = 0,
    [switch]$Fast,
    [switch]$BargainOnly,
    [switch]$BlockMedia,
    [switch]$Sync,
    [string]$MonitorUrl = "",
    [string]$HostName = "",
    [string]$FlowProduct = "https://gajab.com/product-detail/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914",
    [int]$MinFreeMB = 1500,
    [switch]$NoGuard,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$runnerName = if ($Flow) { "user_flow_test.py" } else { "concurrency_test.py" }
if ($Flow -and -not $PSBoundParameters.ContainsKey("Url")) { $Url = $FlowProduct }

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Resolve-PythonExe {
    $cands = @()
    $c = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($c) { $cands += $c.Source }
    $cands += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    $cands += Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    $cands += Get-ChildItem "C:\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    foreach ($p in $cands) {
        try {
            $v = & $p -c "import sys; print(sys.version_info[:2])" 2>$null
            if ($v -match "3") { return $p }
        } catch { }
    }
    return $null
}

function Install-Python {
    Write-Warn "Python not found - attempting to install..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Warn "Installing Python 3.12 via winget..."
        & winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements --scope user
    }
    if (-not (Resolve-PythonExe)) {
        Write-Warn "winget unavailable/failed - downloading official installer..."
        $installer = Join-Path $env:TEMP "python-setup.exe"
        Invoke-WebRequest -Uri $PythonInstallerUrl -OutFile $installer -UseBasicParsing
        Write-Warn "Running silent Python install..."
        Start-Process -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0" -Wait
    }
}

function Invoke-Pip($venvPy, [string[]]$pipArgs) {
    & $venvPy -m pip @pipArgs
    if ($LASTEXITCODE -ne 0) { throw "pip failed: $($pipArgs -join ' ')" }
}

function Get-AvailMB {
    # '\Memory\Available MBytes' = free + standby (reclaimable). Correct Windows headroom metric.
    try {
        $c = Get-Counter '\Memory\Available MBytes' -ErrorAction Stop
        return [int]$c.CounterSamples[0].CookedValue
    } catch {
        try {
            return [int](((Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).FreePhysicalMemory) / 1024)
        } catch {
            return [int]::MaxValue
        }
    }
}

function Get-TotalMB {
    try {
        return [int](((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory) / 1MB)
    } catch {
        return 0
    }
}

Write-Host "============================================================" -ForegroundColor Magenta
Write-Host " gajab load-test worker" -ForegroundColor Magenta
Write-Host " target : $Url" -ForegroundColor Magenta
Write-Host " runner : $runnerName $(if ($Flow) { '(bargain journey)' } else { '(page load)' })" -ForegroundColor Magenta
Write-Host " pc     : $env:COMPUTERNAME   sessions: $Sessions   peak: $(if ($Peak -gt 0) { $Peak } else { 'unlimited' })" -ForegroundColor Magenta
$totalMB = Get-TotalMB
$freeMB = Get-AvailMB
Write-Host " memory : ${totalMB}MB total, ${freeMB}MB available   guard: $(if ($NoGuard) { 'off' } else { "kill below ${MinFreeMB}MB" })" -ForegroundColor Magenta
$estMB = [int]($Sessions * 600)
Write-Host " est    : ~${estMB}MB needed for $Sessions sessions (rough)" -ForegroundColor Magenta
if ($totalMB -gt 0 -and $estMB -gt ($totalMB - 2000)) {
    Write-Warn "Rough estimate (~${estMB}MB) exceeds this PC's RAM headroom. Use -Peak, fewer sessions, or add RAM."
}
Write-Host "============================================================" -ForegroundColor Magenta

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# 1. Runner script
$runnerDst = Join-Path $WorkDir $runnerName
if ($RunnerPath) {
    Copy-Item $RunnerPath $runnerDst -Force
} elseif (-not (Test-Path $runnerDst)) {
    $beside = Join-Path $PSScriptRoot $runnerName
    $repo = Join-Path $PSScriptRoot "..\..\$runnerName"
    if (Test-Path $beside) {
        Copy-Item $beside $runnerDst -Force
    } elseif (Test-Path $repo) {
        Copy-Item (Resolve-Path $repo) $runnerDst -Force
    } elseif ($RunnerUrl) {
        Invoke-WebRequest -Uri $RunnerUrl -OutFile $runnerDst -UseBasicParsing
    } else {
        throw "$runnerName not found. Put it beside worker.ps1, pass -RunnerPath, or set -RunnerUrl."
    }
}
Write-Ok "runner: $runnerDst"

# 2. Python + venv + Playwright
$venvDir = Join-Path $WorkDir "venv"
$venvPy = Join-Path $venvDir "Scripts\python.exe"

if (-not $SkipInstall) {
    $pyExe = Resolve-PythonExe
    if (-not $pyExe) { Install-Python; $pyExe = Resolve-PythonExe }
    if (-not $pyExe) { throw "Python 3 could not be installed automatically. Install Python 3.12 manually and re-run." }
    Write-Ok "python: $pyExe"

    if (-not (Test-Path $venvPy)) {
        Write-Step "Creating virtualenv"
        & $pyExe -m venv $venvDir
    }
    if (-not (Test-Path $venvPy)) { throw "Failed to create virtualenv at $venvDir" }

    Write-Step "Installing Playwright"
    Invoke-Pip $venvPy @("install", "--upgrade", "pip", "--quiet")
    Invoke-Pip $venvPy @("install", "playwright>=1.48,<2.0", "--quiet")

    Write-Step "Downloading Chromium + headless-shell"
    & $venvPy -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed" }
    Write-Ok "browsers ready"
} else {
    if (-not (Test-Path $venvPy)) { throw "-SkipInstall set but no venv at $venvDir" }
}

# 3. Optional authenticated session / session pool
$authDst = Join-Path $WorkDir ".gajab_session.json"
if ($AuthDir) {
    Write-Ok "auth pool: $AuthDir"
} elseif ($AuthStateUrl) {
    Write-Step "Fetching auth session"
    Invoke-WebRequest -Uri $AuthStateUrl -OutFile $authDst -UseBasicParsing
} elseif ($AuthStatePath) {
    Write-Step "Copying auth session"
    Copy-Item $AuthStatePath $authDst -Force
}
$useAuth = (Test-Path $authDst) -or [bool]$AuthDir
Write-Ok ("auth: " + $(if ($AuthDir) { $AuthDir } elseif ($useAuth) { $authDst } else { "no (guest)" }))

# 4. Build runner args
$runnerArgs = @("--url", $Url, "--sessions", "$Sessions", "--tabs", "--timeout", "$Timeout")
if (-not $FullPayload) { $runnerArgs += "--block-heavy" }
if ($Engine -eq "chrome") { $runnerArgs += "--chrome" }
if ($Flow -and $Staging) { $runnerArgs += "--staging" }
if ($Flow -and $ProductFile) { $runnerArgs += @("--product-file", $ProductFile) }
if ($Flow -and $Products) { $runnerArgs += @("--products", $Products) }
if ($Flow -and $ProductOffset -gt 0) { $runnerArgs += @("--product-offset", "$ProductOffset") }
if ($Flow -and $Fast) { $runnerArgs += "--fast" }
if ($Flow -and $BargainOnly) { $runnerArgs += "--pdp-bargain" }
if ($Flow -and $BlockMedia) { $runnerArgs += "--block-media" }
if ($Flow -and $Sync) { $runnerArgs += "--sync" }
if ($MonitorUrl) { $runnerArgs += @("--monitor-url", $MonitorUrl) }
if ($HostName) { $runnerArgs += @("--host-name", $HostName) }
if ($Headed) { $runnerArgs += "--headed" }
if ($Peak -gt 0) { $runnerArgs += @("--peak", "$Peak") }
if ($Duration -gt 0) {
    if (-not $PSBoundParameters.ContainsKey("MaxIterations")) { $MaxIterations = 0 }
    $runnerArgs += @("--duration", "$Duration")
}
$runnerArgs += @("--max-iterations", "$MaxIterations")
if ($ThinkTime -gt 0) { $runnerArgs += @("--think-time", "$ThinkTime") }
if ($AuthDir) {
    $runnerArgs += @("--auth", "--auth-dir", $AuthDir)
} elseif ($useAuth) {
    $runnerArgs += @("--auth", "--auth-state", $authDst)
}

# 5. Run (with RAM guard)
function Start-Runner {
    $quoted = $runnerArgs | ForEach-Object { if ("$_" -match '\s') { '"' + $_ + '"' } else { "$_" } }
    $argLine = ('"' + $runnerDst + '" ' + ($quoted -join ' '))
    $proc = Start-Process -FilePath $venvPy -ArgumentList $argLine -NoNewWindow -PassThru
    if ($NoGuard) {
        $proc.WaitForExit()
        return $proc.ExitCode
    }
    $killed = $false
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $lastReport = -999
    while ($true) {
        $proc.Refresh()
        if ($proc.HasExited) { break }
        $avail = Get-AvailMB
        $elapsed = [int]$sw.Elapsed.TotalSeconds
        if (($elapsed - $lastReport) -ge 10) {
            $lastReport = $elapsed
            Write-Host ("    [{0:mm\:ss}] available {1}MB" -f $sw.Elapsed, $avail) -ForegroundColor DarkGray
        }
        if ($avail -lt $MinFreeMB) {
            Write-Warn "memory guard: available ${avail}MB < ${MinFreeMB}MB - killing session tree"
            & taskkill /PID $proc.Id /T /F | Out-Null
            $killed = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if ($killed) { return 1 }
    return $proc.ExitCode
}

for ($r = 1; $r -le $Rounds; $r++) {
    if ($Rounds -gt 1) { Write-Host "`n===== Round $r / $Rounds =====" -ForegroundColor Magenta }
    Write-Step "Starting $Sessions sessions on $env:COMPUTERNAME"
    $code = Start-Runner
    Write-Ok "round $r finished (exit $code)"
}

Write-Host "`nDone. Watch server load from AWS to see the aggregate effect." -ForegroundColor Magenta
