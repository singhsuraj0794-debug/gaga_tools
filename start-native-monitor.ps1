#Requires -Version 5.1
# start-native-monitor.ps1 - Windows equivalent of start-native-monitor.sh:
# boot the gajab_pixel7 emulator, start Appium, run the Android happy flow.
#
# The bash version hardcodes /opt/homebrew/share/android-commandlinetools; here the
# SDK is at %LOCALAPPDATA%\Android\Sdk and adb/emulator live under it. WHPX
# acceleration is installed on this box (verified: emulator-5554 boots in ~30s).
. "$PSScriptRoot\windows-common.ps1"

$SDK = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA "Android\Sdk" }
$ADB = Join-Path $SDK "platform-tools\adb.exe"
$EMU = Join-Path $SDK "emulator\emulator.exe"
$AVD = if ($env:GAJAB_AVD) { $env:GAJAB_AVD } else { "gajab_pixel7" }
$MON = Join-Path $PSScriptRoot "monitoring"

if (-not (Test-Path $ADB) -or -not (Test-Path $EMU)) {
    Write-Warning "[native] Android SDK not found under $SDK"
    exit 1
}

# --- emulator ----------------------------------------------------------------
$running = (& $ADB devices) -match "emulator-\d+\s+device"
if (-not $running) {
    Write-Host "[native] starting emulator $AVD"
    Start-Process -FilePath $EMU -ArgumentList @(
        "-avd", $AVD, "-no-snapshot-load", "-no-audio", "-no-boot-anim"
    ) -WindowStyle Minimized | Out-Null
} else {
    Write-Host "[native] emulator already running"
}

Write-Host "[native] waiting for device to boot"
$booted = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 3
    $boot = (& $ADB shell getprop sys.boot_completed 2>$null | Out-String).Trim()
    if ($boot -eq "1") { $booted = $true; break }
}
if (-not $booted) {
    Write-Warning "[native] emulator did not finish booting"
    exit 1
}
Write-Host "[native] device booted"

# Disable animations. Without this the app's UI thread never goes idle and
# UIAutomator cannot read the accessibility tree, which made EVERY element lookup
# time out (steps took 20-105s and all failed).
foreach ($s in "window_animation_scale", "transition_animation_scale", "animator_duration_scale") {
    & $ADB shell settings put global $s 0 2>$null | Out-Null
}
Write-Host "[native] animations disabled"

$app = (& $ADB shell pm list packages 2>$null | Out-String)
if ($app -notmatch "com.gajab.buyerstore") {
    Write-Warning "[native] com.gajab.buyerstore is NOT installed on $AVD - the flow will fail at launch"
} else {
    Write-Host "[native] app com.gajab.buyerstore present"
}

# --- Appium ------------------------------------------------------------------
$appiumUp = $false
try {
    $r = Invoke-WebRequest -Uri "http://localhost:4723/status" -TimeoutSec 5 -UseBasicParsing
    $appiumUp = $r.StatusCode -eq 200
} catch { $appiumUp = $false }

if (-not $appiumUp) {
    Write-Host "[native] starting Appium on 4723"
    $appium = (Get-Command appium -ErrorAction SilentlyContinue).Source
    if (-not $appium) { $appium = Join-Path $env:APPDATA "npm\appium.cmd" }
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "`"$appium`" --port 4723 > `"$DbRoot\appium.log`" 2>&1") -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 2
        try {
            if ((Invoke-WebRequest -Uri "http://localhost:4723/status" -TimeoutSec 5 -UseBasicParsing).StatusCode -eq 200) { $appiumUp = $true; break }
        } catch { }
    }
}
if (-not $appiumUp) {
    Write-Warning "[native] Appium did not come up"
    exit 1
}
Write-Host "[native] Appium ready"

# --- flow --------------------------------------------------------------------
Write-Host "[native] running android_happy_flow.py"
Push-Location $MON
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:ANDROID_HOME = $SDK
    $env:ANDROID_SDK_ROOT = $SDK
    & $env:PYTHON_BIN (Join-Path $MON "native\android_happy_flow.py")
    Write-Host ("[native] flow exit code: " + $LASTEXITCODE)
} finally {
    Pop-Location
}
