import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { withApiHeaders } from "@/lib/listingValidator";

interface EnrichStatus {
  pending?: number;
  running?: number;
  done?: number;
  failed?: number;
}

/**
 * Background enrichment (category + attributes) runs at 2 workers while you
 * scrape, so it never slows the browsers. This is its own progress, independent
 * of any scrape or export: queue depth, current batch and failures.
 *
 * Hidden when the queue is empty so it never gets in the way.
 */
export function EnrichmentProgress() {
  const [status, setStatus] = useState<EnrichStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/scraper/enrich/status`, withApiHeaders());
        if (r.ok) {
          const j = (await r.json()) as EnrichStatus;
          if (alive) setStatus(j);
        }
      } catch {
        /* ignore - the worker is optional */
      }
    };
    void load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const pending = (status?.pending ?? 0) + (status?.running ?? 0);
  const done = status?.done ?? 0;
  const failed = status?.failed ?? 0;
  if (!status || pending === 0) return null;

  const total = pending + done + failed;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <div className="fixed bottom-4 right-4 z-40 w-72 rounded-md border border-slate-300 bg-white p-3 text-xs shadow-lg">
      <div className="flex items-center justify-between">
        <span className="font-medium text-slate-700">Enrichment (background)</span>
        <span className="tabular-nums text-slate-500">{pct}%</span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-full rounded-full bg-emerald-600 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-slate-500">
        <span>
          {pending} queued · {done} done
          {failed ? ` · ${failed} failed` : ""}
        </span>
        <span>CPU-friendly, GPU-only</span>
      </div>
    </div>
  );
}
