import { useCallback, useRef, useState } from "react";
import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getApiBase } from "@/lib/api";
import { withApiHeaders } from "@/lib/listingValidator";
import {
  ArrowLeft, Upload, FileSpreadsheet, Loader2, CheckCircle2, AlertTriangle, Download, Image as ImageIcon,
} from "lucide-react";

interface Progress {
  state: "idle" | "running" | "done" | "failed";
  done?: number; total?: number; current?: string; fixed?: number; clean?: number;
}

export default function ImageVerification() {
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"word" | "panel" | "crop" | "auto">("panel");
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress>({ state: "idle" });
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = null;
  };

  const start = useCallback(async () => {
    if (!file) return;
    setError(null);
    setProgress({ state: "running" });
    const form = new FormData();
    form.append("file", file);
    form.append("mode", mode);
    try {
      const res = await fetch(`${getApiBase()}/api/products/image-verify/start`, withApiHeaders({
        method: "POST",
        body: form,
      }));
      const data = await res.json();
      if (!res.ok || !data.jobId) throw new Error(data.error || "Could not start the job");
      const id = data.jobId as string;
      setJobId(id);
      pollRef.current = window.setInterval(async () => {
        try {
          const r = await fetch(`${getApiBase()}/api/products/image-verify/${id}`, withApiHeaders());
          const p = (await r.json()) as Progress;
          setProgress(p);
          if (p.state === "done" || p.state === "failed") stopPolling();
        } catch {
          /* transient */
        }
      }, 2000);
    } catch (e: any) {
      setError(e?.message || String(e));
      setProgress({ state: "failed" });
    }
  }, [file, mode]);

  const download = () => {
    if (!jobId) return;
    window.open(`${getApiBase()}/api/products/image-verify/${jobId}/download`, "_blank");
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-5xl mx-auto px-6 py-12">
        <Link href="/">
          <Button variant="ghost" size="sm" className="mb-6">
            <ArrowLeft className="h-4 w-4 mr-2" /> Back to Gajab Hub
          </Button>
        </Link>

        <div className="flex items-center gap-3 mb-2">
          <div className="p-2 rounded-lg bg-cyan-100">
            <ImageIcon className="h-6 w-6 text-cyan-700" />
          </div>
          <h1 className="text-3xl font-bold text-slate-900">Image Verification</h1>
        </div>
        <p className="text-slate-600 mb-8 max-w-3xl">
          Upload a sheet with image links. Each image is OCR-scanned for operations claims
          (<em>best seller, free delivery, COD, % off, sale, warranty…</em>), the claim areas are
          content-aware inpainted, the cleaned image is re-uploaded to imgbb and the URL is
          rewritten in the same sheet. Packaging text such as MRP / Net Qty / Made in India is left alone.
        </p>

        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">
              <FileSpreadsheet className="h-5 w-5 text-cyan-600" /> Source sheet
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="flex items-center gap-3 cursor-pointer rounded-lg border-2 border-dashed border-slate-300 bg-white px-4 py-6 hover:border-cyan-400">
              <Upload className="h-5 w-5 text-slate-400" />
              <span className="text-sm text-slate-600">
                {file ? file.name : "Choose an .xlsx or .csv (uses the Product Image 1…10 columns)"}
              </span>
              <input
                type="file"
                accept=".xlsx,.csv"
                className="hidden"
                onChange={(e) => {
                  setFile(e.target.files?.[0] || null);
                  setJobId(null);
                  setProgress({ state: "idle" });
                  setError(null);
                }}
              />
            </label>

            <div className="space-y-2">
              <div className="text-sm font-medium text-slate-700">Removal mode</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {([
                  ["panel", "Erase info panel", "Clears the designed copy block into the page background. Keeps the composition."],
                  ["crop", "Crop to product", "Keeps only the product — drops badges, icons, logo and copy. Best for infographics."],
                  ["auto", "Auto", "Crop when the product is a distinct object, otherwise erase the panel."],
                  ["word", "Words only", "Smallest change: masks just the claim words. Can damage typography."],
                ] as const).map(([value, title, desc]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setMode(value)}
                    className={`text-left rounded-lg border p-3 transition ${
                      mode === value ? "border-cyan-500 bg-cyan-50" : "border-slate-200 bg-white hover:border-slate-300"
                    }`}
                  >
                    <div className="text-sm font-medium text-slate-800">{title}</div>
                    <div className="text-xs text-slate-500 mt-0.5">{desc}</div>
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={start} disabled={!file || progress.state === "running"}>
                {progress.state === "running" ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : null}
                Verify images
              </Button>
              {progress.state === "done" && (
                <Button variant="outline" onClick={download}>
                  <Download className="h-4 w-4 mr-2" /> Download verified sheet
                </Button>
              )}
              {error && (
                <span className="text-sm text-rose-600 flex items-center gap-1">
                  <AlertTriangle className="h-4 w-4" /> {error}
                </span>
              )}
            </div>

            {progress.state !== "idle" && (
              <div className="rounded-lg border bg-white p-4 text-sm text-slate-700 space-y-1">
                <div className="flex items-center gap-2">
                  <Badge className={progress.state === "done" ? "bg-emerald-100 text-emerald-700" : "bg-cyan-100 text-cyan-700"}>
                    {progress.state}
                  </Badge>
                  <span>
                    {progress.done ?? 0}/{progress.total ?? "?"} images
                  </span>
                  {typeof progress.fixed === "number" && (
                    <span className="text-slate-500">· {progress.fixed} fixed · {progress.clean ?? 0} already clean</span>
                  )}
                </div>
                {progress.current && <div className="text-xs text-slate-500 truncate">current: {progress.current}</div>}
                {progress.state === "done" && (
                  <div className="text-emerald-700 flex items-center gap-1 text-xs">
                    <CheckCircle2 className="h-4 w-4" />
                    Done — the sheet now has <strong>Verified Image URL</strong>, <strong>Claims Removed</strong> and{" "}
                    <strong>Image Verify Status</strong> columns, and the image cells were rewritten in place.
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
