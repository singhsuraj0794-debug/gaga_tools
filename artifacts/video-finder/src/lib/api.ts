// The scraper API runs on the always-on Windows box, reached through the
// permanent ngrok domain — the same host the validator's compute API uses.
//
// It used to default to the Render deployment, which keeps scrape jobs in
// memory only: a run was lost whenever Render restarted or spun down, which is
// exactly the "dropped after N products" report. Jobs on the Windows box are
// persisted to disk and resumed, and that is where the scrapers actually run.
// The ngrok free plan hit its monthly bandwidth cap (ERR_NGROK_725), which took
// the whole app down - not just this API. The box is now published through a
// Cloudflare quick tunnel, which also serves /rest/v1 and /storage/v1 (port 9000
// is the gateway), so ONE origin covers both the API and Supabase.
const WINDOWS_API = "https://ladies-alumni-monte-toolbox.trycloudflare.com";

// Quick-tunnel URLs change whenever the tunnel restarts. Rather than needing a
// redeploy every time, the base can be overridden at runtime: open the app once
// with ?api=https://new-host (it is remembered) or set
// localStorage.gajab_api_base = "https://new-host".
const API_OVERRIDE_KEY = "gajab_api_base";
function resolveApiOverride(): string {
  try {
    const fromQuery = new URLSearchParams(window.location.search).get("api");
    if (fromQuery && /^https?:\/\//i.test(fromQuery)) {
      const clean = fromQuery.replace(/\/+$/, "");
      localStorage.setItem(API_OVERRIDE_KEY, clean);
      return clean;
    }
    return (localStorage.getItem(API_OVERRIDE_KEY) || "").replace(/\/+$/, "");
  } catch {
    return "";
  }
}

// In dev, use the Vite proxy (relative /api → localhost:8080).
// In production, use the runtime override, then VITE_PRELISTING_API_URL, then
// the Windows box.
export const API_BASE =
  resolveApiOverride() ||
  import.meta.env.VITE_PRELISTING_API_URL ||
  (import.meta.env.DEV ? "" : WINDOWS_API);

// The same gateway serves Supabase (postgrest + storage), so derive it from the
// API origin. A stale VITE_SUPABASE_URL baked into a previous build must not win.
export const SUPABASE_BASE =
  resolveApiOverride() ||
  (import.meta.env.DEV ? (import.meta.env.VITE_SUPABASE_URL as string | undefined) : "") ||
  WINDOWS_API;
