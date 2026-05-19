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

export function getJob(jobId: string): DownloadJob | undefined {
  return jobs.get(jobId);
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

  let resolvedFileName: string | null = null;

  child.stdout.on("data", (chunk: Buffer) => {
    const line = chunk.toString();

    // Parse progress percentage
    const progressMatch = line.match(/(\d+\.?\d*)%/);
    if (progressMatch) {
      job.progress = Math.floor(parseFloat(progressMatch[1]));
    }

    // Extract filename from output
    const destMatch = line.match(/\[download\] Destination: (.+)/);
    if (destMatch) {
      resolvedFileName = destMatch[1].trim();
    }
    const mergeMatch = line.match(/\[Merger\] Merging formats into "(.+)"/);
    if (mergeMatch) {
      resolvedFileName = mergeMatch[1].trim();
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    logger.warn({ jobId, stderr: chunk.toString().trim() }, "yt-dlp stderr");
  });

  child.on("close", (code) => {
    if (code === 0) {
      // Find the actual downloaded file
      let finalPath = resolvedFileName;
      if (!finalPath) {
        // Scan downloads dir for newest file
        try {
          const files = fs.readdirSync(DOWNLOADS_DIR)
            .map((f) => ({ name: f, path: path.join(DOWNLOADS_DIR, f), mtime: fs.statSync(path.join(DOWNLOADS_DIR, f)).mtimeMs }))
            .sort((a, b) => b.mtime - a.mtime);
          if (files.length > 0) finalPath = files[0].path;
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
