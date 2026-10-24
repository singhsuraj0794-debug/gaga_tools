import { API_BASE } from "@/lib/api";
import { withApiHeaders } from "@/lib/listingValidator";

/** Render restarts the instance on every deploy and returns 502/503 while it
 *  is coming up. Those are transient, so they are worth one retry before we
 *  tell the user the export failed. */
const RETRYABLE = new Set([502, 503, 504]);

export class ExportError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ExportError";
  }
}


/** Hand a blob to the browser as a download. */
function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(objectUrl);
  document.body.removeChild(a);
}

export interface L2ExportProgress {
  jobId: string;
  state: "running" | "done" | "failed" | "cancelled" | string;
  stage: string;
  done: number;
  total: number;
  percent: number;
  detail?: string;
  error?: string;
  ready?: boolean;
  result?: any;
}

/** Kick off the L2 export as a background job; it keeps running if the tab is
 *  closed, and progress is read from the export process itself. */
export async function startL2Export(
  platform: string,
  products: unknown[],
): Promise<{ jobId: string; total: number }> {
  const response = await fetch(`${API_BASE}/api/scraper/export-l2/start`, withApiHeaders({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ products, platform }),
  }));
  if (!response.ok) {
    const detail = (await response.text().catch(() => "")).slice(0, 300);
    throw new ExportError(detail || response.statusText, response.status);
  }
  return (await response.json()) as { jobId: string; total: number };
}

export async function getL2ExportProgress(jobId: string): Promise<L2ExportProgress> {
  const response = await fetch(`${API_BASE}/api/scraper/export-l2/${jobId}`, withApiHeaders());
  if (!response.ok) throw new ExportError("progress unavailable", response.status);
  return (await response.json()) as L2ExportProgress;
}

export async function cancelL2Export(jobId: string): Promise<void> {
  await fetch(`${API_BASE}/api/scraper/export-l2/${jobId}/cancel`, withApiHeaders({ method: "POST" }))
    .catch(() => undefined);
}

/**
 * Export by L2 listing template, with live progress.
 *
 * Starts the job, polls the server (~1.5s), and downloads the finished zip.
 * `onProgress` receives every update, starting with the queued state.
 */
export async function exportL2WithProgress(
  platform: string,
  products: unknown[],
  filename: string,
  onProgress: (p: L2ExportProgress) => void,
  pollMs = 1500,
): Promise<void> {
  onProgress({
    jobId: "", state: "running", stage: "Starting", percent: 0,
    done: 0, total: products.length, detail: "queued",
  });

  const { jobId, total } = await startL2Export(platform, products);
  let tick = 0;
  for (;;) {
    await new Promise((r) => setTimeout(r, pollMs));
    tick += 1;
    let p: L2ExportProgress;
    try {
      p = await getL2ExportProgress(jobId);
    } catch (err) {
      // transient network blips should not abort a long export
      if (tick > 20) throw err;
      continue;
    }
    onProgress({ ...p, total: p.total || total });

    if (p.state === "done") {
      const response = await fetch(
        `${API_BASE}/api/scraper/export-l2/${jobId}/download?name=${encodeURIComponent(filename)}`,
        withApiHeaders(),
      );
      if (!response.ok) throw new ExportError("export finished but download failed", response.status);
      saveBlob(await response.blob(), filename);
      return;
    }
    if (p.state === "failed" || p.state === "cancelled") {
      throw new ExportError(p.error || `export ${p.state}`, 0);
    }
  }
}

/**
 * POST a scraped batch to `/api/scraper/<platform>/export` and hand the
 * resulting workbook to the browser. Throws an ExportError carrying the
 * server's own message when the request genuinely fails.
 */
export async function exportGajabWorkbook(
  platform: string,
  products: unknown[],
  filename: string,
): Promise<void> {
  const url = `${API_BASE}/api/scraper/${platform}/export`;
  let lastError: ExportError | null = null;

  for (let attempt = 0; attempt < 2; attempt++) {
    let response: Response;
    try {
      response = await fetch(
        url,
        withApiHeaders({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ products, platform }),
        }),
      );
    } catch (err) {
      throw new ExportError(
        err instanceof Error ? err.message : String(err),
        0,
      );
    }

    if (response.ok) {
      saveBlob(await response.blob(), filename);
      return;
    }

    let detail = "";
    try {
      detail = (await response.text()).trim();
    } catch {
      /* ignore */
    }
    lastError = new ExportError(
      detail.slice(0, 400) || response.statusText || "no response body",
      response.status,
    );

    if (!RETRYABLE.has(response.status) || attempt === 1) break;
    await new Promise((r) => setTimeout(r, 4000));
  }

  throw lastError ?? new ExportError("Export failed", 0);
}
