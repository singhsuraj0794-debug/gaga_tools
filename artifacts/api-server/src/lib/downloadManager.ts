import { spawn } from "child_process";
import path from "path";
import fs from "fs";
import { v4 as uuidv4 } from "uuid";
import { logger } from "./logger";

export type DownloadStatus = "pending" | "downloading" | "completed" | "failed";

export interface DownloadJob {
  jobId: string;
  status: DownloadStatus;
  url: string;
  title: string;
  platform: string | null;
  filePath: string | null;
  fileName: string | null;
  fileSize: number | null;
  progress: number | null;
  error: string | null;
  createdAt: string;
}

export interface DownloadedFile {
  jobId: string;
  fileName: string;
  filePath: string;
  title: string;
  platform: string;
  fileSize: number;
  createdAt: string;
}

const jobs = new Map<string, DownloadJob>();

const DOWNLOADS_DIR = path.resolve(process.cwd(), "downloads");

if (!fs.existsSync(DOWNLOADS_DIR)) {
  fs.mkdirSync(DOWNLOADS_DIR, { recursive: true });
}

function sanitizeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 80);
}

// Allowed domains for yt-dlp downloads — prevents SSRF / internal network abuse
const ALLOWED_DOWNLOAD_DOMAINS = new Set([
  "youtube.com",
  "www.youtube.com",
  "youtu.be",
  "m.youtube.com",
  "instagram.com",
  "www.instagram.com",
  "facebook.com",
  "www.facebook.com",
  "m.facebook.com",
  "fb.watch",
  "tiktok.com",
  "www.tiktok.com",
  "vm.tiktok.com",
  "vt.tiktok.com",
]);

export function isAllowedDownloadUrl(rawUrl: string): boolean {
  try {
    const { hostname } = new URL(rawUrl);
    // Allow exact match or subdomain (e.g. reel.facebook.com)
    for (const allowed of ALLOWED_DOWNLOAD_DOMAINS) {
      if (hostname === allowed || hostname.endsWith(`.${allowed}`)) return true;
    }
    return false;
  } catch {
    return false;
  }
}

export function getJob(jobId: string): DownloadJob | undefined {
  return jobs.get(jobId);
}

/**
 * Scan the downloads directory and register any files not already in the jobs
 * map.  Called once at server startup so that files from previous sessions are
 * immediately playable via /play without requiring a new download.
 */
export function loadExistingDownloads(): void {
  try {
    const files = fs.readdirSync(DOWNLOADS_DIR);
    const knownPaths = new Set(
      Array.from(jobs.values())
        .filter((j) => j.filePath)
        .map((j) => j.filePath!),
    );

    for (const file of files) {
      const filePath = path.join(DOWNLOADS_DIR, file);
      if (knownPaths.has(filePath)) continue;
      try {
        const stat = fs.statSync(filePath);
        if (!stat.isFile()) continue;
        const jobId = `file-${Buffer.from(file).toString("base64").slice(0, 8)}`;
        if (!jobs.has(jobId)) {
          jobs.set(jobId, {
            jobId,
            status: "completed",
            url: "",
            title: file.replace(/\.[^/.]+$/, "").replace(/_/g, " "),
            platform: "unknown",
            filePath,
            fileName: file,
            fileSize: stat.size,
            progress: 100,
            error: null,
            createdAt: stat.birthtime.toISOString(),
          });
        }
      } catch {
        // ignore unreadable files
      }
    }
  } catch {
    // downloads dir may not exist yet
  }
}

export function listCompletedDownloads(): DownloadedFile[] {
  const result: DownloadedFile[] = [];

  for (const job of jobs.values()) {
    if (job.status === "completed" && job.filePath && job.fileName) {
      try {
        const stat = fs.statSync(job.filePath);
        result.push({
          jobId: job.jobId,
          fileName: job.fileName,
          filePath: job.filePath,
          title: job.title,
          platform: job.platform || "unknown",
          fileSize: stat.size,
          createdAt: job.createdAt,
        });
      } catch {
        // file may have been deleted
      }
    }
  }

  // Also scan the downloads directory for any files not in memory
  try {
    const files = fs.readdirSync(DOWNLOADS_DIR);
    const knownPaths = new Set(
      Array.from(jobs.values())
        .filter((j) => j.filePath)
        .map((j) => j.filePath!),
    );

    for (const file of files) {
      const filePath = path.join(DOWNLOADS_DIR, file);
      if (knownPaths.has(filePath)) continue;
      try {
        const stat = fs.statSync(filePath);
        if (stat.isFile()) {
          const jobId = `file-${Buffer.from(file).toString("base64").slice(0, 8)}`;
          result.push({
            jobId,
            fileName: file,
            filePath,
            title: file.replace(/\.[^/.]+$/, "").replace(/_/g, " "),
            platform: "unknown",
            fileSize: stat.size,
            createdAt: stat.birthtime.toISOString(),
          });
        }
      } catch {
        // ignore
      }
    }
  } catch {
    // ignore
  }

  return result;
}

export function startDownload(
  url: string,
  title: string,
  platform: string | null,
): DownloadJob {
  const jobId = uuidv4();
  const job: DownloadJob = {
    jobId,
    status: "pending",
    url,
    title,
    platform,
    filePath: null,
    fileName: null,
    fileSize: null,
    progress: 0,
    error: null,
    createdAt: new Date().toISOString(),
  };
  jobs.set(jobId, job);

  // Route TikTok through RapidAPI (yt-dlp can't handle TikTok bot detection)
  if (platform === "tiktok" || url.includes("tiktok.com") || url.includes("vm.tiktok.com")) {
    startTikTokDownload(job, url, title);
  } else {
    startYtDlpDownload(job, url, title);
  }

  return job;
}

// ─── TikTok download via tiktok-download-video1 RapidAPI or yt-dlp ───────

async function startTikTokDownload(job: DownloadJob, url: string, title: string): Promise<void> {
  const rapidApiKey = process.env.RAPIDAPI_KEY;
  let rapidApiFailed = false;

  if (rapidApiKey) {
    job.status = "downloading";
    job.progress = 10;

    try {
      const apiUrl = `https://tiktok-download-video1.p.rapidapi.com/getVideo?url=${encodeURIComponent(url)}&hd=1`;
      const res = await fetch(apiUrl, {
        headers: {
          "x-rapidapi-host": "tiktok-download-video1.p.rapidapi.com",
          "x-rapidapi-key": rapidApiKey,
          "Content-Type": "application/json",
        },
        signal: AbortSignal.timeout(15000),
      });

      const text = await res.text();
      if (!text) throw new Error("Empty response from TikTok download API");
      const data = JSON.parse(text);

      if (data.code !== 0) throw new Error(data.msg || "TikTok API error");

      // Prefer HD no-watermark, fall back to regular play URL
      const videoUrl: string = data.data?.hdplay || data.data?.play || data.data?.wmplay;
      if (!videoUrl) throw new Error("No video URL in response");

      job.progress = 40;
      logger.info({ jobId: job.jobId, videoUrl: videoUrl.slice(0, 80) }, "Got TikTok CDN URL, downloading...");

      // Stream the video to disk
      const safeTitle = sanitizeFilename(title || "tiktok");
      const fileName = `${safeTitle}-${job.jobId.slice(0, 8)}.mp4`;
      const filePath = path.join(DOWNLOADS_DIR, fileName);

      const videoRes = await fetch(videoUrl, {
        headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" },
        signal: AbortSignal.timeout(60000),
      });

      if (!videoRes.ok) throw new Error(`CDN fetch failed: ${videoRes.status}`);

      const total = parseInt(videoRes.headers.get("content-length") || "0", 10);
      const fileStream = fs.createWriteStream(filePath);
      let received = 0;

      const reader = videoRes.body!.getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        fileStream.write(value);
        received += value.length;
        if (total) job.progress = Math.min(99, 40 + Math.floor((received / total) * 59));
      }

      await new Promise<void>((resolve, reject) => {
        fileStream.end();
        fileStream.on("finish", resolve);
        fileStream.on("error", reject);
      });

      const stat = fs.statSync(filePath);
      job.filePath = filePath;
      job.fileName = fileName;
      job.fileSize = stat.size;
      job.progress = 100;
      job.status = "completed";
      logger.info({ jobId: job.jobId, filePath }, "TikTok download completed via RapidAPI");
      return;
    } catch (err: any) {
      logger.warn({ jobId: job.jobId, err: err.message }, "TikTok RapidAPI download failed, falling back to yt-dlp");
      rapidApiFailed = true;
    }
  } else {
    rapidApiFailed = true;
  }

  if (rapidApiFailed) {
    // Fallback to yt-dlp for TikTok download
    logger.info({ jobId: job.jobId }, "TikTok download: using yt-dlp fallback");
    startYtDlpDownload(job, url, title);
  }
}

// ─── YouTube/generic download via yt-dlp ─────────────────────────────────

function startYtDlpDownload(job: DownloadJob, url: string, title: string): void {
  const safeTitle = sanitizeFilename(title || "video");
  const fileName = `${safeTitle}-${job.jobId.slice(0, 8)}.%(ext)s`;
  const outputTemplate = path.join(DOWNLOADS_DIR, fileName);

  // Find yt-dlp
  const ytDlpPath =
    process.env.YT_DLP_PATH ||
    "/Users/gajabmarketing/bin/yt-dlp";

  const args = [
    "--no-playlist",
    // Pick best pre-muxed mp4 (no ffmpeg needed), fall back to any pre-muxed
    "--format",
    "best[ext=mp4][vcodec!=none][acodec!=none]/best[ext=mp4]/best[vcodec!=none][acodec!=none]/best",
    "--output",
    outputTemplate,
    "--newline",
    "--progress",
    "--cookies-from-browser", "chrome",
    url,
  ];

  logger.info({ jobId: job.jobId, url }, "Starting yt-dlp download");
  job.status = "downloading";

  const child = spawn(ytDlpPath, args, { stdio: ["ignore", "pipe", "pipe"] });

  let lastDestination: string | null = null;
  const startTime = Date.now();
  let stdoutBuf = "";

  child.stdout.on("data", (chunk: Buffer) => {
    stdoutBuf += chunk.toString();
    const lines = stdoutBuf.split("\n");
    stdoutBuf = lines.pop() ?? "";

    for (const line of lines) {
      const progressMatch = line.match(/(\d+\.?\d*)%/);
      if (progressMatch) {
        job.progress = Math.min(Math.floor(parseFloat(progressMatch[1])), 99);
      }
      const destMatch = line.match(/\[download\] Destination: (.+)/);
      if (destMatch) lastDestination = destMatch[1].trim();
      const mergeMatch = line.match(/\[Merger\] Merging formats into "(.+)"/);
      if (mergeMatch) lastDestination = mergeMatch[1].trim();
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    const text = chunk.toString().trim();
    if (text) logger.warn({ jobId: job.jobId, stderr: text }, "yt-dlp stderr");
  });

  child.on("close", (code) => {
    if (code === 0) {
      let finalPath = lastDestination;

      if (!finalPath || !fs.existsSync(finalPath)) {
        try {
          const files = fs.readdirSync(DOWNLOADS_DIR)
            .map((f) => { const p = path.join(DOWNLOADS_DIR, f); return { p, mtime: fs.statSync(p).mtimeMs }; })
            .filter((f) => f.mtime >= startTime)
            .sort((a, b) => b.mtime - a.mtime);
          if (files.length > 0) finalPath = files[0].p;
        } catch {}
      }

      if (finalPath && fs.existsSync(finalPath)) {
        const stat = fs.statSync(finalPath);
        job.filePath = finalPath;
        job.fileName = path.basename(finalPath);
        job.fileSize = stat.size;
        job.progress = 100;
        job.status = "completed";
        logger.info({ jobId: job.jobId, filePath: finalPath }, "Download completed");
      } else {
        job.status = "failed";
        job.error = "File not found after download";
        logger.warn({ jobId: job.jobId }, "Download completed but file not found");
      }
    } else {
      job.status = "failed";
      job.error = `yt-dlp exited with code ${code}`;
      logger.error({ jobId: job.jobId, code }, "Download failed");
    }
  });

  child.on("error", (err) => {
    job.status = "failed";
    job.error = err.message;
    logger.error({ jobId: job.jobId, err }, "Failed to spawn yt-dlp");
  });
}
