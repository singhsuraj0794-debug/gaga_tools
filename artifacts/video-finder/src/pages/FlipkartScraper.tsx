import { useState, useMemo, useCallback, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import {
  ArrowLeft,
  Upload,
  Download,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Search,
  ExternalLink,
  BadgeCheck,
  ListChecks,
  RotateCcw,
  SlidersHorizontal,
} from "lucide-react";
import { Link } from "wouter";

interface FlipkartDetailedProduct {
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

function extractProductId(url: string): string {
  const pidMatch = url.match(/pid=([^&]+)/);
  if (pidMatch) return pidMatch[1];
  const parts = url.split("/");
  return parts[parts.length - 1].split("?")[0];
}

function getPlatformLabel(url: string): { label: string; color: string } {
  if (url.includes("flipkart.com")) return { label: "Flipkart", color: "bg-blue-100 text-blue-700" };
  if (url.includes("amazon.")) return { label: "Amazon", color: "bg-yellow-100 text-yellow-700" };
  if (url.includes("meesho.com")) return { label: "Meesho", color: "bg-purple-100 text-purple-700" };
  return { label: "Other", color: "bg-gray-100 text-gray-700" };
}

export default function FlipkartScraper() {
  const [file, setFile] = useState<File | null>(null);
  const [urls, setUrls] = useState<string[]>([]);
  const [filteredUrls, setFilteredUrls] = useState<string[]>([]);
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
  const [products, setProducts] = useState<FlipkartDetailedProduct[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isScraping, setIsScraping] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{ current: number; total: number } | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<{ total: number; valid: number } | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [range, setRange] = useState<[number, number]>([1, 1]);

  const CACHE_KEY = "flipkart-scraper";

  // Restore cached state on mount
  useEffect(() => {
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed.products?.length > 0) setProducts(parsed.products);
        if (parsed.urls?.length > 0) {
          setUrls(parsed.urls);
          setFilteredUrls(parsed.urls);
          setRange([1, parsed.urls.length]);
          if (parsed.selectedUrls?.length > 0) {
            setSelectedUrls(new Set(parsed.selectedUrls));
          } else {
            setSelectedUrls(new Set(parsed.urls));
          }
        }
      }
    } catch {
      // ignore cache errors
    }
  }, []);

  // Save state to cache whenever it changes
  useEffect(() => {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({
        products,
        urls,
        selectedUrls: Array.from(selectedUrls),
      }));
    } catch {
      // storage full or unavailable
    }
  }, [products, urls, selectedUrls]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setProducts([]);
      setUrls([]);
      setFilteredUrls([]);
      setSelectedUrls(new Set());
      setUploadStatus(null);
      setSearchQuery("");
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    const droppedFile = e.dataTransfer.files?.[0];
    if (droppedFile && (droppedFile.name.endsWith(".xlsx") || droppedFile.name.endsWith(".xls"))) {
      setFile(droppedFile);
      setProducts([]);
      setUrls([]);
      setFilteredUrls([]);
      setSelectedUrls(new Set());
      setUploadStatus(null);
      setSearchQuery("");
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch("/api/scraper/flipkart/upload", {
        method: "POST",
        body: formData,
      });
      const data = await response.json();
      if (response.ok) {
        setUrls(data.urls);
        setFilteredUrls(data.urls);
        setSelectedUrls(new Set(data.urls));
        setRange([1, data.urls.length]);
        setUploadStatus({ total: data.totalUrls, valid: data.validUrls });
      } else {
        alert("Upload failed: " + (data.error || "Unknown error"));
      }
    } catch (error: any) {
      console.error("Upload error:", error);
      alert("Upload failed: " + (error?.message || "Network error"));
    } finally {
      setIsUploading(false);
    }
  };

  const handleSearch = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const q = e.target.value.toLowerCase();
      setSearchQuery(q);
      if (!q.trim()) {
        setFilteredUrls(urls);
      } else {
        setFilteredUrls(
          urls.filter((u) => u.toLowerCase().includes(q)),
        );
      }
    },
    [urls],
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
      for (const url of filteredUrls) next.add(url);
      return next;
    });
  };

  const deselectAllFiltered = () => {
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      for (const url of filteredUrls) next.delete(url);
      return next;
    });
  };

  const applyRange = (newRange: [number, number]) => {
    setRange(newRange);
    const [min, max] = newRange;
    setSelectedUrls(new Set(urls.slice(min - 1, max)));
  };

  const BATCH_SIZE = 5;

  const handleScrape = async () => {
    const urlsToScrape = Array.from(selectedUrls);
    if (urlsToScrape.length === 0) return;

    setIsScraping(true);
    setBatchProgress({ current: 0, total: urlsToScrape.length });

    const totalBatches = Math.ceil(urlsToScrape.length / BATCH_SIZE);

    for (let batch = 0; batch < totalBatches; batch++) {
      const batchUrls = urlsToScrape.slice(batch * BATCH_SIZE, (batch + 1) * BATCH_SIZE);
      setBatchProgress({ current: Math.min(batch * BATCH_SIZE, urlsToScrape.length), total: urlsToScrape.length });

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 300000);
        const response = await fetch("/api/scraper/flipkart/scrape", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ urls: batchUrls }),
          signal: controller.signal,
        });
        clearTimeout(timeoutId);
        const data = await response.json();
        if (response.ok) {
          setProducts(prev => {
            const existing = new Map(prev.map(p => [p.url, p]));
            for (const p of data.products) existing.set(p.url, p);
            return Array.from(existing.values());
          });
        } else {
          const errMsg = data.error || "Unknown error";
          alert(`Batch ${batch + 1}/${totalBatches} failed: ${errMsg}`);
        }
      } catch (error: any) {
        console.error("Scrape error:", error);
        if (error?.name === "AbortError") {
          alert(`Batch ${batch + 1}/${totalBatches} timed out. Partial results are available for export.`);
        } else {
          alert(`Batch ${batch + 1}/${totalBatches} failed: ${error?.message || "Network error"}. Partial results are available for export.`);
        }
      }

      setBatchProgress({ current: Math.min((batch + 1) * BATCH_SIZE, urlsToScrape.length), total: urlsToScrape.length });
    }

    setIsScraping(false);
    setBatchProgress(null);
  };

  const handleExport = async () => {
    if (products.length === 0) return;
    setIsExporting(true);
    try {
      const response = await fetch("/api/scraper/flipkart/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ products }),
      });
      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "flipkart-products.xlsx";
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

  const handleReset = () => {
    setFile(null);
    setUrls([]);
    setFilteredUrls([]);
    setSelectedUrls(new Set());
    setProducts([]);
    setUploadStatus(null);
    setSearchQuery("");
    setRange([1, 1]);
    try { localStorage.removeItem(CACHE_KEY); } catch {}
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-green-50 to-slate-100 p-6">
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
            <CardTitle className="text-2xl font-bold text-green-800">
              Flipkart Product Scraper
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Step 1: Upload Excel */}
            <div
              className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
                isDragOver ? "border-green-500 bg-green-50" : "border-green-300"
              }`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
              <input
                type="file"
                accept=".xlsx,.xls"
                onChange={handleFileChange}
                className="hidden"
                id="file-upload"
              />
              <label
                htmlFor="file-upload"
                className="cursor-pointer flex flex-col items-center gap-2"
              >
                <Upload className="w-12 h-12 text-green-600" />
                <span className="text-lg font-medium text-green-800">
                  {file ? file.name : "Drop your Excel file here or click to browse"}
                </span>
                <span className="text-sm text-green-600">
                  Supports .xlsx and .xls files
                </span>
              </label>
              {file && (
                <Button onClick={handleUpload} disabled={isUploading} className="mt-4">
                  {isUploading ? (
                    <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2" />
                  ) : (
                    <Upload className="w-4 h-4 mr-2" />
                  )}
                  Upload and Extract URLs
                </Button>
              )}
            </div>

            {/* Upload Status */}
            {uploadStatus && (
              <div className="flex items-center gap-2 p-4 bg-green-100 text-green-800 rounded-lg">
                <CheckCircle2 className="w-5 h-5 shrink-0" />
                <span>
                  Extracted {uploadStatus.valid} valid URLs out of {uploadStatus.total}
                </span>
              </div>
            )}

            {/* Step 2: URL Preview & Selection */}
            {urls.length > 0 && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold text-green-800">
                    Preview & Select URLs
                  </h3>
                  <span className="text-sm text-green-600 bg-green-50 px-3 py-1 rounded-full">
                    {selectedUrls.size} of {urls.length} selected
                  </span>
                </div>

                {/* Search + Bulk Actions */}
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative flex-1 min-w-[200px]">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-green-500" />
                    <Input
                      placeholder="Filter URLs..."
                      value={searchQuery}
                      onChange={handleSearch}
                      className="pl-9 border-green-300 focus-visible:ring-green-500"
                    />
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={selectAllFiltered}
                    className="border-green-300 text-green-700"
                  >
                    <BadgeCheck className="h-4 w-4 mr-1" />
                    Select All
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={deselectAllFiltered}
                    className="border-green-300 text-green-700"
                  >
                    <ListChecks className="h-4 w-4 mr-1" />
                    Deselect All
                  </Button>
                </div>

                {/* Range Slider Filter */}
                {urls.length > 1 && (
                  <div className="border border-green-200 rounded-lg p-4 bg-green-50/50 space-y-3">
                    <div className="flex items-center gap-2 text-sm font-medium text-green-800">
                      <SlidersHorizontal className="h-4 w-4" />
                      Range Filter
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-green-600">From</label>
                        <Input
                          type="number"
                          min={1}
                          max={urls.length}
                          value={range[0]}
                          onChange={(e) => {
                            const v = Math.max(1, Math.min(Number(e.target.value) || 1, range[1]));
                            applyRange([v, range[1]]);
                          }}
                          className="w-20 h-8 text-center border-green-300"
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-green-600">To</label>
                        <Input
                          type="number"
                          min={1}
                          max={urls.length}
                          value={range[1]}
                          onChange={(e) => {
                            const v = Math.max(range[0], Math.min(Number(e.target.value) || 1, urls.length));
                            applyRange([range[0], v]);
                          }}
                          className="w-20 h-8 text-center border-green-300"
                        />
                      </div>
                      <span className="text-xs text-green-500">
                        ({range[1] - range[0] + 1} products)
                      </span>
                    </div>
                    <Slider
                      min={1}
                      max={urls.length}
                      step={1}
                      value={range}
                      onValueChange={(val) => applyRange(val as [number, number])}
                      className="w-full"
                    />
                    <div className="flex flex-wrap gap-1.5">
                      {[50, 100, 200, 500].filter(n => n <= urls.length).map((n) => {
                        const start = Math.min(n, urls.length);
                        return (
                          <Button
                            key={`preset-${n}`}
                            variant="outline"
                            size="sm"
                            onClick={() => applyRange([1, start])}
                            className="h-7 text-xs border-green-300 text-green-700"
                          >
                            1-{start}
                          </Button>
                        );
                      })}
                      {urls.length > 100 && Array.from({ length: Math.ceil(urls.length / 100) }, (_, i) => i).slice(1).map((i) => {
                        const start = i * 100 + 1;
                        const end = Math.min(start + 99, urls.length);
                        return (
                          <Button
                            key={`batch-${start}-${end}`}
                            variant="outline"
                            size="sm"
                            onClick={() => applyRange([start, end])}
                            className="h-7 text-xs border-green-300 text-green-700"
                          >
                            {start}-{end}
                          </Button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* URL List */}
                {filteredUrls.length > 0 ? (
                  <div className="border rounded-lg overflow-hidden max-h-[400px] overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-green-100 text-green-800 sticky top-0">
                        <tr>
                          <th className="w-10 px-3 py-2 text-center">
                            <input
                              type="checkbox"
                              className="accent-green-600"
                              checked={
                                filteredUrls.length > 0 &&
                                filteredUrls.every((u) => selectedUrls.has(u))
                              }
                              onChange={() => {
                                const allSelected = filteredUrls.every((u) =>
                                  selectedUrls.has(u),
                                );
                                if (allSelected) deselectAllFiltered();
                                else selectAllFiltered();
                              }}
                            />
                          </th>
                          <th className="w-12 px-2 py-2 text-center text-xs text-green-600 font-medium">
                            #
                          </th>
                          <th className="px-3 py-2 text-left text-xs text-green-600 font-medium">
                            URL
                          </th>
                          <th className="w-24 px-3 py-2 text-left text-xs text-green-600 font-medium">
                            Platform
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-green-100">
                        {filteredUrls.map((url, idx) => {
                          const platform = getPlatformLabel(url);
                          return (
                            <tr
                              key={url}
                              className={`hover:bg-green-50 transition-colors ${
                                selectedUrls.has(url) ? "bg-green-50/50" : ""
                              }`}
                            >
                              <td className="px-3 py-2 text-center">
                                <input
                                  type="checkbox"
                                  className="accent-green-600"
                                  checked={selectedUrls.has(url)}
                                  onChange={() => toggleUrl(url)}
                                />
                              </td>
                              <td className="px-2 py-2 text-center text-xs text-green-500 font-mono">
                                {urls.indexOf(url) + 1}
                              </td>
                              <td className="px-3 py-2">
                                <div className="flex items-center gap-2">
                                  <span className="truncate text-green-900 font-mono text-xs">
                                    {url}
                                  </span>
                                  <a
                                    href={url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="shrink-0 text-green-400 hover:text-green-600 transition-colors"
                                  >
                                    <ExternalLink className="h-3 w-3" />
                                  </a>
                                </div>
                              </td>
                              <td className="px-3 py-2">
                                <span
                                  className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${platform.color}`}
                                >
                                  {platform.label}
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 p-4 bg-yellow-50 text-yellow-700 rounded-lg">
                    <AlertCircle className="w-5 h-5" />
                    <span>No URLs match your search.</span>
                  </div>
                )}

                {/* Scrape Button */}
                <div className="flex items-center gap-3 pt-2">
                  <Button
                    onClick={handleScrape}
                    disabled={isScraping || selectedUrls.size === 0}
                    className="bg-green-600 hover:bg-green-700"
                    size="lg"
                  >
                    {isScraping ? (
                      <>
                        <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2" />
                        {batchProgress ? `Scraping ${batchProgress.current}/${batchProgress.total}` : `Scraping ${selectedUrls.size} Products...`}
                      </>
                    ) : (
                      <>
                        <RefreshCw className="h-4 w-4 mr-2" />
                        Scrape {selectedUrls.size} Selected Products
                      </>
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleReset}
                    className="text-green-600"
                  >
                    <RotateCcw className="h-4 w-4 mr-1" />
                    Start Over
                  </Button>
                </div>
              </div>
            )}

            {/* Scraping Progress */}
            {isScraping && batchProgress && (
              <div className="flex items-center gap-3 p-4 bg-blue-50 text-blue-800 rounded-lg">
                <div className="w-4 h-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent shrink-0" />
                <div className="flex-1">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-medium">
                      Scraping {batchProgress.current}/{batchProgress.total} products
                    </span>
                    <span className="text-xs text-blue-500">
                      Batch {Math.floor(batchProgress.current / 10) + 1}/{Math.ceil(batchProgress.total / 10)}
                    </span>
                  </div>
                  <div className="w-full bg-blue-200 rounded-full h-1.5">
                    <div
                      className="bg-blue-600 h-1.5 rounded-full transition-all duration-300"
                      style={{ width: `${(batchProgress.current / batchProgress.total) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Scraped Products */}
            {(products.length > 0 || isScraping) && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="w-5 h-5 text-green-600" />
                    <span className="text-lg font-semibold text-green-800">
                      {products.length} Product{products.length !== 1 ? "s" : ""} Scraped{isScraping && batchProgress ? ` (${batchProgress.current}/${batchProgress.total})` : ""}
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
                      className="text-green-600"
                    >
                      <RotateCcw className="h-4 w-4 mr-1" />
                      Start Over
                    </Button>
                  </div>
                </div>

                <div className="space-y-3">
                  {products.map((product) => (
                    <Card key={product.id} className={product.status === "blocked" ? "border-yellow-300 bg-yellow-50" : product.status === "failed" ? "border-red-300 bg-red-50" : ""}>
                      <CardContent className="p-4">
                        <div className="flex items-start gap-4">
                          {/* Image Gallery */}
                          <div className="shrink-0">
                            {product.images && product.images.length > 0 ? (
                              <div className="grid grid-cols-2 gap-1 w-32">
                                {product.images.slice(0, 4).map((img, i) => (
                                  <a key={i} href={img} target="_blank" rel="noopener noreferrer">
                                    <img
                                      src={img}
                                      alt={`${product.title} ${i + 1}`}
                                      className="w-14 h-14 object-cover rounded border border-green-200 hover:opacity-80 transition-opacity"
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
                              <div className="w-14 h-14 bg-green-100 rounded flex items-center justify-center text-green-400">
                                <AlertCircle className="w-5 h-5" />
                              </div>
                            )}
                          </div>

                          {/* Product Info */}
                          <div className="flex-1 min-w-0 space-y-1">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-medium text-green-900 text-sm leading-tight">
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
                            </div>

                            {product.description && (
                              <details className="w-full">
                                <summary className="text-xs text-green-500 cursor-pointer hover:text-green-700">
                                  Description
                                </summary>
                                <p className="text-xs text-green-600 mt-1">{product.description}</p>
                              </details>
                            )}
                            {product.meta_description && !product.description && (
                              <p className="text-xs text-green-600 line-clamp-2">{product.meta_description}</p>
                            )}

                            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-green-700">
                              {product.price && <span><span className="text-green-500">Price:</span> {product.price}</span>}
                              {product.dimensions && <span><span className="text-green-500">Dims:</span> {product.dimensions}</span>}
                              {product.weight && <span><span className="text-green-500">Weight:</span> {product.weight}</span>}
                              {product.gst && <span><span className="text-green-500">GST:</span> {product.gst}</span>}
                              {product.hsn && <span><span className="text-green-500">HSN:</span> {product.hsn}</span>}
                              {product.specifications && Object.keys(product.specifications).length > 0 && (
                                <details className="w-full mt-1">
                                  <summary className="text-xs text-green-500 cursor-pointer hover:text-green-700">
                                    Specifications ({Object.keys(product.specifications).length})
                                  </summary>
                                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 mt-1 text-xs">
                                    {Object.entries(product.specifications).slice(0, 20).map(([k, v]) => (
                                      <span key={k} className="text-green-600">
                                        <span className="text-green-400">{k}:</span> {v}
                                      </span>
                                    ))}
                                    {Object.keys(product.specifications).length > 20 && (
                                      <span className="text-green-400 col-span-2 italic">
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
                                View on Flipkart
                              </a>
                              {product.images && product.images.length > 1 && (
                                <span className="text-xs text-green-400">
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
