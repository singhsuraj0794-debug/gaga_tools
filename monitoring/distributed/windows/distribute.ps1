<#
.SYNOPSIS
  Roll out the gajab load-test worker to many Windows PCs and start them together.

.DESCRIPTION
  Copies the worker bundle (worker.ps1 + runners) and an optional session pool to
  each target PC over PowerShell Remoting (WinRM), then starts the worker on every
  machine in parallel — so e.g. 10 PCs x "-Sessions 10" = 100 concurrent users.

  Prerequisites on each target PC:
    - Windows PowerShell Remoting (WinRM) enabled:  Enable-PSRemoting -Force
    - File/Printer sharing + C$ admin share reachable, OR use -UseAdminShare
    - Your account (or -Credential) has admin rights on the target

.PARAMETER Computers
  One or more target hostnames/IPs.

.PARAMETER ComputerFile
  A text file with one hostname per line (# comments allowed).

.PARAMETER Credential
  Optional credential for the remote machines.

.PARAMETER SessionsDir
  Local folder of session JSON files (from setup_login_pool.py). Copied to each PC.

.PARAMETER CopyOnly
  Copy files but do not start the run.

.EXAMPLE
  .\distribute.ps1 -ComputerFile .\pcs.txt -SessionsDir .\sessions -Flow -Sessions 10 -Duration 300 -ThinkTime 3000

.EXAMPLE
  .\distribute.ps1 -Computers PC1,PC2,PC3 -Credential (Get-Credential) -Flow -Staging -Sessions 8 -Duration 300
#>
[CmdletBinding()]
param(
    [string[]]$Computers,
    [string]$ComputerFile,
    [System.Management.Automation.PSCredential]$Credential,
    [string]$RemoteDir = "C:\gajab-loadtest",
    [string]$SessionsDir = "",
    [int]$Sessions = 10,
    [int]$Duration = 300,
    [int]$ThinkTime = 3000,
    [int]$Peak = 0,
    [switch]$Flow,
    [switch]$Staging,
    [switch]$Fast,
    [switch]$BargainOnly,
    [switch]$BlockMedia,
    [switch]$Sync,
    [switch]$FullPayload,
    [ValidateSet("chromium", "chrome")]
    [string]$Engine = "chromium",
    [int]$MinFreeMB = 1500,
    [switch]$SkipInstall,
    [switch]$Headed,
    [switch]$CopyOnly,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$SourceDir = $PSScriptRoot
$WorkerFiles = @("worker.ps1", "user_flow_test.py", "concurrency_test.py", "run-worker.cmd", "products.txt")

function Write-Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Write-Warn2($m) { Write-Host "    $m" -ForegroundColor Yellow }

# --- resolve targets ---
if ($ComputerFile) {
    if (-not (Test-Path $ComputerFile)) { throw "computer file not found: $ComputerFile" }
    $Computers = @(Get-Content $ComputerFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") })
}
if (-not $Computers -or $Computers.Count -eq 0) { throw "no targets. Use -Computers or -ComputerFile" }

$copySessions = ($SessionsDir -and (Test-Path $SessionsDir))
if ($SessionsDir -and -not $copySessions) { Write-Warn2 "sessions dir not found: $SessionsDir (running guest)" }

# --- build the remote worker argument line ---
$w = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$RemoteDir\worker.ps1`"")
$w += @("-Sessions", "$Sessions", "-Duration", "$Duration", "-ThinkTime", "$ThinkTime", "-Timeout", "60000")
if ($Flow) { $w += "-Flow" }
if ($Staging) { $w += "-Staging" }
if ($Fast) { $w += "-Fast" }
if ($BargainOnly) { $w += "-BargainOnly" }
if ($BlockMedia) { $w += "-BlockMedia" }
if ($Sync) { $w += "-Sync" }
if ($FullPayload) { $w += "-FullPayload" }
if ($Peak -gt 0) { $w += @("-Peak", "$Peak") }
if ($Headed) { $w += "-Headed" }
if ($Engine -ne "chromium") { $w += @("-Engine", $Engine) }
if ($MinFreeMB -gt 0) { $w += @("-MinFreeMB", "$MinFreeMB") }
if ($SkipInstall) { $w += "-SkipInstall" }
$hasProducts = ($Flow -and (Test-Path (Join-Path $SourceDir "products.txt")))
if ($hasProducts) { $w += @("-ProductFile", "`"$RemoteDir\products.txt`"") }
$workerArgs = $w -join " "

Write-Host "============================================================" -ForegroundColor Magenta
Write-Host " gajab load-test rollout" -ForegroundColor Magenta
Write-Host " targets : $($Computers.Count)  ($($Computers -join ', '))" -ForegroundColor Magenta
Write-Host " remote  : $RemoteDir" -ForegroundColor Magenta
Write-Host " mode    : $(if ($Flow) { 'bargain journey' } else { 'page load' }) $(if ($Staging) { 'on STAGING' })" -ForegroundColor Magenta
Write-Host " sessions: $Sessions per PC   duration: ${Duration}s" -ForegroundColor Magenta
Write-Host " pool    : $(if ($copySessions) { $SessionsDir } else { 'none (guest)' })" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta

$psSessions = @()
$fails = @()

foreach ($pc in $Computers) {
    Write-Step "$pc : connecting"
    try {
        $pssArgs = @{ ComputerName = $pc; ErrorAction = "Stop" }
        if ($Credential) { $pssArgs["Credential"] = $Credential }
        $s = New-PSSession @pssArgs
        $psSessions += $s

        Invoke-Command -Session $s -ScriptBlock {
            param($dir)
            if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
            if (-not (Test-Path "$dir\sessions")) { New-Item -ItemType Directory -Force -Path "$dir\sessions" | Out-Null }
        } -ArgumentList $RemoteDir

        foreach ($f in $WorkerFiles) {
            $src = Join-Path $SourceDir $f
            if (Test-Path $src) {
                Copy-Item -Path $src -Destination "$RemoteDir\" -ToSession $s -Force
            }
        }
        if ($copySessions) {
            Copy-Item -Path (Join-Path $SessionsDir "*.json") -Destination "$RemoteDir\sessions\" -ToSession $s -Force
        }
        Write-Ok "$pc : bundle copied"
    } catch {
        Write-Warn2 "$pc : FAILED - $($_.Exception.Message)"
        $fails += $pc
    }
}

if ($CopyOnly -or $NoLaunch) {
    Write-Host "`nCopied to $($psSessions.Count) PC(s). (not launching)" -ForegroundColor Magenta
    foreach ($s in $psSessions) { Remove-PSSession $s -ErrorAction SilentlyContinue }
    return
}

# --- launch in parallel on every PC ---
Write-Step "launching on all PCs ..."
$jobs = @()
$hi = 0
foreach ($s in $psSessions) {
    $pc = ($s.ComputerName)
    $hostArgs = $workerArgs
    if ($hasProducts) { $hostArgs += " -ProductOffset $($hi * 5)" }
    $jobs += Invoke-Command -Session $s -ScriptBlock {
        param($RemoteDir, $hostArgs)
        $log = Join-Path $RemoteDir "worker.log"
        $err = Join-Path $RemoteDir "worker.err"
        Start-Process -FilePath "powershell.exe" -ArgumentList $hostArgs -WorkingDirectory $RemoteDir `
            -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden
        return $env:COMPUTERNAME
    } -ArgumentList $RemoteDir, $hostArgs -AsJob -JobName $pc
    $hi++
}

Start-Sleep -Seconds 5
$started = @($jobs | ForEach-Object { $_.Name })
Write-Ok "started on: $($started -join ', ')"

Write-Host "`n============================================================" -ForegroundColor Magenta
Write-Host " Rollout complete" -ForegroundColor Magenta
Write-Host " started : $($started.Count) PC(s)" -ForegroundColor Magenta
if ($fails.Count) { Write-Host " failed  : $($fails -join ', ')" -ForegroundColor Red }
Write-Host ""
Write-Host " Each PC logs to $RemoteDir\worker.log" -ForegroundColor Magenta
Write-Host " Tail all: Invoke-Command -ComputerName $($Computers -join ',') { Get-Content C:\gajab-loadtest\worker.log -Tail 5 }" -ForegroundColor Magenta
Write-Host " Stop all: Invoke-Command -ComputerName $($Computers -join ',') { Get-Process python,powershell -ErrorAction SilentlyContinue | Stop-Process -Force }" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta

foreach ($s in $psSessions) { Remove-PSSession $s -ErrorAction SilentlyContinue }
