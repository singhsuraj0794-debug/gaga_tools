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
      const blob = await response.blob();
      const objectUrl = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(objectUrl);
      document.body.removeChild(a);
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
