#!/usr/bin/env python3
"""Client for the image-generation MCP server (image-mcp 1.30).

Transport: MCP "streamable HTTP" — POST JSON-RPC, response is an SSE frame with a
`data: {...}` line, and the server hands back an `Mcp-Session-Id` header that must
be sent on every later call.

Tools used by the Image Verification pipeline:
    inpaint_image(prompt, image_base64, mask_base64, negative_prompt, steps, guidance)
    gpu_status()

The server returns the result as an image block: {"type":"image","data":"<base64>"}.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

MCP_URL = os.environ.get(
    "IMAGE_MCP_URL",
    # Permanent Tailscale Funnel endpoint (a quick tunnel kept changing/530ing).
    "https://desktop-deghg21.tailff34af.ts.net/xpMtZXQ91CScRrXu60i6eOnbkrHR5i2SkYKliSaDpPk",
)
_TIMEOUT = int(os.environ.get("IMAGE_MCP_TIMEOUT", "600"))
_session: str | None = None


def _call(payload: dict, timeout: int | None = None) -> dict | None:
    global _session
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if _session:
        headers["Mcp-Session-Id"] = _session
    req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout or _TIMEOUT) as r:
        body = r.read().decode()
        sid = r.headers.get("mcp-session-id")
    if sid:
        _session = sid
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return None


def connect() -> bool:
    """Initialize a session (idempotent)."""
    global _session
    if _session:
        return True
    try:
        res = _call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "gajab-hub", "version": "1.0"}}},
                    timeout=60)
        if not res or "result" not in res:
            return False
        _call({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=30)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[image-mcp] connect failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        _session = None
        return False


def available() -> bool:
    return connect()


def gpu_status() -> str:
    if not connect():
        return ""
    try:
        res = _call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                     "params": {"name": "gpu_status", "arguments": {}}}, timeout=60)
        return str(((res or {}).get("result") or {}).get("structuredContent") or "")[:200]
    except Exception:
        return ""


def _retry_connect(attempts: int = 7, delay: float = 20.0) -> bool:
    import time as _t
    for i in range(attempts):
        if connect():
            return True
        if i < attempts - 1:
            print(f"[image-mcp] worker asleep (attempt {i+1}/{attempts}), retrying in {delay:.0f}s",
                  file=sys.stderr)
            _t.sleep(delay)
    return False


def inpaint(image_png: bytes, mask_png: bytes, prompt: str,
            negative_prompt: str = "text, letters, words, watermark, logo, badge, banner",
            steps: int = 24, guidance: float = 6.0) -> bytes:
    """Return the inpainted image bytes (JPEG/PNG) or b'' on failure."""
    if not _retry_connect():
        return b""
    args = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "image_base64": base64.b64encode(image_png).decode("ascii"),
        "mask_base64": base64.b64encode(mask_png).decode("ascii"),
        "steps": steps,
        "guidance": guidance,
    }
    try:
        res = _call({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                     "params": {"name": "inpaint_image", "arguments": args}})
    except Exception as exc:  # noqa: BLE001
        print(f"[image-mcp] inpaint failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return b""
    result = (res or {}).get("result") or {}
    if result.get("isError"):
        print(f"[image-mcp] tool error: {str(result)[:200]}", file=sys.stderr)
        return b""
    for block in result.get("content") or []:
        if block.get("type") == "image" and block.get("data"):
            try:
                return base64.b64decode(block["data"])
            except Exception:
                return b""
    print(f"[image-mcp] no image in response: {str(result)[:160]}", file=sys.stderr)
    return b""
