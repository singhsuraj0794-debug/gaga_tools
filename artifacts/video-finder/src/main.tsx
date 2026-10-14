import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

// The API is reached through the permanent ngrok domain. ngrok's free tier
// answers a browser-looking request with an HTML interstitial
// (ERR_NGROK_6024) instead of our JSON, so every fetch() from the deployed app
// got HTML, response.json() threw, and the pollers retried forever — the UI sat
// on "loading" even though the job had completed on the server. Send ngrok's
// documented opt-out header on all ngrok requests.
const _fetch = window.fetch.bind(window);
window.fetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
  const url =
    typeof input === "string" ? input
      : input instanceof URL ? input.href
        : input.url;
  if (url.includes("ngrok")) {
    const headers = new Headers(init.headers || {});
    headers.set("ngrok-skip-browser-warning", "1");
    return _fetch(input, { ...init, headers });
  }
  return _fetch(input, init);
};

createRoot(document.getElementById("root")!).render(<App />);
