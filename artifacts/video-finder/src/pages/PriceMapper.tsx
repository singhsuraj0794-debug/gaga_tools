import { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ArrowLeft,
  Search,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  BadgePercent,
  Loader2,
  ShoppingBag,
  ShoppingCart,
  Package,
  Save,
  Download,
  ChevronLeft,
  ChevronRight,
  Play,
  SlidersHorizontal,
} from "lucide-react";
import { Link } from "wouter";

interface GajabProduct {
  id: string;
  name: string;
  price: string | null;
  image_url: string | null;
  url: string;
  category: string | null;
}

interface PriceMapping {
  gajab_product_id: string;
  gajab_title: string;
  gajab_image_url: string | null;
  gajab_price: string | null;
  gajab_url: string | null;
  amazon_url: string | null;
  amazon_price: string | null;
  amazon_match_score: number | null;
  amazon_reliable?: boolean;
  amazon_dinov2?: number | null;
  amazon_clip?: number | null;
  amazon_match_tag?: string | null;
  meesho_url: string | null;
  meesho_price: string | null;
  meesho_match_score: number | null;
  meesho_reliable?: boolean;
  flipkart_url: string | null;
  flipkart_price: string | null;
  flipkart_match_score: number | null;
  flipkart_reliable?: boolean;
  flipkart_dinov2?: number | null;
  flipkart_clip?: number | null;
  flipkart_match_tag?: string | null;
  amazon_unavailable?: boolean;
  flipkart_unavailable?: boolean;
  search_errors?: Record<string, string | null>;
  search_error?: string;
}

interface ProductDuplicate {
  product_id: string;
  duplicate_of: string;
  dinov2_score: number;
}

interface ManualEntry {
  url: string;
  price: string;
}

const PAGE_SIZES = [10, 25, 50, 100];

const platforms = [
  {
    key: "amazon" as const,
    label: "Amazon.in",
    borderColor: "border-yellow-200",
    bgColor: "bg-yellow-50",
    iconColor: "text-yellow-600",
    textColor: "text-yellow-800",
    Icon: Package,
  },
  {
    key: "flipkart" as const,
    label: "Flipkart",
    borderColor: "border-blue-200",
    bgColor: "bg-blue-50",
    iconColor: "text-blue-600",
    textColor: "text-blue-800",
    Icon: ShoppingCart,
  },
  {
    key: "meesho" as const,
    label: "Meesho",
    borderColor: "border-orange-200",
    bgColor: "bg-orange-50",
    iconColor: "text-orange-600",
    textColor: "text-orange-800",
    Icon: ShoppingBag,
  },
];

export default function PriceMapper() {
  const [products, setProducts] = useState<GajabProduct[]>([]);
  const [mappings, setMappings] = useState<PriceMapping[]>([]);
  const [duplicates, setDuplicates] = useState<ProductDuplicate[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchingId, setSearchingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [manualUrls, setManualUrls] = useState<Record<string, {
    amazon: ManualEntry; flipkart: ManualEntry; meesho: ManualEntry;
  }>>({});
  const [exporting, setExporting] = useState(false);
  const [productIndexMin, setProductIndexMin] = useState(1);
  const [productIndexMax, setProductIndexMax] = useState(0);
  const [mappingFilter, setMappingFilter] = useState<"all" | "mapped" | "unmapped" | "duplicates">("all");
  const [batchSearching, setBatchSearching] = useState(false);
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0 });
  const [researchMode, setResearchMode] = useState(false);
  const [skipAttempted, setSkipAttempted] = useState(true);
  const batchAbortRef = useRef(false);

  useEffect(() => {
    fetchProducts();
    fetchMappings();
    fetchDuplicates();
  }, []);

  async function fetchProducts() {
    try {
      const res = await fetch("/api/price-mapper/products");
      const data = await res.json();
      const prods = data.products || [];
      setProducts(prods);
      setProductIndexMax(prods.length);
    } catch {
      setError("Failed to load products");
    } finally {
      setLoading(false);
    }
  }

  async function fetchMappings() {
    try {
      const res = await fetch("/api/price-mapper/mappings");
      const data = await res.json();
      const m = data.mappings || [];
      setMappings(m);
      const urls: Record<string, { amazon: ManualEntry; flipkart: ManualEntry; meesho: ManualEntry }> = {};
      for (const mapping of m) {
        urls[mapping.gajab_product_id] = {
          amazon: { url: mapping.amazon_url || "", price: mapping.amazon_price || "" },
          flipkart: { url: mapping.flipkart_url || "", price: mapping.flipkart_price || "" },
          meesho: { url: mapping.meesho_url || "", price: mapping.meesho_price || "" },
        };
      }
      setManualUrls(prev => ({ ...prev, ...urls }));
    } catch {
      // non-critical
    }
  }

  async function fetchDuplicates() {
    try {
      const res = await fetch("/api/price-mapper/duplicates");
      const data = await res.json();
      setDuplicates(data.duplicates || []);
    } catch {
      // non-critical
    }
  }

  async function searchPlatforms(product: GajabProduct) {
    setSearchingId(product.id);
    setError("");
    try {
      const res = await fetch("/api/price-mapper/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product }),
      });
      const data = await res.json();
      if (data.mapping) {
        setMappings(prev => {
          const idx = prev.findIndex(m => m.gajab_product_id === product.id);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = data.mapping;
            return next;
          }
          return [data.mapping, ...prev];
        });

        // Auto-populate manual URL fields
        setManualUrls(prev => ({
          ...prev,
          [product.id]: {
            amazon: { url: data.mapping.amazon_url || prev[product.id]?.amazon?.url || "", price: data.mapping.amazon_price || prev[product.id]?.amazon?.price || "" },
            flipkart: { url: data.mapping.flipkart_url || prev[product.id]?.flipkart?.url || "", price: data.mapping.flipkart_price || prev[product.id]?.flipkart?.price || "" },
            meesho: { url: data.mapping.meesho_url || prev[product.id]?.meesho?.url || "", price: data.mapping.meesho_price || prev[product.id]?.meesho?.price || "" },
          },
        }));
      }
    } catch {
      setError("Search failed");
    } finally {
      setSearchingId(null);
    }
  }

  async function searchAllFiltered() {
    if (filtered.length === 0) {
      setError("No products match the current filters");
      return;
    }

    const toSearch = researchMode
      ? filtered
      : filtered.filter(p => {
          if (isDuplicate(p.id)) return false;
          const mapping = getMapping(p.id);
          const isMapped = mapping && !!(mapping.amazon_url || mapping.flipkart_url || mapping.meesho_url);
          if (skipAttempted && mapping && !isMapped) return false;
          return !isMapped;
        });

    const skipped = filtered.length - toSearch.length;
    if (toSearch.length === 0) {
      setSuccessMsg(skipAttempted && skipped > 0 ? "All products in this filter have been attempted" : "All products in this filter are already mapped");
      setTimeout(() => setSuccessMsg(""), 3000);
      setBatchSearching(false);
      return;
    }

    batchAbortRef.current = false;
    setBatchSearching(true);
    setBatchProgress({ current: 0, total: toSearch.length });

    const CONCURRENCY = 3;
    let completed = 0;
    for (let i = 0; i < toSearch.length && !batchAbortRef.current; i += CONCURRENCY) {
      const batch = toSearch.slice(i, i + CONCURRENCY);
      await Promise.all(batch.map(async (product) => {
        if (batchAbortRef.current) return;
        await searchPlatforms(product);
        completed++;
        setBatchProgress({ current: completed, total: toSearch.length });
      }));
    }

    setBatchSearching(false);
    setSuccessMsg(`Batch ${researchMode ? "re-search" : "search"} complete: ${toSearch.length} product(s) processed`);
    setTimeout(() => setSuccessMsg(""), 3000);
  }

  function abortBatchSearch() {
    batchAbortRef.current = true;
    setBatchSearching(false);
  }

  async function exportExcel() {
    setExporting(true);
    try {
      let exportData: any[] = [];

      if (mappingFilter === "duplicates") {
        // Export duplicate product details
        for (const product of filtered) {
          const dup = getDuplicateSource(product.id);
          const productMapping = getMapping(product.id);
          exportData.push({
            gajab_product_id: product.id,
            gajab_title: product.name,
            gajab_price: product.price,
            gajab_url: product.url,
            duplicate_of: dup?.duplicate_of || "",
            dinov2_score: dup?.dinov2_score || "",
          });
        }
      } else {
        // Export mappings for current filter
        const allMappings: PriceMapping[] = [];
        for (const product of filtered) {
          const mapping = getMapping(product.id);
          if (mapping && (mapping.amazon_url || mapping.flipkart_url || mapping.meesho_url)) {
            allMappings.push(mapping);
          }
        }
        exportData = allMappings;
      }

      const filename = mappingFilter === "duplicates" ? "duplicate-products.csv" : "price-mappings.csv";

      const exportRes = await fetch("/api/price-mapper/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mappings: exportData, filename }),
      });

      const blob = await exportRes.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("Export failed");
    } finally {
      setExporting(false);
    }
  }

  const isDuplicate = (productId: string) => duplicates.some(d => d.product_id === productId);
  function getDuplicateSource(productId: string): ProductDuplicate | undefined {
    return duplicates.find(d => d.product_id === productId);
  }
  const isAttempted = (productId: string) => !!getMapping(productId);

  const filtered = products.filter((p, idx) => {
    const idxInRange = (idx + 1) >= productIndexMin && (productIndexMax === 0 || (idx + 1) <= productIndexMax);
    const matchesQuery = p.name.toLowerCase().includes(searchQuery.toLowerCase());
    if (!idxInRange || !matchesQuery) return false;
    const mapping = getMapping(p.id);
    const isMapped = mapping && !!(mapping.amazon_url || mapping.flipkart_url || mapping.meesho_url);
    const isDup = isDuplicate(p.id);
    if (mappingFilter === "mapped" && !isMapped) return false;
    if (mappingFilter === "unmapped" && (isMapped || isDup)) return false;
    if (mappingFilter === "duplicates" && !isDup) return false;
    return true;
  });

  const totalPages = Math.ceil(filtered.length / pageSize);
  const paginated = filtered.slice((page - 1) * pageSize, page * pageSize);

  useEffect(() => {
    setPage(1);
  }, [searchQuery, pageSize]);

  const formatPrice = (price: string | null | undefined) =>
    price || "\u2014";

  function getMapping(productId: string) {
    return mappings.find(m => m.gajab_product_id === productId);
  }

  function setUrl(productId: string, platform: "amazon" | "flipkart" | "meesho", field: "url" | "price", value: string) {
    setManualUrls(prev => ({
      ...prev,
      [productId]: {
        ...prev[productId],
        [platform]: { ...(prev[productId]?.[platform] || { url: "", price: "" }), [field]: value },
      },
    }));
  }

  function saveSingle(productId: string, platform: string, label: string) {
    const entry = manualUrls[productId]?.[platform as keyof typeof manualUrls[string]];
    if (!entry?.url || !entry?.price) return;

    fetch("/api/price-mapper/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ productId, platform, url: entry.url, price: entry.price }),
    }).then(() => {
      fetchMappings();
      setSuccessMsg(`${label} price saved!`);
      setTimeout(() => setSuccessMsg(""), 2000);
    });
  }

  function hasMatch(mapping: PriceMapping | undefined, platform: string): boolean {
    if (!mapping) return false;
    if (platform === "amazon") return !!mapping.amazon_url;
    if (platform === "flipkart") return !!mapping.flipkart_url;
    if (platform === "meesho") return !!mapping.meesho_url;
    return false;
  }

  function getMatchPrice(mapping: PriceMapping | undefined, platform: string): string | null | undefined {
    if (!mapping) return null;
    if (platform === "amazon") return mapping.amazon_price;
    if (platform === "flipkart") return mapping.flipkart_price;
    if (platform === "meesho") return mapping.meesho_price;
    return null;
  }

  function getMatchUrl(mapping: PriceMapping | undefined, platform: string): string | null | undefined {
    if (!mapping) return null;
    if (platform === "amazon") return mapping.amazon_url;
    if (platform === "flipkart") return mapping.flipkart_url;
    if (platform === "meesho") return mapping.meesho_url;
    return null;
  }

  function getMatchScore(mapping: PriceMapping | undefined, platform: string): number | null | undefined {
    if (!mapping) return null;
    if (platform === "amazon") return mapping.amazon_match_score;
    if (platform === "flipkart") return mapping.flipkart_match_score;
    if (platform === "meesho") return mapping.meesho_match_score;
    return null;
  }

  function getReliable(mapping: PriceMapping | undefined, platform: string): boolean {
    if (!mapping) return false;
    if (platform === "amazon") return mapping.amazon_reliable ?? false;
    if (platform === "flipkart") return mapping.flipkart_reliable ?? false;
    if (platform === "meesho") return mapping.meesho_reliable ?? false;
    return false;
  }

  function getDinov2(mapping: PriceMapping | undefined, platform: string): number | null | undefined {
    if (!mapping) return null;
    if (platform === "amazon") return mapping.amazon_dinov2;
    if (platform === "flipkart") return mapping.flipkart_dinov2;
    return null;
  }

  function getClip(mapping: PriceMapping | undefined, platform: string): number | null | undefined {
    if (!mapping) return null;
    if (platform === "amazon") return mapping.amazon_clip;
    if (platform === "flipkart") return mapping.flipkart_clip;
    return null;
  }

  function getMatchTag(mapping: PriceMapping | undefined, platform: string): string | null | undefined {
    if (!mapping) return null;
    if (platform === "amazon") return mapping.amazon_match_tag;
    if (platform === "flipkart") return mapping.flipkart_match_tag;
    return null;
  }

  function getUnavailable(mapping: PriceMapping | undefined, platform: string): boolean {
    if (!mapping) return false;
    if (platform === "amazon") return mapping.amazon_unavailable ?? false;
    if (platform === "flipkart") return mapping.flipkart_unavailable ?? false;
    return false;
  }

  function matchTagColor(tag: string | null | undefined): string {
    if (tag === "Exact Match") return "bg-green-100 text-green-700";
    if (tag === "Match") return "bg-emerald-100 text-emerald-700";
    if (tag === "Similar") return "bg-blue-100 text-blue-700";
    if (tag === "Almost Similar") return "bg-amber-100 text-amber-700";
    return "bg-gray-100 text-gray-500";
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-50 to-indigo-100">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-4">
            <Link href="/">
              <Button variant="outline" size="icon">
                <ArrowLeft className="h-4 w-4" />
              </Button>
            </Link>
            <div>
              <h1 className="text-3xl font-bold text-slate-900 flex items-center gap-3">
                <BadgePercent className="h-8 w-8 text-indigo-600" />
                Price Mapper
              </h1>
              <p className="text-slate-600 mt-1">
                Reverse-search Amazon, Flipkart, and Meesho to find exact product matches
              </p>
            </div>
          </div>
          <Button onClick={exportExcel} disabled={exporting} variant="outline">
            {exporting ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Download className="h-4 w-4 mr-2" />
            )}
            Export Excel
          </Button>
        </div>

        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center gap-3 text-red-700">
            <AlertCircle className="h-5 w-5 shrink-0" />
            {error}
            <button className="ml-auto text-sm underline" onClick={() => setError("")}>Dismiss</button>
          </div>
        )}
        {successMsg && (
          <div className="mb-6 p-4 bg-green-50 border border-green-200 rounded-lg flex items-center gap-3 text-green-700">
            <CheckCircle2 className="h-5 w-5 shrink-0" />
            {successMsg}
          </div>
        )}

        <div className="mb-6 space-y-3">
          <div className="flex items-center gap-4">
            <div className="relative max-w-md flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
              <Input
                className="pl-10"
                placeholder="Search products..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
              />
            </div>
            <span className="text-sm text-slate-500 whitespace-nowrap">
              {filtered.length} products
            </span>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-1 bg-white rounded-lg border p-0.5 shadow-sm">
              {(["all", "unmapped", "mapped", "duplicates"] as const).map(f => (
                <button
                  key={f}
                  className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                    mappingFilter === f
                      ? "bg-indigo-600 text-white shadow-sm"
                      : "text-slate-600 hover:text-slate-800"
                  }`}
                  onClick={() => setMappingFilter(f)}
                >
                  {f === "all" ? "All" : f === "unmapped" ? "Unmapped" : f === "mapped" ? "Mapped" : "Duplicates"}
                </button>
              ))}
            </div>
            <span className="text-xs text-slate-400 whitespace-nowrap">
              {products.filter(p => {
                const m = getMapping(p.id);
                return m && !!(m.amazon_url || m.flipkart_url || m.meesho_url);
              }).length} / {products.filter(p => {
                const m = getMapping(p.id);
                return !(m && !!(m.amazon_url || m.flipkart_url || m.meesho_url)) && !isDuplicate(p.id);
              }).length} / {products.filter(p => isDuplicate(p.id)).length} / {products.length}{" "}
              mapped / unmapped / dupes / total
              {" / "}
              {products.filter(p => !isDuplicate(p.id) && getMapping(p.id)).length} attempted
            </span>
            <button
              onClick={() => setResearchMode(!researchMode)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors border ${
                researchMode
                  ? "bg-purple-600 text-white border-purple-600 shadow-sm"
                  : "bg-white text-purple-600 border-purple-200 hover:bg-purple-50"
              }`}
            >
              {researchMode ? "Re-Search ON" : "Re-Search OFF"}
            </button>
            {!researchMode && (
              <button
                onClick={() => setSkipAttempted(!skipAttempted)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors border ${
                  skipAttempted
                    ? "bg-amber-600 text-white border-amber-600 shadow-sm"
                    : "bg-white text-amber-600 border-amber-200 hover:bg-amber-50"
                }`}
              >
                {skipAttempted ? "Skip Attempted" : "Retry All"}
              </button>
            )}
            <SlidersHorizontal className="h-4 w-4 text-slate-400" />
            <span className="text-sm text-slate-500">Product #:</span>
            <Input
              type="number"
              className="w-24 h-8 text-sm"
              placeholder="From"
              min={1}
              max={products.length}
              value={productIndexMin}
              onChange={e => setProductIndexMin(Number(e.target.value))}
            />
            <span className="text-slate-400">–</span>
            <Input
              type="number"
              className="w-24 h-8 text-sm"
              placeholder="To"
              min={1}
              max={products.length}
              value={productIndexMax || ""}
              onChange={e => setProductIndexMax(Number(e.target.value))}
            />
            <Button
              onClick={searchAllFiltered}
              disabled={batchSearching}
              size="sm"
              className="ml-2"
            >
              {batchSearching ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  {batchProgress.current}/{batchProgress.total}
                </>
              ) : (
                <>
                  <Play className="h-4 w-4 mr-2" />
                  {researchMode ? "Re-Search All Filtered" : "Search All Filtered"}
                </>
              )}
            </Button>
            {batchSearching && (
              <Button onClick={abortBatchSearch} size="sm" variant="outline" className="text-red-600 border-red-200">
                Stop
              </Button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="text-center py-12 text-slate-500">Loading products...</div>
        ) : (
          <>
            <div className="space-y-4">
              {paginated.map(product => {
                const mapping = getMapping(product.id);
                const isSearching = searchingId === product.id;

                return (
                  <Card key={product.id} className="border-indigo-200">
                    <CardHeader>
                      <div className="flex items-start gap-4">
                        {product.image_url && (
                          <img
                            src={product.image_url}
                            alt={product.name}
                            className="w-16 h-16 rounded-lg object-cover shrink-0"
                          />
                        )}
                        <div className="min-w-0 flex-1">
                          <CardTitle className="text-lg">{product.name}</CardTitle>
                          <p className="text-sm text-slate-500 mt-1">
                            Gajab: <span className="font-semibold">{formatPrice(product.price)}</span>
                            {product.url && (
                              <a href={product.url} target="_blank" rel="noopener noreferrer" className="ml-2 text-xs text-indigo-500 hover:underline inline-flex items-center gap-0.5">
                                View <ExternalLink className="h-3 w-3" />
                              </a>
                            )}
                            {product.category && ` \u00b7 ${product.category}`}
                          </p>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <Button
                            onClick={() => {
                              setManualUrls(prev => {
                                if (!prev[product.id]) {
                                  return {
                                    ...prev,
                                    [product.id]: {
                                      amazon: { url: mapping?.amazon_url || "", price: mapping?.amazon_price || "" },
                                      flipkart: { url: mapping?.flipkart_url || "", price: mapping?.flipkart_price || "" },
                                      meesho: { url: mapping?.meesho_url || "", price: mapping?.meesho_price || "" },
                                    },
                                  };
                                }
                                return prev;
                              });
                              searchPlatforms(product);
                            }}
                            disabled={isSearching}
                          >
                            {isSearching ? (
                              <Loader2 className="h-4 w-4 animate-spin mr-2" />
                            ) : (
                              <Search className="h-4 w-4 mr-2" />
                            )}
                            {isSearching ? "Searching..." : "Auto Search All"}
                          </Button>
                        </div>
                      </div>
                    </CardHeader>

                    <CardContent>
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        {platforms.map(({ key, label, borderColor, bgColor, iconColor, textColor, Icon }) => {
                          const entry = manualUrls[product.id]?.[key] || { url: "", price: "" };
                          const matched = hasMatch(mapping, key);
                          const matchPrice = getMatchPrice(mapping, key);
                          const matchUrl = getMatchUrl(mapping, key);
                          const matchScore = getMatchScore(mapping, key);

                          return (
                            <div key={key} className={`border ${borderColor} rounded-lg p-4 ${bgColor}`}>
                              <div className="flex items-center gap-2 mb-3">
                                <Icon className={`h-4 w-4 ${iconColor}`} />
                                <span className={`font-semibold text-sm ${textColor}`}>{label}</span>
                                {matched && <CheckCircle2 className="h-3.5 w-3.5 text-green-500 ml-auto" />}
                              </div>

                              {matched && (
                                <div className="text-sm mb-2 p-2 bg-white/60 rounded">
                                  {getUnavailable(mapping, key) ? (
                                    <span className="text-xs text-red-500 font-medium">Product Unavailable</span>
                                  ) : (
                                    <>
                                      {matchPrice && <span className="font-bold">{matchPrice} </span>}
                                      {matchScore != null && (
                                        <span className="text-xs font-semibold">
                                          {Math.round(matchScore)}%
                                        </span>
                                      )}
                                    </>
                                  )}
                                  {getMatchTag(mapping, key) && (
                                    <span className={`text-xs ml-1 px-1.5 py-0.5 rounded font-medium ${matchTagColor(getMatchTag(mapping, key))}`}>
                                      {getMatchTag(mapping, key)}
                                    </span>
                                  )}
                                  {key !== "meesho" && (getDinov2(mapping, key) != null || getClip(mapping, key) != null) && (
                                    <span className="text-[10px] ml-1 text-gray-400" title={`DINOv2: ${((getDinov2(mapping, key) ?? 0) * 100).toFixed(0)}% · CLIP: ${((getClip(mapping, key) ?? 0) * 100).toFixed(0)}%`}>
                                      D:{((getDinov2(mapping, key) ?? 0) * 100).toFixed(0)} C:{((getClip(mapping, key) ?? 0) * 100).toFixed(0)}
                                    </span>
                                  )}
                                  {matchUrl ? (
                                    <a
                                      href={matchUrl}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="text-xs text-indigo-600 hover:underline flex items-center gap-1 mt-1 truncate"
                                    >
                                      {matchUrl.slice(0, 55)}... <ExternalLink className="h-3 w-3 shrink-0" />
                                    </a>
                                  ) : <p className="text-xs text-slate-400 italic">No URL available</p>}
                                </div>
                              )}

                              {!matched && mapping?.search_errors?.[key] && (
                                <p className="text-xs text-slate-400 mb-2 italic">
                                  {mapping.search_errors[key]}
                                </p>
                              )}

                              <div className="space-y-2">
                                <Input
                                  className="text-xs h-8"
                                  placeholder={`${label} URL...`}
                                  value={entry.url}
                                  onChange={e => setUrl(product.id, key, "url", e.target.value)}
                                />
                                <div className="flex gap-2">
                                  <Input
                                    className="text-xs h-8 flex-1"
                                    placeholder="Price (e.g. \u20b9299)"
                                    value={entry.price}
                                    onChange={e => setUrl(product.id, key, "price", e.target.value)}
                                  />
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    className="h-8 shrink-0"
                                    disabled={!entry.url || !entry.price}
                                    onClick={() => saveSingle(product.id, key, label)}
                                  >
                                    <Save className="h-3 w-3 mr-1" />
                                    Save
                                  </Button>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </CardContent>
                  </Card>
                );
              })}

              {filtered.length === 0 && (
                <div className="text-center py-12 text-slate-500">
                  {searchQuery ? "No products match your search" : "No products found. Sync products from Gajab.com first."}
                </div>
              )}
            </div>

            {totalPages > 1 && (
              <div className="flex items-center justify-between mt-6">
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <span>Rows per page:</span>
                  <select
                    className="border rounded px-2 py-1 text-sm"
                    value={pageSize}
                    onChange={e => setPageSize(Number(e.target.value))}
                  >
                    {PAGE_SIZES.map(s => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                  <span>
                    {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, filtered.length)} of {filtered.length}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page <= 1}
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  {Array.from({ length: Math.min(totalPages, 10) }, (_, i) => {
                    let pageNum: number;
                    if (totalPages <= 10) {
                      pageNum = i + 1;
                    } else if (page <= 5) {
                      pageNum = i + 1;
                    } else if (page >= totalPages - 4) {
                      pageNum = totalPages - 9 + i;
                    } else {
                      pageNum = page - 4 + i;
                    }
                    return (
                      <Button
                        key={pageNum}
                        variant={pageNum === page ? "default" : "outline"}
                        size="sm"
                        className="min-w-[36px]"
                        onClick={() => setPage(pageNum)}
                      >
                        {pageNum}
                      </Button>
                    );
                  })}
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= totalPages}
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
