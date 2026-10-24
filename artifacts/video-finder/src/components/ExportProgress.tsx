import { Button } from "@/components/ui/button";

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
  result?: {
    exported_products?: number;
    review_products?: number;
    taxonomy_gaps?: number;
    exported_by_level?: Record<string, number>;
    files?: { l2: string; rows: number }[];
  };
}

/** Live bar for the L2 template export (one workbook per L2 + a category agent
 *  pass, so it runs for minutes on a real batch). Stages come from the export
 *  process itself, so the label is what is actually happening. */
export function ExportProgress({
  progress,
  onCancel,
}: {
  progress: L2ExportProgress | null;
  onCancel?: () => void;
}) {
  if (!progress) return null;
  const percent = Math.max(0, Math.min(100, Math.round(progress.percent || 0)));
  const running = progress.state === "running";
  const failed = progress.state === "failed" || progress.state === "cancelled";
  const done = progress.state === "done";

  const barColor = failed ? "bg-red-500" : done ? "bg-green-600" : "bg-indigo-600";
  const counts =
    running && progress.total > 0 ? ` · ${progress.done}/${progress.total}` : "";

  return (
    <div className="w-full rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium text-slate-800">
          {done ? "Export complete" : failed ? "Export stopped" : progress.stage}
          {counts}
        </span>
        <span className="tabular-nums text-slate-600">{percent}%</span>
      </div>

      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-200">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="mt-1 flex items-start justify-between gap-3 text-xs text-slate-500">
        <span className="min-h-[1rem]">
          {failed
            ? (progress.error || "export failed").slice(0, 300)
            : done
              ? [
                  `${progress.result?.exported_products ?? 0} exported`,
                  `${progress.result?.review_products ?? 0} to review`,
                  progress.result?.taxonomy_gaps
                    ? `${progress.result.taxonomy_gaps} taxonomy gaps`
                    : "",
                ]
                  .filter(Boolean)
                  .join(" · ")
              : progress.detail || ""}
        </span>
        {running && onCancel ? (
          <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
    </div>
  );
}
