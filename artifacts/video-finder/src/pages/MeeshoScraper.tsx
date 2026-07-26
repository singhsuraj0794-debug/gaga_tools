import { useState, useCallback, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import {
  ArrowLeft,
  Search,
  Download,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  BadgeCheck,
  ListChecks,
  RotateCcw,
  Store,
  SlidersHorizontal,
} from "lucide-react";
import { Link } from "wouter";

interface MeeshoDetailedProduct {
  id: string;
  title: string;
  description: string | null;
  meta_description: string | null;
  imageUrl: string | null;
  images: string[];
  hsn: string | null;
  gst: string | null;
  dimensions: string | null;
  weight: string | null;
  specifications: Record<string, string> | null;
  variants: string | null;
  price: string | null;
  url: string;
  status: string;
  error: string | null;
}

export default function MeeshoScraper() {
  const [storeUrl, setStoreUrl] = useState("");
  const [products, setProducts] = useState<MeeshoDetailedProduct[]>([]);
  const [filteredProducts, setFilteredProducts] = useState<MeeshoDetailedProduct[]>([]);
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
  const [scrapedProducts, setScrapedProducts] = useState<MeeshoDetailedProduct[]>(() => {
    try {
      const saved = sessionStorage.getItem("meesho_scraped");
      return saved ? JSON.parse(saved) : [];
    } catch { return []; }
  });
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractJobId, setExtractJobId] = useState<string | null>(null);
  const extractPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [isScraping, setIsScraping] = useState(() => !!sessionStorage.getItem("meesho_jobId"));
  const [scrapeJobId, setScrapeJobId] = useState<string | null>(() => sessionStorage.getItem("meesho_jobId"));
  const [isExporting, setIsExporting] = useState(false);
  const [storeName, setStoreName] = useState("");
  const [extractError, setExtractError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [range, setRange] = useState<[number, number]>([1, 1]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Persist scrapedProducts across page reloads
  useEffect(() => {
    sessionStorage.setItem("meesho_scraped", JSON.stringify(scrapedProducts));
  }, [scrapedProducts]);

  // Resume / poll extract job
  useEffect(() => {
    if (!extractJobId) return;
    extractPollRef.current = setInterval(async () => {
      try {
        const pollRes = await fetch(`/api/scraper/meesho/extract/${extractJobId}`);
        const job = await pollRes.json();
        if (job.status === "completed") {
          setProducts(job.products || []);
          setFilteredProducts(job.products || []);
          setSelectedUrls(new Set((job.products || []).map((p: MeeshoDetailedProduct) => p.url)));
          const count = (job.products || []).length;
          setRange([1, count || 1]);
          setStoreName(job.store_name || "");
          setIsExtracting(false);
          setExtractJobId(null);
        } else if (job.status === "failed") {
          setExtractError(job.error || "Extraction failed");
          setIsExtracting(false);
          setExtractJobId(null);
        }
      } catch {
        // keep polling
      }
    }, 3000);
    return () => {
      if (extractPollRef.current) clearInterval(extractPollRef.current);
    };
  }, [extractJobId]);
  useEffect(() => {
    const jobId = sessionStorage.getItem("meesho_jobId");
    if (jobId && isScraping) {
      pollRef.current = setInterval(async () => {
        try {
          const pollRes = await fetch(`/api/scraper/meesho/scrape/${jobId}`);
          const job = await pollRes.json();
          if (job.products) {
            setScrapedProducts(prev => {
              const existing = new Map(prev.map(p => [p.url, p]));
              for (const p of job.products) existing.set(p.url, p);
              return Array.from(existing.values());
            });
          }
          if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setIsScraping(false);
            setScrapeJobId(null);
            sessionStorage.removeItem("meesho_jobId");
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setIsScraping(false);
          setScrapeJobId(null);
          sessionStorage.removeItem("meesho_jobId");
        }
      }, 3000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleExtract = async () => {
    if (!storeUrl.trim()) return;

    setIsExtracting(true);
    setExtractError("");
    setProducts([]);
    setFilteredProducts([]);
    setSelectedUrls(new Set());
    setScrapedProducts([]);
    sessionStorage.removeItem("meesho_scraped");
    sessionStorage.removeItem("meesho_jobId");
    setSearchQuery("");

    try {
      const response = await fetch("/api/scraper/meesho/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: storeUrl.trim() }),
      });

      const data = await response.json();
      if (!response.ok) {
        setExtractError(data.error || "Failed to extract products");
        setIsExtracting(false);
        return;
      }

      // If async — got a jobId, start polling
      if (data.jobId) {
        setExtractJobId(data.jobId);
        return;
      }

      // Fallback: synchronous response
      setProducts(data.products || []);
      setFilteredProducts(data.products || []);
      setSelectedUrls(new Set((data.products || []).map((p: MeeshoDetailedProduct) => p.url)));
      const count = (data.products || []).length;
      setRange([1, count || 1]);
      setStoreName(data.storeName || "");
      setIsExtracting(false);
    } catch (error) {
      console.error("Extract error:", error);
      setExtractError("Failed to connect to server");
      setIsExtracting(false);
    }
  };

  const handleSearch = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const q = e.target.value.toLowerCase();
      setSearchQuery(q);
      if (!q.trim()) {
        setFilteredProducts(products);
      } else {
        setFilteredProducts(
          products.filter((p) =>
            p.title.toLowerCase().includes(q) ||
            p.url.toLowerCase().includes(q)
          ),
        );
      }
    },
    [products],
  );

  const toggleUrl = (url: string) => {
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  const selectAllFiltered = () => {
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      for (const p of filteredProducts) next.add(p.url);
      return next;
    });
  };

  const deselectAllFiltered = () => {
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      for (const p of filteredProducts) next.delete(p.url);
      return next;
    });
  };

  const handleScrape = async () => {
    const urlsToScrape = Array.from(selectedUrls);
    if (urlsToScrape.length === 0) return;

    setIsScraping(true);
    try {
      const response = await fetch("/api/scraper/meesho/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ urls: urlsToScrape, products }),
      });

      const { jobId, total } = await response.json();
      if (!response.ok) {
        alert("Failed to submit scrape job: " + (total || "unknown error"));
        setIsScraping(false);
        return;
      }

      sessionStorage.setItem("meesho_jobId", jobId);
      setScrapeJobId(jobId);

      // Poll for results
      pollRef.current = setInterval(async () => {
        try {
          const pollRes = await fetch(`/api/scraper/meesho/scrape/${jobId}`);
          const job = await pollRes.json();
          if (job.products) {
            setScrapedProducts(prev => {
              const existing = new Map(prev.map(p => [p.url, p]));
              for (const p of job.products) existing.set(p.url, p);
              return Array.from(existing.values());
            });
          }
          if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setIsScraping(false);
            setScrapeJobId(null);
            sessionStorage.removeItem("meesho_jobId");
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setIsScraping(false);
          sessionStorage.removeItem("meesho_jobId");
        }
      }, 3000);
    } catch (error) {
      console.error("Scrape error:", error);
      alert("Failed to submit scrape job");
      setIsScraping(false);
      sessionStorage.removeItem("meesho_jobId");
    }
  };

  const handleExport = async () => {
    if (scrapedProducts.length === 0) return;

    setIsExporting(true);
    try {
      const response = await fetch("/api/scraper/meesho/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ products: scrapedProducts }),
      });

      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "meesho-products.xlsx";
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      } else {
        alert("Failed to export products");
      }
    } catch (error) {
      console.error("Export error:", error);
      alert("Failed to export products");
    } finally {
      setIsExporting(false);
    }
  };

  const handleCancelScrape = async () => {
    if (scrapeJobId) {
      try {
        await fetch(`/api/scraper/meesho/cancel/${scrapeJobId}`, { method: "POST" });
      } catch {}
    }
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
    setIsScraping(false);
    setScrapeJobId(null);
    sessionStorage.removeItem("meesho_jobId");
  };

  const handleExportURLs = () => {
    const csv = "URL\n" + products.map(p => p.url).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "meesho-product-urls.csv";
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  };

  const handleReset = () => {
    if (isScraping) handleCancelScrape();
    setStoreUrl("");
    setProducts([]);
    setFilteredProducts([]);
    setSelectedUrls(new Set());
    setScrapedProducts([]);
    setStoreName("");
    setExtractError("");
    setSearchQuery("");
    setRange([1, 1]);
  };

  const applyRange = (newRange: [number, number]) => {
    setRange(newRange);
    const [min, max] = newRange;
    setSelectedUrls(new Set(products.slice(min - 1, max).map(p => p.url)));
  };

  const handleClearCache = async () => {
    try {
      const res = await fetch("/api/scraper/meesho/clear-cache", { method: "POST" });
      const data = await res.json();
      alert(data.message || "Cache cleared");
    } catch {
      alert("Failed to clear cache");
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 to-slate-100 p-6">
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link href="/">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Home
            </Button>
          </Link>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-2xl font-bold text-orange-800">
              Meesho Store Scraper
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Step 1: Enter Store URL */}
            <div className="border-2 border-dashed border-orange-300 rounded-lg p-8">
              <div className="flex flex-col items-center gap-4">
                <Store className="w-12 h-12 text-orange-600" />
                <span className="text-lg font-medium text-orange-800">
                  Enter a Meesho Store URL
                </span>
                <span className="text-sm text-orange-600 text-center max-w-md">
                  Paste the URL of a Meesho shop/seller page (e.g., https://www.meesho.com/storename)
                </span>
                <div className="flex w-full max-w-xl gap-2">
                  <Input
                    placeholder="https://www.meesho.com/storename"
                    value={storeUrl}
                    onChange={(e) => setStoreUrl(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleExtract()}
                    className="border-orange-300 focus-visible:ring-orange-500"
                  />
                  <Button
                    onClick={handleExtract}
                    disabled={!storeUrl.trim() || isExtracting}
                    className="bg-orange-600 hover:bg-orange-700"
                  >
                    {isExtracting ? (
                      <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2" />
                    ) : (
                      <Search className="w-4 h-4 mr-2" />
                    )}
                    Extract
                  </Button>
                </div>
              </div>
            </div>

            {storeName && (
              <div className="flex items-center gap-2 p-4 bg-orange-100 text-orange-800 rounded-lg">
                <Store className="w-5 h-5 shrink-0" />
                <span>Store: <strong>{storeName}</strong> &mdash; {products.length} products found</span>
              </div>
            )}

            {extractError && (
              <div className="flex items-center gap-2 p-4 bg-red-100 text-red-800 rounded-lg">
                <AlertCircle className="w-5 h-5 shrink-0" />
                <span>{extractError}</span>
              </div>
            )}

            {/* Step 2: Product Preview & Selection */}
            {products.length > 0 && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold text-orange-800">
                    Select Products to Scrape
                  </h3>
                  <span className="text-sm text-orange-600 bg-orange-50 px-3 py-1 rounded-full">
                    {selectedUrls.size} of {products.length} selected
                  </span>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative flex-1 min-w-[200px]">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-orange-500" />
                    <Input
                      placeholder="Filter products..."
                      value={searchQuery}
                      onChange={handleSearch}
                      className="pl-9 border-orange-300 focus-visible:ring-orange-500"
                    />
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={selectAllFiltered}
                    className="border-orange-300 text-orange-700"
                  >
                    <BadgeCheck className="h-4 w-4 mr-1" />
                    Select All
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={deselectAllFiltered}
                    className="border-orange-300 text-orange-700"
                  >
                    <ListChecks className="h-4 w-4 mr-1" />
                    Deselect All
                  </Button>
                </div>

                {/* Range Slider Filter */}
                {products.length > 1 && (
                  <div className="border border-orange-200 rounded-lg p-4 bg-orange-50/50 space-y-3">
                    <div className="flex items-center gap-2 text-sm font-medium text-orange-800">
                      <SlidersHorizontal className="h-4 w-4" />
                      Range Filter
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-orange-600">From</label>
                        <Input
                          type="number"
                          min={1}
                          max={products.length}
                          value={range[0]}
                          onChange={(e) => {
                            const v = Math.max(1, Math.min(Number(e.target.value) || 1, range[1]));
                            applyRange([v, range[1]]);
                          }}
                          className="w-20 h-8 text-center border-orange-300"
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-orange-600">To</label>
                        <Input
                          type="number"
                          min={1}
                          max={products.length}
                          value={range[1]}
                          onChange={(e) => {
                            const v = Math.max(range[0], Math.min(Number(e.target.value) || 1, products.length));
                            applyRange([range[0], v]);
                          }}
                          className="w-20 h-8 text-center border-orange-300"
                        />
                      </div>
                      <span className="text-xs text-orange-500">
                        ({range[1] - range[0] + 1} products)
                      </span>
                    </div>
                    <Slider
                      min={1}
                      max={products.length}
                      step={1}
                      value={range}
                      onValueChange={(val) => applyRange(val as [number, number])}
                      className="w-full"
                    />
                    <div className="flex flex-wrap gap-1.5">
                      {[50, 100, 200, 500].filter(n => n <= products.length).map((n) => {
                        const start = Math.min(n, products.length);
                        return (
                          <Button
                            key={`preset-${n}`}
                            variant="outline"
                            size="sm"
                            onClick={() => applyRange([1, start])}
                            className="h-7 text-xs border-orange-300 text-orange-700"
                          >
                            1-{start}
                          </Button>
                        );
                      })}
                      {products.length > 100 && Array.from({ length: Math.ceil(products.length / 100) }, (_, i) => i).slice(1).map((i) => {
                        const start = i * 100 + 1;
                        const end = Math.min(start + 99, products.length);
                        return (
                          <Button
                            key={`batch-${start}-${end}`}
                            variant="outline"
                            size="sm"
                            onClick={() => applyRange([start, end])}
                            className="h-7 text-xs border-orange-300 text-orange-700"
                          >
                            {start}-{end}
                          </Button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {filteredProducts.length > 0 ? (
                  <div className="border rounded-lg overflow-hidden max-h-[400px] overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-orange-100 text-orange-800 sticky top-0">
                        <tr>
                          <th className="w-10 px-3 py-2 text-center">
                            <input
                              type="checkbox"
                              className="accent-orange-600"
                              checked={
                                filteredProducts.length > 0 &&
                                filteredProducts.every((p) => selectedUrls.has(p.url))
                              }
                              onChange={() => {
                                const allSelected = filteredProducts.every((p) =>
                                  selectedUrls.has(p.url),
                                );
                                if (allSelected) deselectAllFiltered();
                                else selectAllFiltered();
                              }}
                            />
                          </th>
                          <th className="w-12 px-2 py-2 text-center text-xs text-orange-600 font-medium">
                            #
                          </th>
                          <th className="px-2 py-2 text-center text-xs text-orange-600 font-medium">
                            Image
                          </th>
                          <th className="px-3 py-2 text-left text-xs text-orange-600 font-medium">
                            Product
                          </th>
                          <th className="w-20 px-3 py-2 text-right text-xs text-orange-600 font-medium">
                            Price
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-orange-100">
                        {filteredProducts.map((product, idx) => (
                          <tr
                            key={product.url}
                            className={`hover:bg-orange-50 transition-colors ${
                              selectedUrls.has(product.url) ? "bg-orange-50/50" : ""
                            }`}
                          >
                            <td className="px-3 py-2 text-center">
                              <input
                                type="checkbox"
                                className="accent-orange-600"
                                checked={selectedUrls.has(product.url)}
                                onChange={() => toggleUrl(product.url)}
                              />
                            </td>
                            <td className="px-2 py-2 text-center text-xs text-orange-500 font-mono">
                              {products.indexOf(product) + 1}
                            </td>
                            <td className="px-2 py-2 text-center">
                              {product.imageUrl ? (
                                <img
                                  src={product.imageUrl}
                                  alt={product.title}
                                  className="w-10 h-10 object-cover rounded"
                                />
                              ) : (
                                <div className="w-10 h-10 bg-orange-100 rounded flex items-center justify-center">
                                  <AlertCircle className="w-4 h-4 text-orange-400" />
                                </div>
                              )}
                            </td>
                            <td className="px-3 py-2">
                              <div className="flex items-center gap-2">
                                <div className="flex-1 min-w-0">
                                  <span className="truncate block text-orange-900 font-medium text-xs">
                                    {product.title}
                                  </span>
                                  <div className="flex items-center gap-1 mt-0.5">
                                    <span className="text-orange-400 font-mono text-[10px] truncate">
                                      {product.url}
                                    </span>
                                    <a
                                      href={product.url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="shrink-0 text-orange-400 hover:text-orange-600"
                                    >
                                      <ExternalLink className="h-3 w-3" />
                                    </a>
                                  </div>
                                </div>
                              </div>
                            </td>
                            <td className="px-3 py-2 text-right text-sm font-medium text-orange-700">
                              {product.price || "-"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 p-4 bg-yellow-50 text-yellow-700 rounded-lg">
                    <AlertCircle className="w-5 h-5" />
                    <span>No products match your search.</span>
                  </div>
                )}

                <div className="flex items-center gap-3 pt-2">
                  <Button
                    onClick={handleScrape}
                    disabled={isScraping || selectedUrls.size === 0}
                    className="bg-orange-600 hover:bg-orange-700"
                    size="lg"
                  >
                    {isScraping ? (
                      <>
                        <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2" />
                        Scraping...
                      </>
                    ) : (
                      <>
                        <RefreshCw className="h-4 w-4 mr-2" />
                        Scrape {selectedUrls.size} Selected Products
                      </>
                    )}
                  </Button>
                  {isScraping && (
                    <Button
                      onClick={handleCancelScrape}
                      variant="outline"
                      size="lg"
                      className="border-red-300 text-red-600 hover:bg-red-50"
                    >
                      Stop
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleReset}
                    className="text-orange-600"
                  >
                    <RotateCcw className="h-4 w-4 mr-1" />
                    Start Over
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleExportURLs}
                    className="text-green-600"
                  >
                    <Download className="h-4 w-4 mr-1" />
                    Export URLs
                  </Button>
                </div>
              </div>
            )}

            {isScraping && (
              <div className="flex items-center gap-2 p-4 bg-blue-50 text-blue-800 rounded-lg">
                <div className="w-4 h-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                <span>Scraping {selectedUrls.size} products...</span>
              </div>
            )}

            {/* Scraped Products */}
            {scrapedProducts.length > 0 && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="w-5 h-5 text-orange-600" />
                    <span className="text-lg font-semibold text-orange-800">
                      {scrapedProducts.length} Products Scraped
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      onClick={handleExport}
                      disabled={isExporting}
                      className="bg-blue-600 hover:bg-blue-700"
                    >
                      {isExporting ? (
                        <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2" />
                      ) : (
                        <Download className="h-4 w-4 mr-2" />
                      )}
                      Export to Excel
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={handleReset}
                      className="text-orange-600"
                    >
                      <RotateCcw className="h-4 w-4 mr-1" />
                      Start Over
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={handleClearCache}
                      className="text-gray-500"
                    >
                      <RefreshCw className="h-4 w-4 mr-1" />
                      Clear Cache
                    </Button>
                  </div>
                </div>

                <div className="space-y-3">
                  {scrapedProducts.map((product) => (
                    <Card key={product.id || product.url} className={product.status === "blocked" ? "border-yellow-300 bg-yellow-50" : product.status === "failed" ? "border-red-300 bg-red-50" : product.status === "extracted" ? "border-blue-200 bg-blue-50" : ""}>
                      <CardContent className="p-4">
                        <div className="flex items-start gap-4">
                          <div className="shrink-0">
                            {product.images && product.images.length > 0 ? (
                              <div className="grid grid-cols-2 gap-1 w-32">
                                {product.images.slice(0, 4).map((img, i) => (
                                  <a key={i} href={img} target="_blank" rel="noopener noreferrer">
                                    <img
                                      src={img}
                                      alt={`${product.title} ${i + 1}`}
                                      className="w-14 h-14 object-cover rounded border border-orange-200 hover:opacity-80 transition-opacity"
                                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none" }}
                                    />
                                  </a>
                                ))}
                                {product.images.length === 0 && product.imageUrl && (
                                  <a href={product.imageUrl} target="_blank" rel="noopener noreferrer">
                                    <img
                                      src={product.imageUrl}
                                      alt={product.title}
                                      className="w-14 h-14 object-cover rounded border"
                                    />
                                  </a>
                                )}
                              </div>
                            ) : product.imageUrl ? (
                              <a href={product.imageUrl} target="_blank" rel="noopener noreferrer">
                                <img
                                  src={product.imageUrl}
                                  alt={product.title}
                                  className="w-14 h-14 object-cover rounded border"
                                />
                              </a>
                            ) : (
                              <div className="w-14 h-14 bg-orange-100 rounded flex items-center justify-center text-orange-400">
                                <AlertCircle className="w-5 h-5" />
                              </div>
                            )}
                          </div>

                          <div className="flex-1 min-w-0 space-y-1">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-medium text-orange-900 text-sm leading-tight">
                                {product.title || "Untitled"}
                              </span>
                              {product.status === "blocked" && (
                                <span className="text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full font-medium">
                                  Blocked
                                </span>
                              )}
                              {product.status === "failed" && (
                                <span className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full font-medium">
                                  Failed
                                </span>
                              )}
                              {product.status === "success" && (
                                <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">
                                  Success
                                </span>
                              )}
                              {product.status === "extracted" && (
                                <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">
                                  Extract only
                                </span>
                              )}
                            </div>

                            {product.description && (
                              <details className="w-full">
                                <summary className="text-xs text-orange-500 cursor-pointer hover:text-orange-700">
                                  Description
                                </summary>
                                <p className="text-xs text-orange-600 mt-1">{product.description}</p>
                              </details>
                            )}

                            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-orange-700">
                              {product.price && <span><span className="text-orange-500">Price:</span> {product.price}</span>}
                              {product.dimensions && <span><span className="text-orange-500">Dims:</span> {product.dimensions}</span>}
                              {product.weight && <span><span className="text-orange-500">Weight:</span> {product.weight}</span>}
                              {product.gst && <span><span className="text-orange-500">GST:</span> {product.gst}</span>}
                              {product.hsn && <span><span className="text-orange-500">HSN:</span> {product.hsn}</span>}
                              {product.variants && <span><span className="text-orange-500">Variants:</span> {product.variants}</span>}
                              {product.specifications && Object.keys(product.specifications).length > 0 && (
                                <details className="w-full mt-1">
                                  <summary className="text-xs text-orange-500 cursor-pointer hover:text-orange-700">
                                    Specifications ({Object.keys(product.specifications).length})
                                  </summary>
                                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 mt-1 text-xs">
                                    {Object.entries(product.specifications).slice(0, 20).map(([k, v]) => (
                                      <span key={k} className="text-orange-600">
                                        <span className="text-orange-400">{k}:</span> {v}
                                      </span>
                                    ))}
                                    {Object.keys(product.specifications).length > 20 && (
                                      <span className="text-orange-400 col-span-2 italic">
                                        +{Object.keys(product.specifications).length - 20} more
                                      </span>
                                    )}
                                  </div>
                                </details>
                              )}
                            </div>

                            {product.error && (
                              <p className="text-xs text-red-500 mt-1">{product.error}</p>
                            )}

                            <div className="flex items-center gap-2 pt-1">
                              <a
                                href={product.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-xs text-blue-500 hover:text-blue-700 flex items-center gap-1"
                              >
                                <ExternalLink className="h-3 w-3" />
                                View on Meesho
                              </a>
                              {product.images && product.images.length > 1 && (
                                <span className="text-xs text-orange-400">
                                  {product.images.length} images
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
