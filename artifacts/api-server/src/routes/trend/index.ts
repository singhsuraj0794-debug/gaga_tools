import { Router, type IRouter, type Request, type Response } from "express";
import multer from "multer";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { v4 as uuidv4 } from "uuid";
import axios from "axios";
import { logger } from "../../lib/logger";
import { isAllowedDownloadUrl } from "../../lib/downloadManager";
import { tmpPath } from "../../lib/tmp";

const __filename = fileURLToPath(import.meta.url);
const __parentDirname = path.dirname(__filename);

function resolveScript(name: string): string {
  const localPath = path.resolve(__parentDirname, name);
  if (fs.existsSync(localPath)) return localPath;
  return tmpPath(name);
}

// Dedicated Python 3.11 venv that has tribev2 + torch installed. Kept outside
// the repo (and out of git) — see the Meta Mind Map setup notes.
const TRIBE_PYTHON =
  process.env.TRIBE_PYTHON ||
  path.join(os.homedir(), "tribe-v2", "venv", "bin", "python");
const YT_DLP_PATH = process.env.YT_DLP_PATH || path.join(os.homedir(), "bin", "yt-dlp");
const TRIBE_MAX_SECONDS = process.env.TRIBE_MAX_SECONDS || "8";
// Instagram (and other gated sources) need an authenticated session; reuse the
// logged-in browser's cookies. Set to "" to disable.
const YT_DLP_COOKIES_FROM_BROWSER =
  process.env.YT_DLP_COOKIES_FROM_BROWSER ?? "chrome";
const YT_DLP_COOKIES = process.env.YT_DLP_COOKIES || "";
// Benchmark reels are analysed in the background over hours — keep each short.
const BENCHMARK_MAX_SECONDS = process.env.BENCHMARK_MAX_SECONDS || "5";

const TREND_DIR = path.resolve(process.cwd(), "trend_finder");
fs.mkdirSync(TREND_DIR, { recursive: true });

type TrendStatus = "queued" | "downloading" | "processing" | "completed" | "failed";
type TrendKind = "user" | "benchmark";

interface BenchmarkMeta {
  shortcode: string;
  url: string;
  uploader?: string;
  uploaderId?: string;
  likeCount?: number | null;
  commentCount?: number | null;
  durationSec?: number | null;
  successScore?: number;
  addedAt: string;
}

interface TrendJob {
  jobId: string;
  kind: TrendKind;
  status: TrendStatus;
  sourceType: "url" | "upload";
  sourceUrl?: string;
  title?: string;
  createdAt: string;
  progress: { stage: string; pct: number; message: string };
  result?: Record<string, unknown>;
  meta?: BenchmarkMeta;
  error?: string;
  dir: string;
}

const jobs = new Map<string, TrendJob>();
// Sequential queue so benchmark ingestion never runs two CPU-heavy jobs at once.
const benchmarkQueue: string[] = [];
let benchmarkRunning = false;

function ytDlpCookieArgs(): string[] {
  if (YT_DLP_COOKIES && fs.existsSync(YT_DLP_COOKIES)) return ["--cookies", YT_DLP_COOKIES];
  if (YT_DLP_COOKIES_FROM_BROWSER) return ["--cookies-from-browser", YT_DLP_COOKIES_FROM_BROWSER];
  return [];
}

function shortcodeOf(url: string): string {
  const m = url.match(/\/(?:reel|reels|p|tv)\/([A-Za-z0-9_-]+)/);
  return m ? m[1] : url.replace(/[^A-Za-z0-9]+/g, "_").slice(-24);
}

function successScore(like?: number | null, comment?: number | null): number {
  // Views aren't exposed by Instagram; likes + weighted comments are the proxy.
  return Math.round((like || 0) + 3 * (comment || 0));
}

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 300 * 1024 * 1024 },
});

const router: IRouter = Router();

function readProgress(job: TrendJob): void {
  try {
    const raw = fs.readFileSync(path.join(job.dir, "progress.json"), "utf8");
    const p = JSON.parse(raw) as { stage?: string; pct?: number; message?: string };
    job.progress = {
      stage: p.stage || "processing",
      pct: typeof p.pct === "number" ? p.pct : job.progress.pct,
      message: p.message || "",
    };
  } catch {
    /* progress file not written yet */
  }
}

function runYtDlp(args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(YT_DLP_PATH, [...ytDlpCookieArgs(), ...args], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => (stdout += d.toString()));
    child.stderr.on("data", (d: Buffer) => (stderr += d.toString()));
    child.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`yt-dlp failed (${code}): ${stderr.slice(-500)}`));
    });
    child.on("error", reject);
  });
}

async function downloadWithYtDlp(url: string, outPath: string): Promise<void> {
  await runYtDlp([
    "--no-playlist", "-f", "best[ext=mp4]/best", "--no-check-certificates",
    "-o", outPath, url,
  ]);
  if (!fs.existsSync(outPath)) throw new Error("yt-dlp finished but no file was written");
}

/** Fetch metadata (title, likes, comments, duration) without downloading. */
async function fetchYtDlpMeta(url: string): Promise<Record<string, any> | null> {
  try {
    const out = await runYtDlp(["--no-warnings", "--skip-download", "--dump-json", url]);
    const line = out.split("\n").find((l) => l.trim().startsWith("{"));
    return line ? JSON.parse(line) : null;
  } catch (err) {
    logger.warn({ err, url }, "yt-dlp metadata fetch failed");
    return null;
  }
}

function runAnalyzer(job: TrendJob, videoPath: string, maxSeconds: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const script = resolveScript("_tribe_analyze.py");
    const child = spawn(
      TRIBE_PYTHON,
      [script, videoPath, job.dir, "--max-seconds", maxSeconds, "--device", "cpu"],
      { env: { ...process.env }, stdio: ["ignore", "pipe", "pipe"] },
    );

    let stderr = "";
    child.stdout.on("data", (d: Buffer) => {
      const text = d.toString();
      for (const line of text.split("\n")) {
        const m = line.match(/@@TRIBE (\S+) (\d+) ?(.*)/);
        if (m) job.progress = { stage: m[1], pct: Number(m[2]), message: m[3] || "" };
      }
      logger.info({ jobId: job.jobId }, text.trim());
    });
    child.stderr.on("data", (d: Buffer) => (stderr += d.toString()));

    const timer = setInterval(() => readProgress(job), 1500);
    child.on("close", (code) => {
      clearInterval(timer);
      readProgress(job);
      if (code === 0) resolve();
      else reject(new Error(`analysis failed (${code}): ${stderr.slice(-800)}`));
    });
    child.on("error", (err) => {
      clearInterval(timer);
      reject(err);
    });
  });
}

/**
 * Turn the raw brain-engagement signals into a senior-strategist critique using
 * Groq (fast hosted LLM). Non-fatal: on any error the Python heuristic critique
 * in result.critique is kept.
 */
async function enrichCritique(result: Record<string, any>): Promise<void> {
  const key = process.env.GROQ_API_KEY;
  if (!key) return;
  const model = process.env.GROQ_MODEL || "openai/gpt-oss-120b";

  const dimMeans = Object.fromEntries(
    Object.entries(result.dimensions || {}).map(([k, v]) => [
      k,
      Math.round(((v as number[]).reduce((a, b) => a + b, 0) / (v as number[]).length) * 100) / 100,
    ]),
  );
  const payload = {
    durationSec: result.durationSec,
    overallScore: result.overallScore,
    segments: result.segments,
    dropoffs: result.dropoffs,
    breakdown: (result.critique?.breakdown || result.breakdown || []).slice(0, 6),
    moments: (result.moments || []).slice(0, 16),
    clipText: result.clipText || "",
    brainFunctions: (result.brainFunctions || []).map((b: any) => ({
      name: b.name, score: b.score, status: b.status, reason: b.reason,
    })),
    dimensionMeans: dimMeans,
  };

  const system =
    "You are a senior paid-social creative strategist reviewing a short video ad. " +
    "You are given TRIBE v2 brain-engagement signals AND concrete per-second video " +
    "observations (hard cuts, static shots, dark frames, faces, audio level) plus a " +
    "breakdown of what happened at each drop-off. Ground EVERY point in a specific " +
    "second and the actual observation — never give generic advice. Never invent " +
    "facts that are not in the signals. Reply with STRICT JSON only.";
  const user =
    `Signals: ${JSON.stringify(payload)}\n\n` +
    `Return JSON exactly of the shape:\n` +
    `{"summary": string, "segments": [{"segment": "Hook"|"Bridge"|"Offer", ` +
    `"score": number, "verdict": "strong"|"ok"|"weak", "issues": string[], ` +
    `"fixes": string[], "rewriteIdea": string}], ` +
    `"breakdown": [{"t": number, "what": string, "why": string, "fix": string}]}\n` +
    `Rules: each issue/fix must cite the timestamp and the observed cause ` +
    `(e.g. "at 2s a hard cut lands on a static, faceless shot — add motion or a face"). ` +
    `1-3 issues and 1-3 fixes per segment; skip segments with a null score. ` +
    `Carry the breakdown through with concrete fixes. summary is 1-2 sentences ` +
    `naming the single biggest problem and the second it occurs.`;

  const resp = await axios.post(
    "https://api.groq.com/openai/v1/chat/completions",
    {
      model,
      temperature: 0.4,
      response_format: { type: "json_object" },
      messages: [
        { role: "system", content: system },
        { role: "user", content: user },
      ],
    },
    { headers: { Authorization: `Bearer ${key}` }, timeout: 30000 },
  );

  const text = resp.data?.choices?.[0]?.message?.content;
  if (!text) return;
  const parsed = JSON.parse(text);
  if (parsed?.summary && Array.isArray(parsed.segments)) {
    // Keep the feature-grounded breakdown; let Groq enrich fixes per second and
    // add anything it found. Never lose the concrete observations.
    const base: any[] = result.critique?.breakdown || [];
    const groq: any[] = Array.isArray(parsed.breakdown) ? parsed.breakdown : [];
    const byT = new Map<number, any>();
    for (const b of base) byT.set(Number(b.t), b);
    for (const g of groq) {
      const t = Number(g.t);
      const prev = byT.get(t);
      byT.set(t, prev
        ? { ...prev, fix: g.fix || prev.fix, why: g.why || prev.why }
        : g);
    }
    const merged = [...byT.values()].sort((a, b) => Number(a.t) - Number(b.t));
    result.critique = { ...parsed, breakdown: merged, source: "groq", model };
  }
}

async function runJob(job: TrendJob, file: Express.Multer.File | undefined, url?: string) {
  try {
    let videoPath: string;
    if (file) {
      videoPath = path.join(job.dir, "source.mp4");
      fs.writeFileSync(videoPath, file.buffer);
    } else if (url) {
      job.status = "downloading";
      job.progress = { stage: "download", pct: 5, message: "Downloading video" };
      videoPath = path.join(job.dir, "source.mp4");
      await downloadWithYtDlp(url, videoPath);
    } else {
      throw new Error("No video input provided");
    }

    job.status = "processing";
    job.progress = { stage: "queued", pct: 8, message: "Starting analysis" };
    const maxSeconds = job.kind === "benchmark" ? BENCHMARK_MAX_SECONDS : TRIBE_MAX_SECONDS;
    await runAnalyzer(job, videoPath, maxSeconds);

    const resultPath = path.join(job.dir, "result.json");
    const result = JSON.parse(fs.readFileSync(resultPath, "utf8")) as Record<string, any>;
    // Benchmark reels only need scores (for the distribution), so skip the LLM
    // critique to save time and API calls.
    if (job.kind === "user") {
      job.progress = { stage: "critique", pct: 95, message: "Writing strategist critique" };
      try {
        await enrichCritique(result);
        fs.writeFileSync(resultPath, JSON.stringify(result));
      } catch (err) {
        logger.warn({ err, jobId: job.jobId }, "Groq critique failed — keeping heuristic");
      }
    }
    job.result = result;
    job.status = "completed";
    job.progress = { stage: "done", pct: 100, message: "Complete" };
  } catch (err) {
    job.status = "failed";
    job.error = err instanceof Error ? err.message : String(err);
    job.progress = { stage: "error", pct: 100, message: job.error };
    logger.error({ err, jobId: job.jobId }, "trend analysis failed");
  }
}

// POST /api/trend/analyze — multipart file OR JSON { url, title }
router.post("/trend/analyze", upload.single("file"), async (req, res): Promise<void> => {
  const url = typeof req.body?.url === "string" ? req.body.url.trim() : "";
  const title = typeof req.body?.title === "string" ? req.body.title.trim() : undefined;

  if (!req.file && !url) {
    res.status(400).json({ error: "Provide a video file or a video URL" });
    return;
  }
  if (url && !isAllowedDownloadUrl(url)) {
    res.status(400).json({
      error: "URL not allowed. Only YouTube, Instagram, Facebook, and TikTok URLs are accepted.",
    });
    return;
  }

  const jobId = uuidv4();
  const dir = path.join(TREND_DIR, jobId);
  fs.mkdirSync(dir, { recursive: true });

  const job: TrendJob = {
    jobId,
    kind: "user",
    status: "queued",
    sourceType: req.file ? "upload" : "url",
    sourceUrl: url || undefined,
    title,
    createdAt: new Date().toISOString(),
    progress: { stage: "queued", pct: 0, message: "Queued" },
    dir,
  };
  jobs.set(jobId, job);

  res.json({ jobId, status: job.status });

  runJob(job, req.file, url || undefined).catch((err) => {
    logger.error({ err, jobId }, "unhandled trend job error");
  });
});

function _stat(arr: number[]) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const q = (p: number) => s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))];
  return {
    count: s.length,
    mean: Math.round(s.reduce((a, b) => a + b, 0) / s.length),
    p25: q(0.25), p50: q(0.5), p75: q(0.75),
    min: s[0], max: s[s.length - 1],
  };
}

// ─── Benchmark corpus ─────────────────────────────────────────────────────
// Successful public reels (ranked by likes/comments) analysed in the background
// to form a reference distribution. Discovery is manual — Instagram has no
// public search API — so reels are supplied as URLs (UI or benchmark_seeds.json).

function loadBenchmarkCorpus(): void {
  try {
    for (const d of fs.readdirSync(TREND_DIR)) {
      const metaPath = path.join(TREND_DIR, d, "meta.json");
      if (!fs.existsSync(metaPath)) continue;
      if (jobs.has(d)) continue;
      try {
        const meta = JSON.parse(fs.readFileSync(metaPath, "utf8")) as BenchmarkMeta & { jobId?: string };
        const resultPath = path.join(TREND_DIR, d, "result.json");
        const result = fs.existsSync(resultPath)
          ? (JSON.parse(fs.readFileSync(resultPath, "utf8")) as Record<string, unknown>)
          : undefined;
        jobs.set(d, {
          jobId: d,
          kind: "benchmark",
          status: result ? "completed" : "failed",
          sourceType: "url",
          sourceUrl: meta.url,
          title: meta.uploader ? `@${meta.uploader}` : meta.shortcode,
          createdAt: meta.addedAt || new Date().toISOString(),
          progress: { stage: "done", pct: 100, message: "Complete" },
          result,
          meta,
          dir: path.join(TREND_DIR, d),
        });
      } catch { /* skip malformed */ }
    }
    logger.info({ benchmark: [...jobs.values()].filter((j) => j.kind === "benchmark").length }, "benchmark corpus loaded");
  } catch { /* no dir */ }
}

async function runBenchmarkJob(job: TrendJob, url: string): Promise<void> {
  try {
    job.status = "downloading";
    job.progress = { stage: "meta", pct: 3, message: "Fetching reel metadata" };
    const meta = await fetchYtDlpMeta(url);
    job.meta = {
      shortcode: shortcodeOf(url),
      url,
      uploader: meta?.uploader,
      uploaderId: meta?.uploader_id,
      likeCount: meta?.like_count ?? null,
      commentCount: meta?.comment_count ?? null,
      durationSec: meta?.duration ?? null,
      successScore: successScore(meta?.like_count, meta?.comment_count),
      addedAt: job.createdAt,
    };
    job.title = meta?.uploader ? `@${meta.uploader} · ${job.meta.shortcode}` : job.meta.shortcode;

    const videoPath = path.join(job.dir, "source.mp4");
    job.progress = { stage: "download", pct: 6, message: "Downloading reel" };
    await downloadWithYtDlp(url, videoPath);

    job.status = "processing";
    await runAnalyzer(job, videoPath, BENCHMARK_MAX_SECONDS);

    const result = JSON.parse(fs.readFileSync(path.join(job.dir, "result.json"), "utf8")) as Record<string, any>;
    job.result = result;
    job.status = "completed";
    job.progress = { stage: "done", pct: 100, message: "Complete" };
  } catch (err) {
    job.status = "failed";
    job.error = err instanceof Error ? err.message : String(err);
    job.progress = { stage: "error", pct: 100, message: job.error };
    logger.warn({ err, url }, "benchmark reel failed");
  } finally {
    fs.writeFileSync(
      path.join(job.dir, "meta.json"),
      JSON.stringify({ jobId: job.jobId, ...(job.meta || { shortcode: shortcodeOf(url), url, addedAt: job.createdAt }) }),
    );
  }
}

function pumpBenchmarkQueue(): void {
  if (benchmarkRunning) return;
  const nextId = benchmarkQueue.shift();
  if (!nextId) return;
  const job = jobs.get(nextId);
  if (!job) { pumpBenchmarkQueue(); return; }
  benchmarkRunning = true;
  runBenchmarkJob(job, job.sourceUrl as string).finally(() => {
    benchmarkRunning = false;
    pumpBenchmarkQueue();
  });
}

function enqueueBenchmark(urls: string[]): { queued: string[]; skipped: string[] } {
  const existing = new Set(
    [...jobs.values()].filter((j) => j.kind === "benchmark").map((j) => j.meta?.shortcode),
  );
  const queued: string[] = [];
  const skipped: string[] = [];
  for (const raw of urls) {
    const url = raw.trim();
    if (!url || !isAllowedDownloadUrl(url)) { skipped.push(url); continue; }
    const shortcode = shortcodeOf(url);
    if (existing.has(shortcode)) { skipped.push(url); continue; }
    existing.add(shortcode);
    const jobId = uuidv4();
    const dir = path.join(TREND_DIR, jobId);
    fs.mkdirSync(dir, { recursive: true });
    const job: TrendJob = {
      jobId, kind: "benchmark", status: "queued", sourceType: "url",
      sourceUrl: url, title: shortcode, createdAt: new Date().toISOString(),
      progress: { stage: "queued", pct: 0, message: "Queued" }, dir,
    };
    jobs.set(jobId, job);
    benchmarkQueue.push(jobId);
    queued.push(url);
  }
  pumpBenchmarkQueue();
  return { queued, skipped };
}

// POST /api/trend/benchmark/ingest — queue reels for benchmark analysis
router.post("/trend/benchmark/ingest", (req, res): void => {
  let urls: string[] = [];
  if (Array.isArray(req.body?.urls)) urls = req.body.urls.filter((u: unknown) => typeof u === "string");
  else if (typeof req.body?.url === "string") urls = [req.body.url];
  if (urls.length === 0) {
    res.status(400).json({ error: "Provide { urls: string[] }" });
    return;
  }
  const { queued, skipped } = enqueueBenchmark(urls);
  res.json({ queued: queued.length, skipped: skipped.length, urls: queued, queueLength: benchmarkQueue.length });
});

// POST /api/trend/benchmark/seed — queue the bundled seed list
router.post("/trend/benchmark/seed", (_req, res): void => {
  const seedPath = resolveScript("benchmark_seeds.json");
  let urls: string[] = [];
  try {
    const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
    urls = (seed.reels || []).filter((u: unknown) => typeof u === "string");
  } catch {
    res.status(404).json({ error: "benchmark_seeds.json not found" });
    return;
  }
  const { queued, skipped } = enqueueBenchmark(urls);
  res.json({ queued: queued.length, skipped: skipped.length, queueLength: benchmarkQueue.length });
});

// GET /api/trend/benchmark/status — ingest queue + corpus
router.get("/trend/benchmark/status", (_req, res): void => {
  const bm = [...jobs.values()].filter((j) => j.kind === "benchmark");
  res.json({
    corpusCount: bm.filter((j) => j.status === "completed").length,
    queueLength: benchmarkQueue.length,
    running: benchmarkRunning,
    items: bm
      .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
      .slice(0, 50)
      .map((j) => ({
        jobId: j.jobId, status: j.status, title: j.title, url: j.sourceUrl,
        likeCount: j.meta?.likeCount ?? null, overallScore: j.result?.overallScore ?? null,
        progress: j.progress,
      })),
  });
});

// GET /api/trend/benchmark — how a reel compares (benchmark corpus + local)
router.get("/trend/benchmark", (_req, res): void => {
  const rows: Record<string, any>[] = [];
  try {
    for (const d of fs.readdirSync(TREND_DIR)) {
      const p = path.join(TREND_DIR, d, "result.json");
      if (!fs.existsSync(p)) continue;
      try {
        const r = JSON.parse(fs.readFileSync(p, "utf8"));
        if (typeof r.overallScore !== "number") continue;
        const metaPath = path.join(TREND_DIR, d, "meta.json");
        const meta = fs.existsSync(metaPath) ? JSON.parse(fs.readFileSync(metaPath, "utf8")) : null;
        rows.push({ jobId: d, result: r, meta, isBenchmark: !!meta });
      } catch { /* skip malformed */ }
    }
  } catch { /* no dir */ }

  const segOf = (r: Record<string, any>, name: string): number | null => {
    const s = (r.segments || []).find((x: any) => x.name === name);
    return typeof s?.score === "number" ? s.score : null;
  };
  const build = (subset: typeof rows) => {
    const pick = (fn: (r: Record<string, any>) => number | null) =>
      subset.map((x) => fn(x.result)).filter((v): v is number => typeof v === "number");
    return {
      count: subset.length,
      overall: _stat(pick((r) => r.overallScore)),
      segments: {
        Hook: _stat(pick((r) => segOf(r, "Hook"))),
        Bridge: _stat(pick((r) => segOf(r, "Bridge"))),
        Offer: _stat(pick((r) => segOf(r, "Offer"))),
      },
      scores: pick((r) => r.overallScore),
    };
  };

  const benchmark = rows.filter((x) => x.isBenchmark);
  const local = rows.filter((x) => !x.isBenchmark);
  const top = [...benchmark]
    .sort((a, b) => (b.meta?.successScore || 0) - (a.meta?.successScore || 0))
    .slice(0, 10)
    .map((x) => ({
      jobId: x.jobId,
      title: x.meta?.uploader ? `@${x.meta.uploader}` : x.meta?.shortcode,
      url: x.meta?.url,
      likeCount: x.meta?.likeCount ?? null,
      commentCount: x.meta?.commentCount ?? null,
      successScore: x.meta?.successScore ?? null,
      overallScore: x.result.overallScore,
      verdict: x.result.verdict,
    }));

  const all = build(rows);
  res.json({
    ...all,
    benchmarkCount: benchmark.length,
    localCount: local.length,
    benchmark: build(benchmark),
    local: build(local),
    top,
  });
});

// GET /api/trend/jobs — recent jobs for the card view
router.get("/trend/jobs", (_req, res): void => {
  const list = [...jobs.values()]
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
    .map((j) => ({
      jobId: j.jobId,
      kind: j.kind,
      status: j.status,
      title: j.title,
      sourceType: j.sourceType,
      sourceUrl: j.sourceUrl,
      createdAt: j.createdAt,
      progress: j.progress,
      overall: j.result?.overall,
      overallScore: j.result?.overallScore,
      verdict: j.result?.verdict,
      durationSec: j.result?.durationSec,
    }));
  res.json({ jobs: list });
});

// GET /api/trend/analyze/:jobId — status + result
router.get("/trend/analyze/:jobId", (req, res): void => {
  const jobId = req.params.jobId;
  const job = jobs.get(jobId);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }
  const { dir: _dir, ...rest } = job;
  res.json(rest);
});

// GET /api/trend/analyze/:jobId/progress — SSE
router.get("/trend/analyze/:jobId/progress", (req, res): void => {
  const jobId = req.params.jobId;
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const send = (data: object) => res.write(`data: ${JSON.stringify(data)}\n\n`);
  const tick = () => {
    const job = jobs.get(jobId);
    if (!job) {
      send({ error: "Job not found" });
      clearInterval(timer);
      res.end();
      return;
    }
    send({ jobId, status: job.status, progress: job.progress, error: job.error });
    if (job.status === "completed" || job.status === "failed") {
      clearInterval(timer);
      res.end();
    }
  };
  const timer = setInterval(tick, 1000);
  tick();
  req.on("close", () => clearInterval(timer));
});

function streamFile(req: Request, res: Response, filePath: string, contentType: string): void {
  if (!fs.existsSync(filePath)) {
    res.status(404).json({ error: "File not found" });
    return;
  }
  const stat = fs.statSync(filePath);
  const range = req.headers.range;
  if (range) {
    const parts = range.replace(/bytes=/, "").split("-");
    const start = parseInt(parts[0], 10);
    const end = parts[1] ? parseInt(parts[1], 10) : stat.size - 1;
    res.writeHead(206, {
      "Content-Range": `bytes ${start}-${end}/${stat.size}`,
      "Accept-Ranges": "bytes",
      "Content-Length": end - start + 1,
      "Content-Type": contentType,
    });
    fs.createReadStream(filePath, { start, end }).pipe(res);
  } else {
    res.writeHead(200, {
      "Content-Length": stat.size,
      "Content-Type": contentType,
      "Accept-Ranges": "bytes",
    });
    fs.createReadStream(filePath).pipe(res);
  }
}

// GET /api/trend/media/:jobId — stream the analysed clip
router.get("/trend/media/:jobId", (req, res): void => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }
  streamFile(req, res, path.join(job.dir, "clip.mp4"), "video/mp4");
});

// GET /api/trend/frames/:jobId/:name — serve a rendered brain frame PNG
router.get("/trend/frames/:jobId/:name", (req, res): void => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }
  const name = path.basename(req.params.name);
  if (!/^frame_\d+\.png$/.test(name)) {
    res.status(400).json({ error: "Invalid frame name" });
    return;
  }
  streamFile(req, res, path.join(job.dir, name), "image/png");
});

// Register any previously-analysed benchmark reels so they survive restarts.
loadBenchmarkCorpus();

export default router;
