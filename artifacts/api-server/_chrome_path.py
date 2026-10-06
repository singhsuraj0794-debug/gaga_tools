#!/usr/bin/env python3
"""Resolve the system Google Chrome executable, cross-platform.

Returns None when Chrome is not found, in which case callers should let
Playwright use its own bundled Chromium (install with `playwright install
chromium`). Set GAJAB_CHROME (or CHROME_PATH) to override.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

_CANDIDATES = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ],
    "linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ],
}


def chrome_executable() -> Optional[str]:
    override = os.environ.get("GAJAB_CHROME") or os.environ.get("CHROME_PATH")
    if override and os.path.exists(override):
        return override
    for path in _CANDIDATES.get(sys.platform, []):
        if os.path.exists(path):
            return path
    return None
