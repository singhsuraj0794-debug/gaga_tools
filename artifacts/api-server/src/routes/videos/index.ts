import { Router, type IRouter } from "express";
import path from "path";
import fs from "fs";
import { execFile } from "child_process";
import { promisify } from "util";
import axios from "axios";
import {
  SearchVideosBody,
  DownloadVideoBody,
  GetDownloadStatusParams,
} from "@workspace/api-zod";
import { searchVideosForProducts } from "../../lib/videoSearch";
import {
  startDownload,
  getJob,
  listCompletedDownloads,
  isAllowedDownloadUrl,
} from "../../lib/downloadManager";

const execFileAsync = promisify(execFile);
const YT_DLP_PATH =
  process.env.YT_DLP_PATH ||
  "/home/runner/workspace/.pythonlibs/bin/yt-dlp";

const router: IRouter = Router();

router.post("/videos/search", async (req, res): Promise<void> => {
  const parsed = SearchVideosBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const { products, platforms } = parsed.data;

  // Validate API key availability and warn clearly
  const googleKey = process.env.GOOGLE_API_KEY || process.env.YOUTUBE_API_KEY;
  const rapidApiKey = process.env.RAPIDAPI_KEY;

  const warnings: string[] = [];
  if (platforms.includes("youtube") && !googleKey) {
    warnings.push("GOOGLE_API_KEY not set — YouTube will use scrape fallback");
  }
  if (
    (platforms.includes("instagram") ||
      platforms.includes("tiktok") ||
      platforms.includes("facebook")) &&
    !rapidApiKey
  ) {
    warnings.push(
      "RAPIDAPI_KEY not set — Instagram, TikTok, and Facebook search skipped",
    );
  }
  if (warnings.length > 0) {
    req.log.warn({ warnings }, "Missing API keys for video search");
  }

  try {
    const results = await searchVideosForProducts(
      products,
      platforms ?? ["youtube", "instagram", "tiktok"],
    );
    res.json({
      results,
      searchedProducts: products.map((p) => p.name),
      ...(warnings.length > 0 ? { warnings } : {}),
    });
  } catch (err: any) {
    req.log.error({ err }, "Video search failed");
    res.status(500).json({ error: "Video search failed. Please try again." });
  }
});

router.post("/videos/download", async (req, res): Promise<void> => {
  const parsed = DownloadVideoBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const { url, platform, title } = parsed.data;

  if (!isAllowedDownloadUrl(url)) {
    res.status(400).json({
      error: "URL not allowed. Only YouTube, Instagram, Facebook, and TikTok URLs are accepted.",
    });
    return;
  }

  const job = startDownload(url, title, platform);
  res.json(job);
});

router.get("/videos/downloads", async (_req, res): Promise<void> => {
  const downloads = listCompletedDownloads();
  res.json({ downloads });
});

router.get("/videos/downloads/:jobId/status", async (req, res): Promise<void> => {
  const rawId = Array.isArray(req.params.jobId) ? req.params.jobId[0] : req.params.jobId;
  const params = GetDownloadStatusParams.safeParse({ jobId: rawId });
  if (!params.success) {
    res.status(400).json({ error: "Invalid job ID" });
    return;
  }

  const job = getJob(params.data.jobId);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }

  res.json(job);
});

// SSE endpoint — streams live progress updates until job completes/fails
router.get("/videos/downloads/:jobId/progress", (req, res): void => {
  const jobId = Array.isArray(req.params.jobId)
    ? req.params.jobId[0]
    : req.params.jobId;

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no"); // disable nginx buffering if present
  res.flushHeaders();

  const send = (data: object) => {
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  const tick = () => {
    const job = getJob(jobId);
    if (!job) {
      send({ error: "Job not found" });
      clearInterval(timer);
      res.end();
      return;
    }
    send({
      jobId: job.jobId,
      status: job.status,
      progress: job.progress ?? 0,
      filePath: job.filePath,
      fileName: job.fileName,
      fileSize: job.fileSize,
      error: job.error,
    });
    if (job.status === "completed" || job.status === "failed") {
      clearInterval(timer);
      res.end();
    }
  };

  const timer = setInterval(tick, 500);
  tick(); // send immediately

  req.on("close", () => clearInterval(timer));
});

// Serve video inline for in-browser playback
router.get("/videos/downloads/:jobId/play", async (req, res): Promise<void> => {
  const rawId = Array.isArray(req.params.jobId) ? req.params.jobId[0] : req.params.jobId;
  const job = getJob(rawId);

  if (!job || !job.filePath || job.status !== "completed") {
    res.status(404).json({ error: "File not found or not yet downloaded" });
    return;
  }

  if (!fs.existsSync(job.filePath)) {
    res.status(404).json({ error: "File no longer exists on disk" });
    return;
  }

  const fileName = path.basename(job.filePath);
  const stat = fs.statSync(job.filePath);
  const fileSize = stat.size;

  const range = req.headers.range;
  if (range) {
    const parts = range.replace(/bytes=/, "").split("-");
    const start = parseInt(parts[0], 10);
    const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
    const chunkSize = end - start + 1;
    const fileStream = fs.createReadStream(job.filePath, { start, end });

    res.writeHead(206, {
      "Content-Range": `bytes ${start}-${end}/${fileSize}`,
      "Accept-Ranges": "bytes",
      "Content-Length": chunkSize,
      "Content-Type": "video/mp4",
      "Content-Disposition": `inline; filename="${fileName}"`,
    });
    fileStream.pipe(res);
  } else {
    res.writeHead(200, {
      "Content-Length": fileSize,
      "Content-Type": "video/mp4",
      "Content-Disposition": `inline; filename="${fileName}"`,
      "Accept-Ranges": "bytes",
    });
    fs.createReadStream(job.filePath).pipe(res);
  }
});

// Download video file (forces browser download)
router.get("/videos/file/:jobId", async (req, res): Promise<void> => {
  const rawId = Array.isArray(req.params.jobId) ? req.params.jobId[0] : req.params.jobId;
  const job = getJob(rawId);

  if (!job || !job.filePath || job.status !== "completed") {
    res.status(404).json({ error: "File not found or not yet downloaded" });
    return;
  }

  if (!fs.existsSync(job.filePath)) {
    res.status(404).json({ error: "File no longer exists on disk" });
    return;
  }

  const fileName = path.basename(job.filePath);
  res.setHeader("Content-Disposition", `attachment; filename="${fileName}"`);
  res.setHeader("Content-Type", "video/mp4");
  res.sendFile(job.filePath);
});

/**
 * GET /videos/preview?url=<encoded-video-url>
 *
 * Uses yt-dlp --get-url to resolve the direct CDN stream URL for a video
 * (TikTok, Instagram, etc.) and proxies the stream through this server so
 * the browser can play it in a <video> element without CORS issues.
 *
 * This avoids downloading the full file to disk — it's a real-time proxy.
 */
router.get("/videos/preview", async (req, res): Promise<void> => {
  const rawUrl = Array.isArray(req.query.url) ? req.query.url[0] : req.query.url;
  if (!rawUrl || typeof rawUrl !== "string") {
    res.status(400).json({ error: "url query parameter is required" });
    return;
  }

  if (!isAllowedDownloadUrl(rawUrl)) {
    res.status(400).json({ error: "URL not allowed for preview" });
    return;
  }

  try {
    // Ask yt-dlp to resolve the best direct stream URL (no download)
    const { stdout } = await execFileAsync(YT_DLP_PATH, [
      "--no-playlist",
      "--format",
      "best[ext=mp4]/best",
      "--get-url",
      rawUrl,
    ], { timeout: 20000 });

    const directUrl = stdout.trim().split("\n")[0];
    if (!directUrl || !directUrl.startsWith("http")) {
      res.status(502).json({ error: "Could not resolve direct stream URL" });
      return;
    }

    // Proxy the stream through this server
    const upstream = await axios.get(directUrl, {
      responseType: "stream",
      timeout: 10000,
      headers: {
        "User-Agent": "Mozilla/5.0",
        ...(req.headers.range ? { Range: req.headers.range } : {}),
      },
    });

    res.setHeader("Content-Type", String(upstream.headers["content-type"] || "video/mp4"));
    res.setHeader("Accept-Ranges", "bytes");
    if (upstream.headers["content-length"])
      res.setHeader("Content-Length", String(upstream.headers["content-length"]));
    if (upstream.headers["content-range"])
      res.setHeader("Content-Range", String(upstream.headers["content-range"]));

    res.status(upstream.status);
    upstream.data.pipe(res);

    req.on("close", () => upstream.data.destroy());
  } catch (err: any) {
    req.log.warn({ err: err?.message }, "Preview stream failed");
    res.status(502).json({ error: "Preview unavailable — try downloading instead" });
  }
});

export default router;
