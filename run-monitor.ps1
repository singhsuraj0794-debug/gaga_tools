#Requires -Version 5.1
# run-monitor.ps1 - run the synthetic monitor on the Windows box.
#
# The GitHub workflow fell back to a US runner for Lighthouse (the gajab-windows
# runner was never registered), and measuring an Indian site from the US inflated
# LCP into false failures (home 3001ms, category 2960/3756ms vs a 2500ms
# threshold). Run here, the same pages measure 1456 / 879 / 1225ms.
#
# This runs the health + API + happy-flow + Lighthouse checks against the LOCAL
# database. The Android flow is separate (start-native-monitor.ps1) because it
# needs an emulator boot.
#
# Usage:  powershell -File run-monitor.ps1 [health|browser|lighthouse|full]
param(
    [string]$Mode = "full"
)
. "$PSScriptRoot\windows-common.ps1"

$MON = Join-Path $PSScriptRoot "monitoring"
$PY = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "C:\gajab\venvs\api\Scripts\python.exe" }

if (-not (Test-Path (Join-Path $MON "run_monitor.py"))) {
    Write-Warning "[monitor] $MON\run_monitor.py not found"
    exit 1
}
# The database must be up or every result is written nowhere.
if (-not (Test-PortOpen $ShimPort)) {
    Write-Warning "[monitor] storage shim (:3001) is down - results would be lost"
}

Set-Location $MON
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:MONITOR_MODE = $Mode
if (-not $env:CHROME_PATH) {
    $chrome = Get-ChromePath
    if ($chrome) { $env:CHROME_PATH = $chrome }
}

Write-Host "[monitor] mode=$Mode  python=$PY"
& $PY "run_monitor.py"
$code = $LASTEXITCODE
Write-Host "[monitor] exit code: $code"
exit $code
