import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Search, Download, ArrowLeft, AlertCircle, CheckCircle2 } from "lucide-react";
import { Link } from "wouter";
import { searchEcommerceProducts } from "@workspace/api-client-react";
import type { EcommerceProduct } from "@workspace/api-zod";

export default function Scraper({ initialPlatform = "flipkart" }: { initialPlatform?: "flipkart" | "amazon" | "meesho" }) {
  const [query, setQuery] = useState("");
  const [platform, setPlatform] = useState<"flipkart" | "amazon" | "meesho">(initialPlatform);
  const [products, setProducts] = useState<EcommerceProduct[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);

  const searchMutation = useMutation({
    mutationFn: async () => {
      const response = await searchEcommerceProducts({ query, platform });
      return response;
    },
    onSuccess: (data) => {
      setProducts(data.products || []);
      setWarnings(data.warnings || []);
    },
  });

  const handleExport = async () => {
    try {
      const response = await fetch("/api/scraper/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ products, filename: `${platform}-products.xlsx` }),
      });

      if (!response.ok) throw new Error("Export failed");

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${platform}-products.xlsx`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      console.error("Export error:", error);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-6">
      <div className="max-w-7xl mx-auto space-y-6">
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
            <CardTitle className="text-2xl font-bold">Product Scraper</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="space-y-2">
                  <Label>Platform</Label>
                  <Select
                    value={platform}
                    onValueChange={(value: any) => setPlatform(value)}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select platform" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="flipkart">Flipkart</SelectItem>
                      <SelectItem value="amazon">Amazon (Coming Soon)</SelectItem>
                      <SelectItem value="meesho">Meesho (Coming Soon)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2 md:col-span-2">
                  <Label>Search Query</Label>
                  <div className="flex gap-2">
                    <Input
                      placeholder="Search for products..."
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && searchMutation.mutate()}
                    />
                    <Button
                      onClick={() => searchMutation.mutate()}
                      disabled={!query || searchMutation.isPending}
                    >
                      {searchMutation.isPending ? (
                        <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                      ) : (
                        <Search className="h-4 w-4 mr-2" />
                      )}
                      Search
                    </Button>
                  </div>
                </div>
              </div>

              {warnings.length > 0 && (
                <Alert variant="destructive">
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>
                    {warnings.map((warning, i) => (
                      <div key={i}>{warning}</div>
                    ))}
                  </AlertDescription>
                </Alert>
              )}

              {products.length > 0 && (
                <div className="space-y-4">
                  <div className="flex justify-between items-center">
                    <Badge variant="outline" className="px-3 py-1">
                      {products.length} product{products.length !== 1 ? "s" : ""} found
                    </Badge>
                    <Button onClick={handleExport}>
                      <Download className="h-4 w-4 mr-2" />
                      Export to Excel
                    </Button>
                  </div>

                  <ScrollArea className="h-[500px] border rounded-lg">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Image</TableHead>
                          <TableHead>Title</TableHead>
                          <TableHead>Description</TableHead>
                          <TableHead>Price</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {products.map((product) => (
                          <TableRow key={product.id}>
                            <TableCell>
                              {product.imageUrl ? (
                                <img
                                  src={product.imageUrl}
                                  alt={product.title}
                                  className="w-16 h-16 object-cover rounded"
                                />
                              ) : (
                                <div className="w-16 h-16 bg-slate-200 rounded flex items-center justify-center">
                                  <span className="text-slate-400 text-xs">No image</span>
                                </div>
                              )}
                            </TableCell>
                            <TableCell className="font-medium max-w-xs truncate">
                              {product.title}
                            </TableCell>
                            <TableCell className="max-w-md truncate text-slate-500">
                              {product.description || "No description"}
                            </TableCell>
                            <TableCell>{product.price || "-"}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </ScrollArea>
                </div>
              )}

              {products.length === 0 && !searchMutation.isPending && (
                <div className="text-center py-12">
                  <CheckCircle2 className="h-12 w-12 mx-auto text-slate-300 mb-4" />
                  <p className="text-slate-500">Search for products to get started</p>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
