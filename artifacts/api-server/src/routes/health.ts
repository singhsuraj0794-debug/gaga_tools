import { Router, type IRouter } from "express";
import { HealthCheckResponse } from "@workspace/api-zod";
import { logger } from "../lib/logger.js";

const router: IRouter = Router();

interface MonitorResult {
  passed: number;
  failed: number;
  steps: { name: string; status: string; duration: number; error?: string }[];
  timestamp: string;
  overall: string;
}

let latestResult: MonitorResult | null = null;

router.get("/healthz", (_req, res) => {
  const data = HealthCheckResponse.parse({ status: "ok" });
  res.json(data);
});

router.post("/monitor/results", (req, res) => {
  const body = req.body as MonitorResult;
  if (!body || !body.overall) {
    res.status(400).json({ error: "Invalid monitor result" });
    return;
  }
  latestResult = body;
  logger.info({ overall: body.overall, passed: body.passed, failed: body.failed }, "Monitor result received");
  res.json({ ok: true });
});

router.get("/monitor/results", (_req, res) => {
  res.json(latestResult || { overall: "never", passed: 0, failed: 0, steps: [], timestamp: null });
});

export default router;
