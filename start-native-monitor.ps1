#Requires -Version 5.1
# start-native-monitor.ps1 - Windows equivalent of start-native-monitor.sh:
# ensure the Android emulator + Appium are up, then run the native happy flow.
# AVD default: gajab_pixel7 (override with GAJAB_AVD).
. "$PSScriptRoot\windows-common.ps1"

$androidHome = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { "$env:LOCALAPPDATA\Android\Sdk" }
$env:ANDROID_HOME = $androidHome
$env:ANDROID_SDK_ROOT = $androidHome
$env:Path = "$androidHome\platform-tools;$androidHome\emulator;$env:Path"

$adb = Join-Path $androidHome "platform-tools\adb.exe"
$emu = Join-Path $androidHome "emulator\emulator.exe"
$avd = if ($env:GAJAB_AVD) { $env:GAJAB_AVD } else { "gajab_pixel7" }

if (-not (Test-Path $adb)) { throw "adb not found at $adb - set ANDROID_HOME or install the Android SDK" }

$devices = & $adb devices
if ($devices -notmatch "emulator-\d+\s+device") {
    Write-Host "Starting emulator $avd ..."
    Start-Process -FilePath $emu -ArgumentList @("-avd", $avd, "-no-snapshot-load") | Out-Null
    Start-Sleep -Seconds 15
}

& $adb wait-for-device
for ($i = 0; $i -lt 60; $i++) {
    $boot = ((& $adb shell getprop sys.boot_completed) -join "").Trim()
    if ($boot -eq "1") { Write-Host "Device booted."; break }
    Start-Sleep -Seconds 3
}

# Animations off: with them on the app UI thread never goes idle and
# UIAutomator cannot read the accessibility tree.
& $adb shell settings put global window_animation_scale 0 | Out-Null
& $adb shell settings put global transition_animation_scale 0 | Out-Null
& $adb shell settings put global animator_duration_scale 0 | Out-Null

if (-not (Test-PortOpen $AppiumPort)) {
    Write-Host "Starting Appium on $AppiumPort ..."
    Start-Process -FilePath "appium" -ArgumentList @("--port", "$AppiumPort") -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 8
}

Write-Host "Running native happy flow ..."
Set-Location (Join-Path $RepoDir "monitoring")
& (Get-Python) "native/android_happy_flow.py"
