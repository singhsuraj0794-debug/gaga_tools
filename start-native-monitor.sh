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

echo "=== Ensuring Appium server is running ==="
if ! curl -s http://localhost:4723/status >/dev/null 2>&1; then
  echo "Starting Appium..."
  nohup appium --port 4723 > /tmp/appium.log 2>&1 &
  sleep 8
fi

echo "=== Running native happy flow ==="
cd "$SCRIPT_DIR/monitoring"
python3 native/android_happy_flow.py
