import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ArrowLeft, Upload, Download, RefreshCw, CheckCircle2, AlertCircle } from "lucide-react";
import { Link } from "wouter";

interface FlipkartDetailedProduct {
  id: string;
  title: string;
  description: string | null;
  imageUrl: string | null;
  hsn: string | null;
  gst: string | null;
  dimensions: string | null;
  weight: string | null;
  variants: string | null;
  price: string | null;
  url: string;
}

export default function FlipkartScraper() {
  const [file, setFile] = useState<File | null>(null);
  const [urls, setUrls] = useState<string[]>([]);
  const [products, setProducts] = useState<FlipkartDetailedProduct[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isScraping, setIsScraping] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<{ total: number; valid: number } | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
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
        setUploadStatus({ total: data.totalUrls, valid: data.validUrls });
      } else {
        alert("Failed to upload file: " + data.error);
      }
    } catch (error) {
      console.error("Upload error:", error);
      alert("Failed to upload file");
    } finally {
      setIsUploading(false);
    }
  };

  const handleScrape = async () => {
    if (urls.length === 0) return;
    setIsScraping(true);
    try {
      const response = await fetch("/api/scraper/flipkart/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ urls }),
      });
      const data = await response.json();
      if (response.ok) {
        setProducts(data.products);
      } else {
        alert("Failed to scrape products: " + data.error);
      }
    } catch (error) {
      console.error("Scrape error:", error);
      alert("Failed to scrape products");
    } finally {
      setIsScraping(false);
    }
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
            <div className="border-2 border-dashed border-green-300 rounded-lg p-8 text-center">
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
                  {file ? file.name : "Click to upload Excel file with product links"}
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
                <CheckCircle2 className="w-5 h-5" />
                <span>
                  Extracted {uploadStatus.valid} valid URLs out of {uploadStatus.total}
                </span>
              </div>
            )}

            {/* Step 2: Scrape Products */}
            {urls.length > 0 && (
              <div className="space-y-4">
                <div className="flex items-center gap-4">
                  <Button
                    onClick={handleScrape}
                    disabled={isScraping || products.length > 0}
                    className="bg-green-600 hover:bg-green-700"
                  >
                    {isScraping ? (
                      <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2" />
                    ) : (
                      <RefreshCw className="w-4 h-4 mr-2" />
                    )}
                    Scrape {urls.length} Products
                  </Button>
                  {products.length > 0 && (
                    <Button
                      onClick={handleExport}
                      disabled={isExporting}
                      className="bg-blue-600 hover:bg-blue-700"
                    >
                      {isExporting ? (
                        <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2" />
                      ) : (
                        <Download className="w-4 h-4 mr-2" />
                      )}
                      Export {products.length} Products to Excel
                    </Button>
                  )}
                </div>
              </div>
            )}

            {/* Scraped Products */}
            {products.length > 0 && (
              <div className="border rounded-lg overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-green-100 text-green-800">
                      <tr>
                        <th className="px-4 py-2 text-left">Title</th>
                        <th className="px-4 py-2 text-left">Price</th>
                        <th className="px-4 py-2 text-left">HSN</th>
                        <th className="px-4 py-2 text-left">GST</th>
                        <th className="px-4 py-2 text-left">Dimensions</th>
                        <th className="px-4 py-2 text-left">Weight</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {products.map((product) => (
                        <tr key={product.id} className="hover:bg-slate-50">
                          <td className="px-4 py-2 max-w-xs truncate">
                            {product.title}
                          </td>
                          <td className="px-4 py-2">{product.price || "-"}</td>
                          <td className="px-4 py-2">{product.hsn || "-"}</td>
                          <td className="px-4 py-2">{product.gst || "-"}</td>
                          <td className="px-4 py-2">{product.dimensions || "-"}</td>
                          <td className="px-4 py-2">{product.weight || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
