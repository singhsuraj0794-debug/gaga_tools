import { useState, useEffect } from "react";
import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Activity, ArrowLeft, BarChart3, Loader2, AlertTriangle, CheckCircle, MinusCircle } from "lucide-react";

interface MonitoringRun {
  id: string;
  run_at: string;
  page: string;
  metric: string;
  value: number | null;
  status: "pass" | "fail" | "degraded";
  step_failed: string | null;
  duration_ms: number | null;
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pass: "bg-green-100 text-green-800 border-green-200",
    fail: "bg-red-100 text-red-800 border-red-200",
    degraded: "bg-yellow-100 text-yellow-800 border-yellow-200",
  };
  const icons: Record<string, React.ReactNode> = {
    pass: <CheckCircle className="h-3.5 w-3.5" />,
    fail: <AlertTriangle className="h-3.5 w-3.5" />,
    degraded: <MinusCircle className="h-3.5 w-3.5" />,
  };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${colors[status] || "bg-gray-100 text-gray-800"}`}>
      {icons[status]} {status}
    </span>
  );
}

function MetricValue({ metric, value }: { metric: string; value: number | null }) {
  if (value === null) return <span className="text-gray-400">—</span>;
  const formatted = metric.includes("ms")
    ? `${value.toFixed(0)} ms`
    : metric === "cls"
    ? value.toFixed(3)
    : metric.includes("score")
    ? `${value.toFixed(1)}%`
    : value.toLocaleString();
  return <span className="font-mono font-medium">{formatted}</span>;
}

export default function MonitoringDashboard() {
  const [runs, setRuns] = useState<MonitoringRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function fetchRuns() {
    const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
    const supabaseKey = import.meta.env.VITE_SUPABASE_KEY;

    if (!supabaseUrl || !supabaseKey) {
      setError("VITE_SUPABASE_URL and VITE_SUPABASE_KEY env vars required");
      setLoading(false);
      return;
    }

    try {
      const url = new URL(`${supabaseUrl}/rest/v1/monitoring_runs`);
      url.searchParams.set("select", "*");
      url.searchParams.set("order", "run_at.desc");
      url.searchParams.set("limit", "200");
      const resp = await fetch(url.toString(), {
        headers: { apikey: supabaseKey, Authorization: `Bearer ${supabaseKey}` },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setRuns(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchRuns();
    const interval = setInterval(fetchRuns, 30000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="h-8 w-8 animate-spin mx-auto text-slate-400" />
          <p className="mt-4 text-slate-500">Loading monitoring data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-6">
        <div className="max-w-4xl mx-auto">
          <Link href="/" className="inline-flex items-center gap-2 text-slate-600 hover:text-slate-900 mb-6">
            <ArrowLeft className="h-4 w-4" /> Back to Home
          </Link>
          <Card className="border-red-200 bg-red-50">
            <CardContent className="pt-6">
              <div className="flex items-start gap-3">
                <AlertTriangle className="h-5 w-5 text-red-600 mt-0.5" />
                <div>
                  <h3 className="font-semibold text-red-800">Configuration Error</h3>
                  <p className="text-red-600 text-sm mt-1">{error}</p>
                  <p className="text-red-500 text-xs mt-2">
                    Set <code className="bg-red-100 px-1 rounded">VITE_SUPABASE_URL</code> and{" "}
                    <code className="bg-red-100 px-1 rounded">VITE_SUPABASE_KEY</code> in your .env.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  const latestRun = runs.length > 0 ? runs[0].run_at : null;
  const latestRuns = runs.filter((r) => r.run_at === latestRun);
  const passCount = latestRuns.filter((r) => r.status === "pass").length;
  const failCount = latestRuns.filter((r) => r.status === "fail").length;
  const degradedCount = latestRuns.filter((r) => r.status === "degraded").length;

  const byPage: Record<string, MonitoringRun[]> = {};
  for (const r of runs) {
    if (!byPage[r.page]) byPage[r.page] = [];
    byPage[r.page].push(r);
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <Link href="/" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700 mb-2">
              <ArrowLeft className="h-3.5 w-3.5" /> Back to Home
            </Link>
            <h1 className="text-3xl font-bold text-slate-900 flex items-center gap-3">
              <Activity className="h-7 w-7 text-indigo-600" />
              Synthetic Monitoring
            </h1>
            <p className="text-slate-500 text-sm mt-1">
              Core Web Vitals, Lighthouse scores, and happy-flow checks for gajab.com
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <Card>
            <CardContent className="pt-6 text-center">
              <div className="text-3xl font-bold text-slate-900">{runs.length > 0 ? new Set(runs.map(r => r.run_at)).size : 0}</div>
              <div className="text-xs text-slate-500 uppercase tracking-wider mt-1">Total Runs</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6 text-center">
              <div className={`text-3xl font-bold ${passCount > 0 ? "text-green-600" : "text-slate-400"}`}>{passCount}</div>
              <div className="text-xs text-slate-500 uppercase tracking-wider mt-1">Passing</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6 text-center">
              <div className={`text-3xl font-bold ${failCount > 0 ? "text-red-600" : "text-slate-400"}`}>{failCount}</div>
              <div className="text-xs text-slate-500 uppercase tracking-wider mt-1">Failing</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6 text-center">
              <div className={`text-3xl font-bold ${degradedCount > 0 ? "text-yellow-600" : "text-slate-400"}`}>{degradedCount}</div>
              <div className="text-xs text-slate-500 uppercase tracking-wider mt-1">Degraded</div>
            </CardContent>
          </Card>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
          {Object.entries(byPage).map(([page, pageRuns]) => {
            const pageLatest = pageRuns.filter((r) => r.run_at === latestRun);
            const pageStatus = pageLatest.some((r) => r.status === "fail")
              ? "fail" : pageLatest.some((r) => r.status === "degraded") ? "degraded" : "pass";

            const byMetric: Record<string, MonitoringRun> = {};
            for (const r of pageRuns) {
              if (!byMetric[r.metric]) byMetric[r.metric] = r;
            }

            return (
              <Card key={page} className="border-l-4 transition-shadow hover:shadow-md" style={{
                borderLeftColor: pageStatus === "fail" ? "#ef4444" : pageStatus === "degraded" ? "#eab308" : "#22c55e",
              }}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm font-semibold text-slate-700 uppercase tracking-wider">
                      {page.replace(/_/g, " ")}
                    </CardTitle>
                    <StatusBadge status={pageStatus} />
                  </div>
                </CardHeader>
                <CardContent className="pt-0">
                  {Object.entries(byMetric).slice(0, 8).map(([metric, run]) => (
                    <div key={metric} className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-b-0 text-sm">
                      <span className="text-slate-500">{metric}</span>
                      <div className="flex items-center gap-2">
                        <MetricValue metric={metric} value={run.value} />
                        <StatusBadge status={run.status} />
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            );
          })}
        </div>

        <Card className="mt-8">
          <CardHeader>
            <CardTitle className="text-sm font-semibold text-slate-700 uppercase tracking-wider flex items-center gap-2">
              <BarChart3 className="h-4 w-4" /> Recent Runs
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="text-left px-4 py-2 text-xs text-slate-500 uppercase tracking-wider">Time</th>
                    <th className="text-left px-4 py-2 text-xs text-slate-500 uppercase tracking-wider">Page</th>
                    <th className="text-left px-4 py-2 text-xs text-slate-500 uppercase tracking-wider">Metric</th>
                    <th className="text-right px-4 py-2 text-xs text-slate-500 uppercase tracking-wider">Value</th>
                    <th className="text-center px-4 py-2 text-xs text-slate-500 uppercase tracking-wider">Status</th>
                    <th className="text-left px-4 py-2 text-xs text-slate-500 uppercase tracking-wider">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.slice(0, 100).map((run) => (
                    <tr key={run.id} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="px-4 py-2 text-slate-600 whitespace-nowrap text-xs">
                        {new Date(run.run_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-2 font-medium text-slate-700">{run.page}</td>
                      <td className="px-4 py-2 text-slate-500">{run.metric}</td>
                      <td className="px-4 py-2 text-right font-mono">
                        <MetricValue metric={run.metric} value={run.value} />
                      </td>
                      <td className="px-4 py-2 text-center">
                        <StatusBadge status={run.status} />
                      </td>
                      <td className="px-4 py-2 text-slate-400 text-xs max-w-[200px] truncate">
                        {run.step_failed || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
