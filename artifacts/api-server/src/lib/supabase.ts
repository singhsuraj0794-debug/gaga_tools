import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient | null {
  if (client) return client;
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_KEY;
  if (!url || !key) return null;

  // Polyfill WebSocket for Node 20 (Supabase realtime-js needs it)
  try {
    const WebSocket = require("ws");
    (globalThis as any).WebSocket = (globalThis as any).WebSocket || WebSocket;
  } catch {}

  client = createClient(url, key);
  return client;
}
