import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Database,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Copy,
  Trash2,
  Download,
  Loader2,
  Brain,
  Image,
} from "lucide-react";
import { API_BASE } from "@/lib/api";

interface SyncResult {
  gajab_active: number;
  supabase_before: number;
  supabase_after: number;
  imported: number;
  enriched: number;
  deleted: number;
  message: string;
}

interface DuplicateProduct {
  id: string;
  url: string;
  image_url: string;
  category: string;
  mrp_price: string;
  verified_duplicate?: boolean;
  dinov2_sim?: number | null;
  clip_text_sim?: number | null;
}

interface DuplicateGroup {
  name: string;
  price: string;
  brand: string;
  count: number;
  products: DuplicateProduct[];
}

interface DuplicatesResult {
  total_groups: number;
  total_duplicate_products: number;
  groups: DuplicateGroup[];
}

interface VerifyResult {
  verified_groups: DuplicateGroup[];
  verified_duplicates: number;
  verified_total_pairs: number;
  to_delete_ids: string[];
}

export default function ProductSync() {
  const [supabaseCount, setSupabaseCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const [dupLoading, setDupLoading] = useState(false);
  const [dupResult, setDupResult] = useState<DuplicatesResult | null>(null);
  const [dupError, setDupError] = useState<string | null>(null);

  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [deleteDone, setDeleteDone] = useState(false);

  const [imgDupLoading, setImgDupLoading] = useState(false);
  const [imgDupResult, setImgDupResult] = useState<DuplicatesResult | null>(null);
  const [imgDupError, setImgDupError] = useState<string | null>(null);
  const [imgDeleting, setImgDeleting] = useState(false);
  const [imgShowConfirm, setImgShowConfirm] = useState(false);
  const [imgDeleteDone, setImgDeleteDone] = useState(false);

  useEffect(() => {
    fetchStatus();
  }, []);

  async function fetchStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/products/status`);
      if (res.ok) {
        const data = await res.json();
        setSupabaseCount(data.supabase_total);
      }
    } catch {
    } finally {
      setLoading(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    setResult(null);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/products/sync-and-clean`, {
        method: "POST",
        signal: AbortSignal.timeout(300000),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text.slice(0, 500));
      }
      const data: SyncResult = await res.json();
      setResult(data);
      setSupabaseCount(data.supabase_after);
    } catch (err: any) {
      setError(err.message || "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  async function findDuplicates() {
    setDupLoading(true);
    setDupResult(null);
    setDupError(null);
    setVerifyResult(null);
    setDeleteDone(false);
    try {
      const res = await fetch(`${API_BASE}/api/products/duplicates`, {
        signal: AbortSignal.timeout(120000),
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 200));
      const data: DuplicatesResult = await res.json();
      setDupResult(data);
    } catch (err: any) {
      setDupError(err.message || "Failed to find duplicates");
    } finally {
      setDupLoading(false);
    }
  }

  async function verifyDuplicates() {
    if (!dupResult) return;
    setVerifying(true);
    setVerifyResult(null);
    setDupError(null);
    try {
      const res = await fetch(`${API_BASE}/api/products/verify-duplicates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(dupResult.groups),
        signal: AbortSignal.timeout(360000),
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 200));
      const data: VerifyResult = await res.json();
      setVerifyResult(data);
    } catch (err: any) {
      setDupError(err.message || "AI verification failed");
    } finally {
      setVerifying(false);
    }
  }

  async function deleteVerifiedDuplicates() {
    if (!verifyResult) return;
    setDeleting(true);
    setDupError(null);
    try {
      const res = await fetch(`${API_BASE}/api/products/delete-duplicates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: verifyResult.to_delete_ids }),
        signal: AbortSignal.timeout(60000),
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 200));
      const data = await res.json();
      setDeleteDone(true);
      setShowConfirm(false);
      setSupabaseCount((prev) => (prev !== null ? prev - data.deleted : null));
      setDupResult(null);
      setVerifyResult(null);
    } catch (err: any) {
      setDupError(err.message || "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  function exportDuplicates() {
    const a = document.createElement("a");
    a.href = "/api/products/export-duplicates";
    a.download = "duplicate-products.csv";
    a.click();
  }

  async function findImageDuplicates() {
    setImgDupLoading(true);
    setImgDupResult(null);
    setImgDupError(null);
    setImgDeleteDone(false);
    try {
      const res = await fetch(`${API_BASE}/api/products/image-duplicates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
        signal: AbortSignal.timeout(600000),
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 500));
      const data: DuplicatesResult = await res.json();
      setImgDupResult(data);
    } catch (err: any) {
      setImgDupError(err.message || "Image duplicate detection failed");
    } finally {
      setImgDupLoading(false);
    }
  }

  async function deleteImageDuplicates() {
    if (!imgDupResult) return;
    setImgDeleting(true);
    setImgDupError(null);
    try {
      const allIds = imgDupResult.groups.flatMap((g) =>
        g.products.slice(1).map((p) => p.id)
      );
      if (allIds.length === 0) return;
      const res = await fetch(`${API_BASE}/api/products/delete-duplicates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: allIds }),
        signal: AbortSignal.timeout(60000),
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 200));
      const data = await res.json();
      setImgDeleteDone(true);
      setImgShowConfirm(false);
      setSupabaseCount((prev) => (prev !== null ? prev - data.deleted : null));
      setImgDupResult(null);
    } catch (err: any) {
      setImgDupError(err.message || "Delete failed");
    } finally {
      setImgDeleting(false);
    }
  }

  const displayGroups = verifyResult?.verified_groups ?? dupResult?.groups ?? [];

  return (
    <div className="space-y-4">
      <Card className="border-slate-200 bg-white/80">
        <CardHeader
          className="cursor-pointer select-none pb-3"
          onClick={() => setExpanded(!expanded)}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {expanded ? (
                <ChevronDown className="h-5 w-5 text-slate-600" />
              ) : (
                <ChevronRight className="h-5 w-5 text-slate-600" />
              )}
              <Database className="h-6 w-6 text-slate-600" />
              <div>
                <CardTitle className="text-lg">Database Management</CardTitle>
                <p className="text-sm text-slate-500 font-normal">
                  {loading
                    ? "Loading..."
                    : supabaseCount !== null
                    ? `${supabaseCount} products · Sync, find duplicates & detect image matches`
                    : "Unable to load status"}
                </p>
              </div>
            </div>
          </div>
        </CardHeader>

        {expanded && (
          <CardContent className="space-y-6 pt-2">

            {/* ── Product Sync ── */}
            <div className="border rounded-lg p-4 bg-indigo-50/30">
              <div className="flex items-center gap-2 mb-3">
                <RefreshCw className="h-5 w-5 text-indigo-600" />
                <h3 className="text-sm font-semibold text-indigo-800">Product Sync</h3>
              </div>
              <p className="text-xs text-slate-600 mb-3">
                One-click sync: fetches live Gajab sitemaps — imports new products, removes inactive, enriches data.
              </p>
              <Button
                size="sm"
                onClick={handleSync}
                disabled={syncing}
                className="bg-indigo-600 hover:bg-indigo-700"
              >
                <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${syncing ? "animate-spin" : ""}`} />
                {syncing ? "Syncing..." : "Sync & Clean"}
              </Button>

              {syncing && (
                <div className="mt-3 text-xs text-slate-500">
                  <RefreshCw className="w-4 h-4 inline mr-1 animate-spin" />
                  Syncing Gajab products, scraping prices & categories...
                </div>
              )}

              {result && (
                <div className="mt-3 bg-white rounded border p-3 text-xs space-y-2">
                  <p className="font-semibold text-green-600 flex items-center gap-1">
                    <CheckCircle2 className="h-3.5 w-3.5" /> Sync Complete
                  </p>
                  <div className="grid grid-cols-3 gap-2">
                    <div><span className="font-bold text-indigo-600">{result.imported}</span> imported</div>
                    <div><span className="font-bold text-violet-600">{result.enriched}</span> enriched</div>
                    <div><span className="font-bold text-amber-600">{result.deleted}</span> removed</div>
                  </div>
                </div>
              )}

              {error && (
                <div className="mt-3 bg-red-50 border border-red-200 rounded p-2 text-xs text-red-600 flex items-center gap-1">
                  <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {error}
                </div>
              )}
            </div>

            {/* ── Duplicate Detection ── */}
            <div className="border rounded-lg p-4 bg-rose-50/30">
              <div className="flex items-center gap-2 mb-3">
                <Copy className="h-5 w-5 text-rose-600" />
                <h3 className="text-sm font-semibold text-rose-800">Duplicate Detection</h3>
                <span className="text-[10px] text-slate-400 ml-auto">
                  {verifyResult
                    ? `${verifyResult.verified_duplicates} verified`
                    : dupResult
                    ? `${dupResult.total_duplicate_products} potential`
                    : ""}
                </span>
              </div>

              <div className="flex flex-wrap gap-1.5 mb-3">
                <Button
                  size="sm"
                  variant="default"
                  onClick={findDuplicates}
                  disabled={dupLoading || verifying}
                  className="bg-rose-600 hover:bg-rose-700 h-7 text-xs"
                >
                  {dupLoading ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <Copy className="w-3 h-3 mr-1" />}
                  {dupLoading ? "Scanning..." : "Find Duplicates"}
                </Button>
                {dupResult && dupResult.total_groups > 0 && !verifyResult && (
                  <Button
                    size="sm"
                    variant="default"
                    onClick={verifyDuplicates}
                    disabled={verifying}
                    className="bg-purple-600 hover:bg-purple-700 h-7 text-xs"
                  >
                    {verifying ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <Brain className="w-3 h-3 mr-1" />}
                    {verifying ? "Verifying..." : "Verify with AI"}
                  </Button>
                )}
                {dupResult && dupResult.total_groups > 0 && (
                  <Button size="sm" variant="outline" onClick={exportDuplicates} className="h-7 text-xs border-rose-300">
                    <Download className="w-3 h-3 mr-1" /> CSV
                  </Button>
                )}
                {verifyResult && verifyResult.verified_duplicates > 0 && !showConfirm && (
                  <Button size="sm" variant="destructive" onClick={() => setShowConfirm(true)} className="h-7 text-xs">
                    <Trash2 className="w-3 h-3 mr-1" />
                    Delete {verifyResult.verified_duplicates}
                  </Button>
                )}
              </div>

              {showConfirm && (
                <div className="bg-white rounded border border-red-300 p-3 text-xs space-y-2 mb-3">
                  <div className="flex items-start gap-1.5">
                    <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                    <div>
                      <p className="font-medium text-red-700">Confirm deletion of {verifyResult?.verified_duplicates} duplicates?</p>
                      <p className="text-slate-500 mt-0.5">This cannot be undone.</p>
                    </div>
                  </div>
                  <div className="flex gap-2 justify-end">
                    <Button size="sm" variant="outline" onClick={() => setShowConfirm(false)} disabled={deleting}>Cancel</Button>
                    <Button size="sm" variant="destructive" onClick={deleteVerifiedDuplicates} disabled={deleting}>
                      {deleting ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : null}
                      {deleting ? "Deleting..." : "Delete"}
                    </Button>
                  </div>
                </div>
              )}

              {deleteDone && (
                <div className="bg-green-50 border border-green-200 rounded p-2 text-xs text-green-700 flex items-center gap-1 mb-3">
                  <CheckCircle2 className="h-3.5 w-3.5" /> Verified duplicates deleted
                </div>
              )}

              {(dupLoading || verifying) && (
                <div className="text-xs text-slate-500 mb-3">
                  <Loader2 className="w-3.5 h-3.5 inline mr-1 animate-spin" />
                  {dupLoading ? "Scanning products..." : "Running DINOv2 & CLIP verification..."}
                </div>
              )}

              {dupError && (
                <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-600 flex items-center gap-1 mb-3">
                  <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {dupError}
                </div>
              )}

              {displayGroups.length > 0 && (
                <div className="space-y-1.5 max-h-60 overflow-y-auto">
                  {displayGroups.map((group, gi) => (
                    <div key={gi} className="bg-white rounded border p-2 text-xs">
                      <p className="font-medium text-slate-800 truncate">{group.name}</p>
                      <p className="text-[10px] text-slate-500 mb-1">{group.brand} · {group.price} · {group.products.length}x</p>
                      <div className="flex flex-wrap gap-1">
                        {group.products.map((p) => (
                          <span key={p.id} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] ${p.verified_duplicate ? "bg-red-50 text-red-700" : "bg-slate-50 text-slate-600"}`}>
                            <span className="font-mono truncate max-w-[80px]">{p.id}</span>
                            {p.dinov2_sim != null && <span className="text-green-600">D:{p.dinov2_sim.toFixed(2)}</span>}
                            {p.clip_text_sim != null && <span className="text-blue-600">C:{p.clip_text_sim.toFixed(2)}</span>}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {!dupLoading && !verifying && !dupResult && !dupError && !deleteDone && !verifyResult && (
                <p className="text-xs text-slate-400 text-center py-2">Click "Find Duplicates" to scan for products with the same name, price & seller</p>
              )}
              {!dupLoading && !verifying && displayGroups.length === 0 && dupResult && (
                <p className="text-xs text-green-600 text-center py-2 flex items-center justify-center gap-1">
                  <CheckCircle2 className="h-3.5 w-3.5" /> No duplicate products found
                </p>
              )}
            </div>

            {/* ── Image Duplicate Detection ── */}
            <div className="border rounded-lg p-4 bg-amber-50/30">
              <div className="flex items-center gap-2 mb-3">
                <Image className="h-5 w-5 text-amber-600" />
                <h3 className="text-sm font-semibold text-amber-800">Image Duplicate Detection</h3>
                <span className="text-[10px] text-slate-400 ml-auto">
                  {imgDupResult ? `${imgDupResult.total_duplicate_products} found` : ""}
                </span>
              </div>

              <div className="flex flex-wrap gap-1.5 mb-3">
                <Button
                  size="sm"
                  variant="default"
                  onClick={findImageDuplicates}
                  disabled={imgDupLoading || imgDeleting}
                  className="bg-amber-600 hover:bg-amber-700 h-7 text-xs"
                >
                  {imgDupLoading ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <Image className="w-3 h-3 mr-1" />}
                  {imgDupLoading ? "Analyzing..." : "Find by Image"}
                </Button>
                {imgDupResult && imgDupResult.total_groups > 0 && !imgDeleteDone && (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setImgShowConfirm(true)}
                    disabled={imgDeleting}
                    className="h-7 text-xs"
                  >
                    <Trash2 className="w-3 h-3 mr-1" />
                    Delete {imgDupResult.total_duplicate_products}
                  </Button>
                )}
              </div>

              {imgShowConfirm && (
                <div className="bg-white rounded border border-red-300 p-3 text-xs space-y-2 mb-3">
                  <div className="flex items-start gap-1.5">
                    <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                    <div>
                      <p className="font-medium text-red-700">Confirm deletion of {imgDupResult?.total_duplicate_products} image duplicates?</p>
                      <p className="text-slate-500 mt-0.5">This cannot be undone.</p>
                    </div>
                  </div>
                  <div className="flex gap-2 justify-end">
                    <Button size="sm" variant="outline" onClick={() => setImgShowConfirm(false)} disabled={imgDeleting}>Cancel</Button>
                    <Button size="sm" variant="destructive" onClick={deleteImageDuplicates} disabled={imgDeleting}>
                      {imgDeleting ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : null}
                      {imgDeleting ? "Deleting..." : "Delete"}
                    </Button>
                  </div>
                </div>
              )}

              {imgDeleteDone && (
                <div className="bg-green-50 border border-green-200 rounded p-2 text-xs text-green-700 flex items-center gap-1 mb-3">
                  <CheckCircle2 className="h-3.5 w-3.5" /> Image duplicates deleted
                </div>
              )}

              {imgDupLoading && (
                <div className="text-xs text-slate-500 mb-3">
                  <Loader2 className="w-3.5 h-3.5 inline mr-1 animate-spin" />
                  Running DINOv2 vision AI on all product images...
                </div>
              )}

              {imgDupError && (
                <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-600 flex items-center gap-1 mb-3">
                  <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {imgDupError}
                </div>
              )}

              {imgDupResult && imgDupResult.groups.length > 0 && (
                <div className="space-y-1.5 max-h-60 overflow-y-auto">
                  {imgDupResult.groups.map((group, gi) => (
                    <div key={gi} className="bg-white rounded border p-2 text-xs">
                      <p className="font-medium text-slate-800 truncate">{group.name}</p>
                      <p className="text-[10px] text-slate-500 mb-1">{group.brand} · {group.price} · {group.count}x visually similar</p>
                      <div className="flex gap-1.5 overflow-x-auto pb-1">
                        {group.products.map((p) => (
                          <div key={p.id} className="flex-shrink-0 w-16 text-center">
                            {p.image_url ? (
                              <img src={p.image_url} alt="" className="w-full h-14 object-cover rounded border" />
                            ) : (
                              <div className="w-full h-14 bg-slate-100 rounded flex items-center justify-center text-[10px] text-slate-400">—</div>
                            )}
                            <p className="text-[9px] font-mono text-slate-400 truncate">{p.id}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {!imgDupLoading && imgDupResult && imgDupResult.total_groups === 0 && !imgDeleteDone && (
                <p className="text-xs text-green-600 text-center py-2 flex items-center justify-center gap-1">
                  <CheckCircle2 className="h-3.5 w-3.5" /> No image duplicates found
                </p>
              )}

              {!imgDupLoading && !imgDupResult && !imgDupError && !imgDeleteDone && (
                <p className="text-xs text-slate-400 text-center py-2">Click "Find by Image" to scan for visual duplicates using DINOv2 AI</p>
              )}
            </div>

          </CardContent>
        )}
      </Card>
    </div>
  );
}
