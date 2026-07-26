import { useState, useEffect } from "react";
import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { API_BASE } from "@/lib/api";
import {
  Activity,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Database,
  ShoppingBag,
  ShoppingCart,
  Package,
  BadgePercent,
  Video,
  ArrowLeft,
  Loader2,
} from "lucide-react";

interface HealthStatus {
  api: string;
  products?: number;
  scrapers?: Record<string, string>;
  uptime?: string;
}

export default function MonitoringDashboard() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function fetchHealth() {
    setLoading(true);
    setError(null);
    try {
      const [healthRes, statusRes] = await Promise.all([
        fetch(`${API_BASE}/api/healthz`).then((r) => r.json()).catch(() => ({ status: "down" })),
        fetch(`${API_BASE}/api/products/status`).then((r) => r.json()).catch(() => ({})),
      ]);
      setHealth({
        api: healthRes.status || "ok",
        products: statusRes.supabase_total || statusRes.count || statusRes.total || 0,
      });
    } catch {
      setHealth({ api: "down" });
      setError("Unable to reach backend");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 60000);
    return () => clearInterval(interval);
  }, []);

  const isHealthy = health?.api === "ok";

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-4">
            <Link href="/">
              <Button variant="ghost" size="sm" className="text-slate-500">
                <ArrowLeft className="w-4 h-4 mr-1" /> Back
              </Button>
            </Link>
            <div>
              <h1 className="text-3xl font-bold text-slate-900">Monitoring Dashboard</h1>
              <p className="text-sm text-slate-500">Synthetic monitoring & system health</p>
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={fetchHealth} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>

        {/* Status Banner */}
        <Card className={`mb-6 ${isHealthy ? "border-green-300 bg-green-50" : "border-red-300 bg-red-50"}`}>
          <CardContent className="py-4">
            <div className="flex items-center gap-3">
              {loading ? (
                <Loader2 className="w-6 h-6 text-slate-400 animate-spin" />
              ) : isHealthy ? (
                <CheckCircle2 className="w-6 h-6 text-green-600" />
              ) : (
                <AlertCircle className="w-6 h-6 text-red-600" />
              )}
              <div>
                <p className={`font-semibold ${isHealthy ? "text-green-800" : "text-red-800"}`}>
                  {loading ? "Checking..." : isHealthy ? "All Systems Operational" : "Service Outage Detected"}
                </p>
                <p className={`text-sm ${isHealthy ? "text-green-600" : "text-red-600"}`}>
                  API: {health?.api} · Products: {health?.products?.toLocaleString() || "—"}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {error && (
          <Card className="mb-6 border-yellow-300 bg-yellow-50">
            <CardContent className="py-3 text-sm text-yellow-700 flex items-center gap-2">
              <AlertCircle className="w-4 h-4" /> {error}
            </CardContent>
          </Card>
        )}

        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <Card>
            <CardContent className="py-4 text-center">
              <Database className="w-8 h-8 mx-auto mb-2 text-indigo-500" />
              <p className="text-2xl font-bold text-slate-800">{health?.products?.toLocaleString() || "—"}</p>
              <p className="text-xs text-slate-500">Products in Database</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-4 text-center">
              <Badge className={`${isHealthy ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"} border-0 text-sm px-4 py-2`}>
                {loading ? "..." : isHealthy ? "Online" : "Offline"}
              </Badge>
              <p className="text-xs text-slate-500 mt-2">API Status</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-4 text-center">
              <Activity className="w-8 h-8 mx-auto mb-2 text-rose-500" />
              <p className="text-2xl font-bold text-slate-800">Hourly</p>
              <p className="text-xs text-slate-500">Check Frequency</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="py-4 text-center">
              <CheckCircle2 className="w-8 h-8 mx-auto mb-2 text-green-500" />
              <p className="text-xs text-slate-500">Last checked: {new Date().toLocaleTimeString()}</p>
            </CardContent>
          </Card>
        </div>

        {/* Quick Links */}
        <h2 className="text-lg font-semibold text-slate-800 mb-3">Quick Actions</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {[
            { title: "Video Finder", icon: <Video className="w-4 h-4" />, path: "/video-finder", color: "text-blue-600 bg-blue-50 hover:bg-blue-100" },
            { title: "Price Mapper", icon: <BadgePercent className="w-4 h-4" />, path: "/price-mapper", color: "text-indigo-600 bg-indigo-50 hover:bg-indigo-100" },
            { title: "Meesho", icon: <ShoppingBag className="w-4 h-4" />, path: "/meesho-scraper", color: "text-orange-600 bg-orange-50 hover:bg-orange-100" },
            { title: "Flipkart", icon: <ShoppingCart className="w-4 h-4" />, path: "/flipkart-scraper", color: "text-green-600 bg-green-50 hover:bg-green-100" },
            { title: "Amazon", icon: <Package className="w-4 h-4" />, path: "/amazon-scraper", color: "text-yellow-600 bg-yellow-50 hover:bg-yellow-100" },
            { title: "Home", icon: <ArrowLeft className="w-4 h-4" />, path: "/", color: "text-slate-600 bg-slate-50 hover:bg-slate-100" },
          ].map((item) => (
            <Link key={item.title} href={item.path}>
              <Card className={`cursor-pointer ${item.color} border-0 transition-all hover:shadow`}>
                <CardContent className="py-3 flex items-center gap-2 justify-center text-sm font-medium">
                  {item.icon} {item.title}
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
