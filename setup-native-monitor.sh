#!/bin/bash
# setup-native-monitor.sh — Android emulator + Appium setup (already completed once).
# The Android SDK lives at /opt/homebrew/share/android-commandlinetools (brew cask).
set -e

export ANDROID_HOME="/opt/homebrew/share/android-commandlinetools"
CMDS="$ANDROID_HOME/cmdline-tools/latest/bin"
EMU="$ANDROID_HOME/emulator/emulator"

# Add to your shell profile so tools are always available
grep -q "ANDROID_HOME" ~/.zprofile 2>/dev/null || {
  echo 'export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools' >> ~/.zprofile
  echo 'export PATH="$ANDROID_HOME/emulator:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"' >> ~/.zprofile
}

echo "=== Launching emulator gajab_pixel7 (Play Store) ==="
"$EMU" -avd gajab_pixel7 -no-snapshot-load &
echo ""
echo "Next: sign into Google in the emulator, then install the gajab app from Play Store."
