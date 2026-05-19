import { Router, type IRouter } from "express";
import path from "path";
import fs from "fs";
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
} from "../../lib/downloadManager";

const router: IRouter = Router();

router.post("/videos/search", async (req, res): Promise<void> => {
  const parsed = SearchVideosBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const { products, platforms } = parsed.data;

  try {
    const results = await searchVideosForProducts(
      products,
      platforms ?? ["youtube", "instagram", "tiktok"],
    );
    res.json({
      results,
      searchedProducts: products.map((p) => p.name),
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

export default router;
