// Preload: polyfill WebSocket for Node 20 before any Supabase imports
try {
  const WS = require("ws");
  if (typeof (globalThis as any).WebSocket === "undefined") {
    (globalThis as any).WebSocket = WS;
  }
} catch {}
