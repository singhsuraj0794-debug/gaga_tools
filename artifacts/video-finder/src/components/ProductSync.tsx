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
      const res = await fetch("/api/products/status");
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
      const res = await fetch("/api/products/sync-and-clean", {
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
      const res = await fetch("/api/products/duplicates", {
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
      const res = await fetch("/api/products/verify-duplicates", {
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
      const res = await fetch("/api/products/delete-duplicates", {
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
      const res = await fetch("/api/products/image-duplicates", {
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
      const res = await fetch("/api/products/delete-duplicates", {
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
      <Card className="border-indigo-200 bg-indigo-50/50">
        <CardHeader
          className="cursor-pointer select-none"
          onClick={() => setExpanded(!expanded)}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {expanded ? (
                <ChevronDown className="h-5 w-5 text-indigo-600" />
              ) : (
                <ChevronRight className="h-5 w-5 text-indigo-600" />
              )}
              <Database className="h-6 w-6 text-indigo-600" />
              <div>
                <CardTitle className="text-lg">Product Sync Manager</CardTitle>
                <p className="text-sm text-slate-500 font-normal">
                  {loading
                    ? "Loading..."
                    : supabaseCount !== null
                    ? `${supabaseCount} products in Supabase`
                    : "Unable to load status"}
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                fetchStatus();
              }}
              disabled={loading}
              className="h-8 text-xs"
            >
              <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        </CardHeader>

        {expanded && (
          <CardContent className="space-y-4 pt-0">
            <div className="bg-white rounded-lg border p-4">
              <p className="text-sm text-slate-600 mb-3">
                One-click sync: fetches live Gajab sitemaps, then:
              </p>
              <ul className="text-xs text-slate-500 space-y-1 mb-4 list-disc pl-4">
                <li>Imports new products from Gajab</li>
                <li>Deletes products no longer on Gajab</li>
                <li>Scrapes prices, brands &amp; categories for all products</li>
              </ul>
              <Button
                size="default"
                onClick={handleSync}
                disabled={syncing}
                className="w-full"
              >
                <RefreshCw className={`w-4 h-4 mr-2 ${syncing ? "animate-spin" : ""}`} />
                {syncing ? "Syncing & Cleaning..." : "Sync & Clean"}
              </Button>
            </div>

            {syncing && (
              <div className="bg-white rounded-lg border p-4 text-center">
                <RefreshCw className="w-6 h-6 mx-auto mb-2 text-indigo-500 animate-spin" />
                <p className="text-sm text-slate-600">Syncing Gajab products, scraping prices &amp; categories...</p>
                <p className="text-xs text-slate-400 mt-1">This may take 2-3 minutes</p>
              </div>
            )}

            {result && (
              <div className="bg-white rounded-lg border p-4 space-y-3">
                <h3 className="text-sm font-semibold flex items-center gap-2 text-green-600">
                  <CheckCircle2 className="h-4 w-4" />
                  Sync Complete
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                  <div className="text-center p-2 bg-indigo-50 rounded">
                    <p className="text-lg font-bold text-indigo-600">{result.gajab_active}</p>
                    <p className="text-[10px] text-slate-500">Active on Gajab</p>
                  </div>
                  <div className="text-center p-2 bg-blue-50 rounded">
                    <p className="text-lg font-bold text-blue-600">{result.supabase_before}</p>
                    <p className="text-[10px] text-slate-500">Before</p>
                  </div>
                  <div className="text-center p-2 bg-emerald-50 rounded">
                    <p className="text-lg font-bold text-emerald-600">{result.imported}</p>
                    <p className="text-[10px] text-slate-500">Imported</p>
                  </div>
                  <div className="text-center p-2 bg-violet-50 rounded">
                    <p className="text-lg font-bold text-violet-600">{result.enriched}</p>
                    <p className="text-[10px] text-slate-500">Enriched</p>
                  </div>
                  <div className="text-center p-2 bg-amber-50 rounded">
                    <p className="text-lg font-bold text-amber-600">{result.deleted}</p>
                    <p className="text-[10px] text-slate-500">Removed</p>
                  </div>
                  <div className="text-center p-2 bg-green-50 rounded">
                    <p className="text-lg font-bold text-green-600">{result.supabase_after}</p>
                    <p className="text-[10px] text-slate-500">After</p>
                  </div>
                </div>
                <p className="text-xs text-slate-500 pt-2 border-t">
                  {result.message}
                </p>
              </div>
            )}

            {error && (
              <div className="bg-white rounded-lg border border-red-200 p-4">
                <div className="flex items-start gap-2">
                  <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-medium text-red-600">Sync Failed</p>
                    <p className="text-xs text-red-500 mt-1">{error}</p>
                  </div>
                </div>
              </div>
            )}

            {!syncing && !result && !error && (
              <div className="text-center py-4 text-xs text-slate-400">
                Click "Sync & Clean" to compare with live Gajab products
              </div>
            )}
          </CardContent>
        )}
      </Card>

      <Card className="border-rose-200 bg-rose-50/50">
        <CardHeader>
          <div className="flex items-center gap-3">
            <Copy className="h-6 w-6 text-rose-600" />
            <div>
              <CardTitle className="text-lg">Duplicate Detection</CardTitle>
              <p className="text-sm text-slate-500 font-normal">
                {verifyResult
                  ? `${verifyResult.verified_duplicates} AI-verified duplicates in ${verifyResult.verified_groups.length} groups`
                  : dupResult
                  ? `${dupResult.total_groups} groups, ${dupResult.total_duplicate_products} potential duplicates`
                  : "Find products with same name, price & seller"}
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-0">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="default"
              onClick={findDuplicates}
              disabled={dupLoading || verifying}
              className="bg-rose-600 hover:bg-rose-700"
            >
              {dupLoading ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <Copy className="w-4 h-4 mr-2" />
              )}
              {dupLoading ? "Scanning..." : "Find Duplicates"}
            </Button>
            {dupResult && dupResult.total_groups > 0 && !verifyResult && (
              <Button
                variant="default"
                onClick={verifyDuplicates}
                disabled={verifying}
                className="bg-purple-600 hover:bg-purple-700"
              >
                {verifying ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <Brain className="w-4 h-4 mr-2" />
                )}
                {verifying ? "Verifying..." : "Verify with AI"}
              </Button>
            )}
            {dupResult && dupResult.total_groups > 0 && (
              <Button
                variant="outline"
                onClick={exportDuplicates}
                className="border-rose-300"
              >
                <Download className="w-4 h-4 mr-2" />
                Export CSV
              </Button>
            )}
            {verifyResult && verifyResult.verified_duplicates > 0 && !showConfirm && (
              <Button
                variant="destructive"
                onClick={() => setShowConfirm(true)}
              >
                <Trash2 className="w-4 h-4 mr-2" />
                Delete {verifyResult.verified_duplicates} Verified
              </Button>
            )}
          </div>

          {showConfirm && (
            <div className="bg-white rounded-lg border border-red-300 p-4 space-y-3">
              <div className="flex items-start gap-2">
                <AlertCircle className="h-5 w-5 text-red-500 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-red-700">Confirm Deletion</p>
                  <p className="text-xs text-slate-600 mt-1">
                    This will delete {verifyResult?.verified_duplicates} AI-verified duplicates,
                    keeping one copy of each. This action cannot be undone.
                  </p>
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowConfirm(false)}
                  disabled={deleting}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={deleteVerifiedDuplicates}
                  disabled={deleting}
                >
                  {deleting ? (
                    <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4 mr-1" />
                  )}
                  {deleting ? "Deleting..." : `Delete ${verifyResult?.verified_duplicates} Duplicates`}
                </Button>
              </div>
            </div>
          )}

          {deleteDone && (
            <div className="bg-white rounded-lg border border-green-200 p-3 flex items-center gap-2 text-sm text-green-700">
              <CheckCircle2 className="h-4 w-4" />
              Verified duplicates deleted successfully
            </div>
          )}

          {(dupLoading || verifying) && (
            <div className="bg-white rounded-lg border p-4 text-center">
              <Loader2 className="w-6 h-6 mx-auto mb-2 text-rose-500 animate-spin" />
              <p className="text-sm text-slate-600">
                {dupLoading ? "Scanning all products for duplicates..." : "Running DINOv2 & CLIP AI verification..."}
              </p>
              <p className="text-xs text-slate-400 mt-1">
                {dupLoading ? "Comparing names, prices & sellers" : "Checking images with DINOv2 and titles with CLIP"}
              </p>
            </div>
          )}

          {dupError && (
            <div className="bg-white rounded-lg border border-red-200 p-4">
              <div className="flex items-start gap-2">
                <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-red-600">Error</p>
                  <p className="text-xs text-red-500 mt-1">{dupError}</p>
                </div>
              </div>
            </div>
          )}

          {!dupLoading && !verifying && displayGroups.length === 0 && (
            <div className="bg-white rounded-lg border p-4 text-center text-sm text-slate-500">
              <CheckCircle2 className="w-6 h-6 mx-auto mb-2 text-green-500" />
              No duplicate products found
            </div>
          )}

          {displayGroups.length > 0 && (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {displayGroups.map((group, gi) => (
                <div key={gi} className="bg-white rounded-lg border p-3">
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex-1 min-w-0 mr-2">
                      <p className="text-sm font-medium text-slate-800 truncate">{group.name}</p>
                      <p className="text-xs text-slate-500">
                        {group.brand} &middot; {group.price} &middot; {group.products.length}x copies
                      </p>
                    </div>
                  </div>
                  <div className="space-y-1">
                    {group.products.map((p) => (
                      <div
                        key={p.id}
                        className={`flex items-center gap-2 text-xs rounded px-2 py-1 ${
                          p.verified_duplicate
                            ? "bg-red-50 text-red-700"
                            : "bg-slate-50 text-slate-600"
                        }`}
                      >
                        <span className="font-mono w-28 truncate shrink-0">{p.id}</span>
                        {p.image_url && (
                          <img src={p.image_url} alt="" className="w-6 h-6 rounded object-cover shrink-0" />
                        )}
                        <span className="truncate flex-1">{p.category || "—"}</span>
                        {p.dinov2_sim !== undefined && p.dinov2_sim !== null && (
                          <span className={`shrink-0 font-mono ${p.dinov2_sim >= 0.8 ? "text-green-600" : "text-slate-400"}`}>
                            D:{p.dinov2_sim.toFixed(2)}
                          </span>
                        )}
                        {p.clip_text_sim !== undefined && p.clip_text_sim !== null && (
                          <span className={`shrink-0 font-mono ${p.clip_text_sim >= 0.9 ? "text-green-600" : "text-slate-400"}`}>
                            C:{p.clip_text_sim.toFixed(2)}
                          </span>
                        )}
                        {p.verified_duplicate && (
                          <Trash2 className="w-3 h-3 text-red-500 shrink-0" />
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {!dupLoading && !verifying && !dupResult && !dupError && !deleteDone && (
            <div className="text-center py-4 text-xs text-slate-400">
              Click "Find Duplicates" to scan for products with the same name, price &amp; seller
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="border-amber-200 bg-amber-50/50">
        <CardHeader>
          <div className="flex items-center gap-3">
            <Image className="h-6 w-6 text-amber-600" />
            <div>
              <CardTitle className="text-lg">Image Duplicate Detection</CardTitle>
              <p className="text-sm text-slate-500 font-normal">
                {imgDupResult
                  ? `${imgDupResult.total_duplicate_products} image duplicates in ${imgDupResult.total_groups} groups`
                  : "Find products that look the same using DINOv2 vision AI"}
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-0">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="default"
              onClick={findImageDuplicates}
              disabled={imgDupLoading || imgDeleting}
              className="bg-amber-600 hover:bg-amber-700"
            >
              {imgDupLoading ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <Image className="w-4 h-4 mr-2" />
              )}
              {imgDupLoading ? "Analyzing Images..." : "Find by Image"}
            </Button>
            {imgDupResult && imgDupResult.total_groups > 0 && !imgDeleteDone && (
              <Button
                variant="destructive"
                onClick={() => setImgShowConfirm(true)}
                disabled={imgDeleting}
              >
                <Trash2 className="w-4 h-4 mr-2" />
                Delete {imgDupResult.total_duplicate_products} Duplicates
              </Button>
            )}
          </div>

          {imgShowConfirm && (
            <div className="bg-white rounded-lg border border-red-300 p-4 space-y-3">
              <div className="flex items-start gap-2">
                <AlertCircle className="h-5 w-5 text-red-500 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-red-700">Confirm Deletion</p>
                  <p className="text-xs text-slate-600 mt-1">
                    This will delete {imgDupResult?.total_duplicate_products} image-identified duplicates,
                    keeping one copy of each. This action cannot be undone.
                  </p>
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setImgShowConfirm(false)}
                  disabled={imgDeleting}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={deleteImageDuplicates}
                  disabled={imgDeleting}
                >
                  {imgDeleting ? (
                    <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4 mr-1" />
                  )}
                  {imgDeleting ? "Deleting..." : `Delete ${imgDupResult?.total_duplicate_products} Duplicates`}
                </Button>
              </div>
            </div>
          )}

          {imgDeleteDone && (
            <div className="bg-white rounded-lg border border-green-200 p-3 flex items-center gap-2 text-sm text-green-700">
              <CheckCircle2 className="h-4 w-4" />
              Image duplicates deleted successfully
            </div>
          )}

          {imgDupLoading && (
            <div className="bg-white rounded-lg border p-4 text-center">
              <Loader2 className="w-6 h-6 mx-auto mb-2 text-amber-500 animate-spin" />
              <p className="text-sm text-slate-600">
                Running DINOv2 vision AI on all product images...
              </p>
              <p className="text-xs text-slate-400 mt-1">
                Computing embeddings and comparing every pair. This may take several minutes.
              </p>
            </div>
          )}

          {imgDupError && (
            <div className="bg-white rounded-lg border border-red-200 p-4">
              <div className="flex items-start gap-2">
                <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium text-red-600">Error</p>
                  <p className="text-xs text-red-500 mt-1">{imgDupError}</p>
                </div>
              </div>
            </div>
          )}

          {!imgDupLoading && imgDupResult && imgDupResult.total_groups === 0 && !imgDeleteDone && (
            <div className="bg-white rounded-lg border p-4 text-center text-sm text-slate-500">
              <CheckCircle2 className="w-6 h-6 mx-auto mb-2 text-green-500" />
              No image duplicates found
            </div>
          )}

          {imgDupResult && imgDupResult.groups.length > 0 && (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {imgDupResult.groups.map((group, gi) => (
                <div key={gi} className="bg-white rounded-lg border p-3">
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex-1 min-w-0 mr-2">
                      <p className="text-sm font-medium text-slate-800 truncate">{group.name}</p>
                      <p className="text-xs text-slate-500">
                        {group.brand} &middot; {group.price} &middot; {group.count}x visually similar
                      </p>
                    </div>
                  </div>
                  <div className="flex gap-2 overflow-x-auto pb-1">
                    {group.products.map((p) => (
                      <div
                        key={p.id}
                        className="flex-shrink-0 w-24 bg-slate-50 rounded border p-1.5 text-center"
                      >
                        {p.image_url ? (
                          <img
                            src={p.image_url}
                            alt=""
                            className="w-full h-20 object-cover rounded mb-1"
                          />
                        ) : (
                          <div className="w-full h-20 bg-slate-200 rounded mb-1 flex items-center justify-center text-xs text-slate-400">
                            No img
                          </div>
                        )}
                        <p className="text-[10px] font-mono text-slate-500 truncate">{p.id}</p>
                        <p className="text-[10px] text-slate-400 truncate">{p.category || "—"}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {!imgDupLoading && !imgDupResult && !imgDupError && !imgDeleteDone && (
            <div className="text-center py-4 text-xs text-slate-400">
              Click "Find by Image" to scan all products for visual duplicates using DINOv2 AI
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
