// The scraper API runs on the always-on Windows box, reached through the
// permanent ngrok domain — the same host the validator's compute API uses.
//
// It used to default to the Render deployment, which keeps scrape jobs in
// memory only: a run was lost whenever Render restarted or spun down, which is
// exactly the "dropped after N products" report. Jobs on the Windows box are
// persisted to disk and resumed, and that is where the scrapers actually run.
const WINDOWS_API = "https://headphone-shudder-lavender.ngrok-free.dev";

// In dev, use the Vite proxy (relative /api → localhost:8080).
// In production, use VITE_PRELISTING_API_URL if set, else the Windows box.
export const API_BASE =
  import.meta.env.VITE_PRELISTING_API_URL ||
  (import.meta.env.DEV ? "" : WINDOWS_API);
