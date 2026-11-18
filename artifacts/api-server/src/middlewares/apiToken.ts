// Optional shared-secret guard for the compute API.
//
// The API is published on a public tunnel and has no authentication: anyone who
// learns the URL can call /api/products/delete-duplicates, /sync or /image-verify
// and start expensive or destructive work. Full auth is out of scope, but a
// single shared token is enough to stop accidental/opportunistic use.
//
// Behaviour:
//   API_TOKEN unset  -> no-op (current deployments keep working unchanged)
//   API_TOKEN set    -> require `x-api-token: <token>` (or ?token=<token>)
// Health checks are always allowed so tunnels/watchdogs can probe.
import type { Request, Response, NextFunction } from "express";

const OPEN_PATHS = new Set(["/api/healthz", "/healthz"]);

export function apiTokenGuard(req: Request, res: Response, next: NextFunction): void {
  const expected = (process.env.API_TOKEN || "").trim();
  if (!expected) {
    next();
    return;
  }
  if (req.method === "OPTIONS" || OPEN_PATHS.has(req.path)) {
    next();
    return;
  }
  const given =
    String(req.get("x-api-token") || "") ||
    String((req.query?.token as string | undefined) || "");
  if (given && given === expected) {
    next();
    return;
  }
  req.log?.warn({ path: req.path }, "rejected: missing or bad x-api-token");
  res.status(401).json({ error: "unauthorized: set the x-api-token header" });
}
