const REMOTE_API = "https://product-video-scraper-api.onrender.com";

// In dev, use the Vite proxy (relative /api → localhost:8080).
// In production, use VITE_PRELISTING_API_URL if set, else the remote API.
export const API_BASE =
  import.meta.env.VITE_PRELISTING_API_URL ||
  (import.meta.env.DEV ? "" : REMOTE_API);
