import { useState, useEffect } from "react";
import { Link } from "wouter";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { API_BASE } from "@/lib/api";
import {
  Activity,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Globe,
  ArrowLeft,
  Loader2,
} from "lucide-react";

interface MonitorStep {
  name: string;
  status: string;
  duration: number;
  error?: string;
}

interface MonitorResult {
  passed: number;
  failed: number;
  steps: MonitorStep[];
  timestamp: string;
  overall: string;
}

export default function MonitoringDashboard() {
  const [result, setResult] = useState<MonitorResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function fetchResults() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/monitor/results`);
      if (!res.ok) throw new Error("API error");
      const data = await res.json();
      setResult(data);
    } catch {
      setError("Unable to fetch monitor results");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchResults();
    const interval = setInterval(fetchResults, 30000);
    return () => clearInterval(interval);
  }, []);

  const isHealthy = result?.overall === "pass";
  const totalSteps = result ? result.passed + result.failed : 0;
  const passRate = totalSteps > 0 ? Math.round((result!.passed / totalSteps) * 100) : 0;
  const lastRun = result?.timestamp ? new Date(result.timestamp + "Z") : null;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-4">
            <Link href="/">
              <Button variant="ghost" size="sm" className="text-slate-500">
                <ArrowLeft className="w-4 h-4 mr-1" /> Back
              </Button>
            </Link>
            <div>
              <h1 className="text-3xl font-bold text-slate-900">Synthetic Monitor</h1>
              <p className="text-sm text-slate-500">Gajab.com happy flow — hourly checks via Playwright</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-[10px]">
              Hourly
            </Badge>
            <Button variant="outline" size="sm" onClick={fetchResults} disabled={loading}>
              <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        </div>

        {/* Overall Status */}
        <Card className={`mb-6 ${!result ? "border-slate-200" : isHealthy ? "border-green-300 bg-green-50" : "border-red-300 bg-red-50"}`}>
          <CardContent className="py-6">
            <div className="flex items-center gap-4">
              {loading ? (
                <Loader2 className="w-8 h-8 text-slate-400 animate-spin" />
              ) : !result || result.overall === "never" ? (
                <Globe className="w-8 h-8 text-slate-400" />
              ) : isHealthy ? (
                <CheckCircle2 className="w-8 h-8 text-green-600" />
              ) : (
                <AlertCircle className="w-8 h-8 text-red-600" />
              )}
              <div className="flex-1">
                <p className={`text-lg font-bold ${!result ? "text-slate-600" : isHealthy ? "text-green-800" : "text-red-800"}`}>
                  {loading ? "Checking..." : !result || result.overall === "never" ? "No checks yet — waiting for first run" : isHealthy ? "All Happy Flow Checks Passing" : "Happy Flow Checks Failing"}
                </p>
                <p className="text-sm text-slate-500">
                  {result && result.overall !== "never"
                    ? `Last run: ${lastRun?.toLocaleString() || "—"} · ${result.passed}/${totalSteps} steps passed (${passRate}%)`
                    : "GitHub Action runs hourly — first result appears within 60 min"}
                </p>
              </div>
              {result && result.overall !== "never" && (
                <div className="text-right">
                  <p className={`text-2xl font-bold ${isHealthy ? "text-green-600" : "text-red-600"}`}>
                    {isHealthy ? "PASS" : "FAIL"}
                  </p>
                  <p className="text-xs text-slate-400">{result.passed}p / {result.failed}f</p>
                </div>
              )}
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

        {/* Stats */}
        {result && result.overall !== "never" && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
              <Card>
                <CardContent className="py-4 text-center">
                  <p className="text-2xl font-bold text-slate-800">{passRate}%</p>
                  <p className="text-xs text-slate-500">Pass Rate</p>
                  <Progress value={passRate} className="mt-2 h-1.5" />
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-4 text-center">
                  <p className="text-2xl font-bold text-green-600">{result.passed}</p>
                  <p className="text-xs text-slate-500">Passed Steps</p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-4 text-center">
                  <p className="text-2xl font-bold text-red-600">{result.failed}</p>
                  <p className="text-xs text-slate-500">Failed Steps</p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-4 text-center">
                  <p className="text-2xl font-bold text-slate-800">{totalSteps}</p>
                  <p className="text-xs text-slate-500">Total Steps</p>
                </CardContent>
              </Card>
            </div>

            {/* Steps Timeline */}
            <Card>
              <CardContent className="py-4">
                <h2 className="text-sm font-semibold text-slate-800 mb-4 flex items-center gap-2">
                  <Activity className="w-4 h-4" /> Check Steps
                </h2>
                <div className="space-y-2">
                  {result.steps.map((step, i) => (
                    <div key={i} className="flex items-center gap-3 p-3 rounded-lg border bg-white">
                      {step.status === "pass" ? (
                        <CheckCircle2 className="w-5 h-5 text-green-500 shrink-0" />
                      ) : (
                        <AlertCircle className="w-5 h-5 text-red-500 shrink-0" />
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-slate-800">{step.name}</span>
                          <Badge variant={step.status === "pass" ? "secondary" : "destructive"} className="text-[10px] h-5">
                            {step.status === "pass" ? "Pass" : "Fail"}
                          </Badge>
                        </div>
                        <div className="flex items-center gap-3 text-xs text-slate-400 mt-0.5">
                          <span>{step.duration}s</span>
                          {step.error && <span className="text-red-400 truncate">{step.error}</span>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
