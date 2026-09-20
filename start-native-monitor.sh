#!/bin/bash
# start-native-monitor.sh — Start the Android emulator + Appium server, then run
# the native happy-flow check. Used by the 6-hourly native-monitor workflow.
set -e

export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
ADB="$ANDROID_HOME/platform-tools/adb"
EMU="$ANDROID_HOME/emulator/emulator"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Ensuring emulator is running ==="
if ! "$ADB" devices | grep -q "emulator"; then
  echo "Starting emulator gajab_pixel7..."
  "$EMU" -avd gajab_pixel7 -no-snapshot-load >/dev/null 2>&1 &
  sleep 15
fi

echo "=== Waiting for device ==="
"$ADB" wait-for-device
for i in $(seq 1 60); do
  BOOT=$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
  if [ "$BOOT" = "1" ]; then echo "Device booted."; break; fi
  sleep 3
done

# Disable animations. Without this the app's UI thread never goes idle and
# UIAutomator cannot read the accessibility tree:
#   "Timed out after 10091ms waiting for the root AccessibilityNodeInfo in the
#    active window ... the application is being idle long enough"
# which made EVERY element lookup time out (steps took 20-105s and all failed).
echo "=== Disabling device animations ==="
"$ADB" shell settings put global window_animation_scale 0 2>/dev/null || true
"$ADB" shell settings put global transition_animation_scale 0 2>/dev/null || true
"$ADB" shell settings put global animator_duration_scale 0 2>/dev/null || true
echo "  window=$(  "$ADB" shell settings get global window_animation_scale 2>/dev/null | tr -d '\r') transition=$(  "$ADB" shell settings get global transition_animation_scale 2>/dev/null | tr -d '\r') animator=$(  "$ADB" shell settings get global animator_duration_scale 2>/dev/null | tr -d '\r')"

echo "=== Ensuring Appium server is running ==="
if ! curl -s http://localhost:4723/status >/dev/null 2>&1; then
  echo "Starting Appium..."
  nohup appium --port 4723 > /tmp/appium.log 2>&1 &
  sleep 8
fi

echo "=== Running native happy flow ==="
cd "$SCRIPT_DIR/monitoring"
python3 native/android_happy_flow.py
