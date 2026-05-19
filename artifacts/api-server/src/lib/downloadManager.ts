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
  const safeTitle = sanitizeFilename(title || "video");
  const fileName = `${safeTitle}-${jobId.slice(0, 8)}.%(ext)s`;
  const outputTemplate = path.join(DOWNLOADS_DIR, fileName);

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

  // Find yt-dlp
  const ytDlpPath =
    process.env.YT_DLP_PATH ||
    "/home/runner/workspace/.pythonlibs/bin/yt-dlp";

  const args = [
    "--no-playlist",
    "--format",
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "--merge-output-format",
    "mp4",
    "--output",
    outputTemplate,
    "--newline",
    "--progress",
    url,
  ];

  logger.info({ jobId, url }, "Starting yt-dlp download");
  job.status = "downloading";

  const child = spawn(ytDlpPath, args, { stdio: ["ignore", "pipe", "pipe"] });

  // Track all destination files mentioned by yt-dlp (last one wins = merged file)
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
        const pct = Math.floor(parseFloat(progressMatch[1]));
        // Only go to 99 until close fires, so UI doesn't show 100 before file is ready
        job.progress = Math.min(pct, 99);
      }

      const destMatch = line.match(/\[download\] Destination: (.+)/);
      if (destMatch) lastDestination = destMatch[1].trim();

      // Merger output: final merged mp4 path
      const mergeMatch = line.match(/\[Merger\] Merging formats into "(.+)"/);
      if (mergeMatch) lastDestination = mergeMatch[1].trim();

      // ffmpeg output line also contains the final file
      const moveMatch = line.match(/\[download\] (.+\.mp4) has already been downloaded/);
      if (moveMatch) lastDestination = moveMatch[1].trim();
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    const text = chunk.toString().trim();
    // Only log real errors, not INFO/WARNING lines
    if (text && !text.startsWith("WARNING:") && !text.startsWith("[debug]")) {
      logger.warn({ jobId, stderr: text }, "yt-dlp stderr");
    }
  });

  child.on("close", (code) => {
    if (code === 0) {
      // Priority 1: use the last destination yt-dlp reported
      let finalPath = lastDestination;

      // Priority 2: scan downloads dir for the newest file created after job started
      if (!finalPath || !fs.existsSync(finalPath)) {
        try {
          const files = fs
            .readdirSync(DOWNLOADS_DIR)
            .map((f) => {
              const p = path.join(DOWNLOADS_DIR, f);
              const stat = fs.statSync(p);
              return { p, mtime: stat.mtimeMs };
            })
            .filter((f) => f.mtime >= startTime)
            .sort((a, b) => b.mtime - a.mtime);
          if (files.length > 0) finalPath = files[0].p;
        } catch {}
      }

      // Priority 3: newest file in downloads dir regardless of time
      if (!finalPath || !fs.existsSync(finalPath)) {
        try {
          const files = fs
            .readdirSync(DOWNLOADS_DIR)
            .map((f) => {
              const p = path.join(DOWNLOADS_DIR, f);
              return { p, mtime: fs.statSync(p).mtimeMs };
            })
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
        logger.info({ jobId, filePath: finalPath }, "Download completed");
      } else {
        job.status = "failed";
        job.error = "File not found after download";
        logger.warn({ jobId }, "Download completed but file not found");
      }
    } else {
      job.status = "failed";
      job.error = `yt-dlp exited with code ${code}`;
      logger.error({ jobId, code }, "Download failed");
    }
  });

  child.on("error", (err) => {
    job.status = "failed";
    job.error = err.message;
    logger.error({ jobId, err }, "Failed to spawn yt-dlp");
  });

  return job;
}
