import { useState, useEffect, useMemo } from "react";
import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Activity, ArrowLeft, BarChart3, Loader2, AlertTriangle,
  CheckCircle, MinusCircle, Clock, Camera, Info, ExternalLink,
  ChevronDown, ChevronRight, ListChecks, Globe, Smartphone,
  Bug, Search, Image, ShoppingCart, DollarSign,
} from "lucide-react";

const _SUPABASE_URL = "https://okxyskmjsmtykblrtmyi.supabase.co";
const _SUPABASE_KEY = "sb_publishable_reTKPSKU-oZ9XkcfiTv96w_9zxMARBp";

interface MonitoringRun {
  id: string; run_at: string; page: string; metric: string;
  value: number | null; status: "pass" | "fail" | "degraded";
  step_failed: string | null; duration_ms: number | null;
  details: {
    screenshot_base64?: string; failure_reason?: string;
    console_errors?: {type:string;text:string}[];
    sub_steps?: {check:string;status:string;detail:string}[];
    detail?: string; url?: string; product_count?: number;
    screenshot_path?: string;
  } | null;
}

const METRIC_INFO: Record<string, string> = {
  performance_score: "Lighthouse Performance score (0-100). Higher is better. Measures how fast the page loads and responds.",
  lcp_ms: "Largest Contentful Paint in milliseconds. Measures perceived load speed. Target: <2500ms.",
  cls: "Cumulative Layout Shift score. Measures visual stability. Target: <0.1.",
  tbt_ms: "Total Blocking Time in milliseconds. Measures interactivity. Target: <300ms.",
  si_ms: "Speed Index in milliseconds. How quickly content is visually displayed. Target: <4000ms.",
  inp_ms: "Interaction to Next Paint in milliseconds. Measures responsiveness. Target: <200ms.",
  response_time_ms: "Time taken for the server to respond to a request.",
  status_code: "HTTP status code returned by the server. 200 = OK, 4xx/5xx = error.",
  step_home_load: "Time to load the gajab.com home page and verify the title contains 'Gajab'.",
  step_category_load: "Time to load the product listing page and verify product cards appear.",
  step_product_detail_load: "Time to load a product detail page and verify the price section exists.",
  step_bargain_flow: "Time to complete the full bargain flow: Start Bargaining, set offer, submit, accept.",
};

const STEP_ICONS: Record<string, React.ReactNode> = {
  home_load: <Globe className="h-4 w-4" />,
  category_load: <Search className="h-4 w-4" />,
  product_detail_load: <Image className="h-4 w-4" />,
  bargain_flow: <DollarSign className="h-4 w-4" />,
};

function InfoTooltip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex ml-1">
      <button
        className="text-slate-400 hover:text-slate-600 cursor-help transition-colors"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onClick={() => setShow(!show)}
      >
        <Info className="h-3.5 w-3.5" />
      </button>
      {show && (
        <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 bg-slate-800 text-white text-xs rounded-lg shadow-lg w-64 pointer-events-none">
          {text}
          <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-slate-800" />
        </div>
      )}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pass: "bg-green-100 text-green-800 border-green-200",
    fail: "bg-red-100 text-red-800 border-red-200",
    degraded: "bg-yellow-100 text-yellow-800 border-yellow-200",
  };
  const icons: Record<string, React.ReactNode> = {
    pass: <CheckCircle className="h-3 w-3" />,
    fail: <AlertTriangle className="h-3 w-3" />,
    degraded: <MinusCircle className="h-3 w-3" />,
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
    ? `${value.toFixed(1)}`
    : value.toLocaleString();
  return <span className="font-mono font-medium">{formatted}</span>;
}

function ScreenshotViewer({ base64, label }: { base64?: string; label: string }) {
  const [open, setOpen] = useState(false);
  if (!base64) return null;
  return (
    <div className="mt-2">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800">
        <Camera className="h-3 w-3" />
        {open ? "Hide screenshot" : "Show screenshot"}
      </button>
      {open && (
        <img src={base64} alt={label} className="mt-2 rounded-lg border border-slate-200 max-w-full max-h-64 object-contain" />
      )}
    </div>
  );
}

function RunTimeline({ runs }: { runs: MonitoringRun[] }) {
  const runGroups = useMemo(() => {
    const groups = new Map<string, MonitoringRun[]>();
    for (const r of runs) {
      const key = r.run_at;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(r);
    }
    return Array.from(groups.entries()).slice(0, 24);
  }, [runs]);

  return (
    <div className="space-y-3">
      {runGroups.map(([timestamp, groupRuns]) => {
        const time = new Date(timestamp);
        const happySteps = groupRuns.filter(r => r.page === "happy_flow" && !r.metric.includes("monitor"));
        const overall = happySteps.some(r => r.status === "fail") ? "fail"
          : happySteps.some(r => r.status === "degraded") ? "degraded" : "pass";
        const allMetrics = groupRuns.filter(r => r.page.startsWith("lighthouse/") || r.page === "happy_flow");

        return (
          <details key={timestamp} className="group">
            <summary className="flex items-center gap-3 p-3 rounded-lg border bg-white cursor-pointer hover:bg-slate-50 transition-colors">
              <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                overall === "pass" ? "bg-green-500" : overall === "degraded" ? "bg-yellow-500" : "bg-red-500"
              }`} />
              <Clock className="h-4 w-4 text-slate-400 shrink-0" />
              <span className="font-medium text-sm text-slate-700 min-w-[160px]">
                {time.toLocaleDateString()} {time.toLocaleTimeString()}
              </span>
              <StatusBadge status={overall} />
              <span className="text-xs text-slate-400">{groupRuns.length} metrics</span>
              <div className="ml-auto flex gap-1">
                {happySteps.map(s => {
                  const stepName = s.metric.replace("step_", "");
                  return (
                    <span key={s.metric} className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-slate-100">
                      {STEP_ICONS[stepName] || <ListChecks className="h-3 w-3" />}
                      <span className={s.status === "fail" ? "text-red-600" : s.status === "degraded" ? "text-yellow-600" : "text-green-600"}>
                        {s.duration_ms ? `${(s.duration_ms / 1000).toFixed(1)}s` : "—"}
                      </span>
                    </span>
                  );
                })}
              </div>
              <ChevronRight className="h-4 w-4 text-slate-400 group-open:rotate-90 transition-transform" />
            </summary>
            <div className="mt-2 pl-6 space-y-2">
              {allMetrics.map(r => {
                const stepName = r.metric.replace("step_", "");
                const isHappyStep = r.page === "happy_flow" && r.metric.startsWith("step_");
                const failure_reason = r.details?.failure_reason || r.step_failed;
                const screenshot = r.details?.screenshot_base64;
                const console_errors = r.details?.console_errors || [];
                const sub_steps = r.details?.sub_steps || [];

                return (
                  <Card key={r.id} className={`border-l-4 ${
                    r.status === "fail" ? "border-l-red-500" : r.status === "degraded" ? "border-l-yellow-500" : "border-l-green-500"
                  }`}>
                    <CardContent className="py-3 px-4">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            {isHappyStep && (STEP_ICONS[stepName] || <ListChecks className="h-4 w-4 text-slate-500" />)}
                            <span className="font-medium text-sm text-slate-800">
                              {isHappyStep ? stepName.replace(/_/g, " ") : r.metric}
                            </span>
                            <InfoTooltip text={METRIC_INFO[r.metric] || METRIC_INFO[stepName] || "Metric tracked during monitoring run."} />
                            <StatusBadge status={r.status} />
                          </div>

                          {r.details?.detail && (
                            <p className="text-xs text-slate-500 mt-1">{r.details.detail}</p>
                          )}

                          {failure_reason && (
                            <div className="flex items-start gap-1.5 mt-2 p-2 bg-red-50 rounded border border-red-100">
                              <AlertTriangle className="h-3.5 w-3.5 text-red-500 mt-0.5 shrink-0" />
                              <span className="text-xs text-red-700">{failure_reason}</span>
                            </div>
                          )}

                          {r.value !== null && (
                            <div className="flex items-center gap-2 mt-1">
                              <span className="text-xs text-slate-400">Value:</span>
                              <MetricValue metric={r.metric} value={r.value} />
                              {r.duration_ms && (
                                <>
                                  <span className="text-xs text-slate-300">·</span>
                                  <span className="text-xs text-slate-400">{r.duration_ms}ms total</span>
                                </>
                              )}
                            </div>
                          )}

                          {console_errors.length > 0 && (
                            <div className="mt-2">
                              <span className="text-xs font-medium text-orange-600 flex items-center gap-1">
                                <Bug className="h-3 w-3" /> Console errors: {console_errors.length}
                              </span>
                              {console_errors.slice(0, 3).map((ce: {text:string}, i: number) => (
                                <p key={i} className="text-xs text-orange-700 truncate ml-4" title={ce.text}>{ce.text}</p>
                              ))}
                            </div>
                          )}

                          {sub_steps.length > 0 && (
                            <div className="mt-2 space-y-0.5">
                              <span className="text-xs font-medium text-slate-500 flex items-center gap-1">
                                <ListChecks className="h-3 w-3" /> Sub-steps
                              </span>
                              {sub_steps.map((ss: {check:string;status:string;detail:string}, i: number) => (
                                <div key={i} className="flex items-center gap-2 text-xs ml-2">
                                  {ss.status === "pass" ? (
                                    <CheckCircle className="h-3 w-3 text-green-500" />
                                  ) : ss.status === "degraded" ? (
                                    <MinusCircle className="h-3 w-3 text-yellow-500" />
                                  ) : (
                                    <AlertTriangle className="h-3 w-3 text-red-500" />
                                  )}
                                  <span className="text-slate-600">{ss.check}</span>
                                  {ss.detail && <span className="text-slate-400">— {ss.detail}</span>}
                                </div>
                              ))}
                            </div>
                          )}

                          {r.details?.screenshot_path && (
                            <p className="text-xs text-slate-400 mt-1">
                              📁 {r.details.screenshot_path}
                            </p>
                          )}
                        </div>
                        <div className="shrink-0">
                          {screenshot && <ScreenshotViewer base64={screenshot} label={stepName} />}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          </details>
        );
      })}
    </div>
  );
}

export default function MonitoringDashboard() {
  const [runs, setRuns] = useState<MonitoringRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("all");

  async function fetchRuns() {
    const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || _SUPABASE_URL;
    const supabaseKey = import.meta.env.VITE_SUPABASE_KEY || _SUPABASE_KEY;
    if (!supabaseUrl || !supabaseKey) return;
    try {
      const url = new URL(`${supabaseUrl}/rest/v1/monitoring_runs`);
      url.searchParams.set("select", "*");
      url.searchParams.set("order", "run_at.desc");
      url.searchParams.set("limit", "500");
      const resp = await fetch(url.toString(), {
        headers: { apikey: supabaseKey, Authorization: `Bearer ${supabaseKey}` },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setRuns(await resp.json());
    } catch (e: any) {
      console.error("Supabase fetch error:", e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchRuns();
    const interval = setInterval(fetchRuns, 30000);
    return () => clearInterval(interval);
  }, []);

  const latestRun = runs.length > 0 ? runs[0].run_at : null;
  const latest = runs.filter(r => r.run_at === latestRun);
  const passCount = latest.filter(r => r.status === "pass").length;
  const failCount = latest.filter(r => r.status === "fail").length;
  const degCount = latest.filter(r => r.status === "degraded").length;

  const uniqueRunCount = new Set(runs.map(r => r.run_at)).size;
  const totalHappySteps = runs.filter(r => r.page === "happy_flow" && r.metric.startsWith("step_")).length;
  const failedHappySteps = runs.filter(r => r.page === "happy_flow" && r.metric.startsWith("step_") && r.status === "fail").length;
  const lastRunTime = runs[0] ? new Date(runs[0].run_at) : null;

  const happyFlowRuns = runs.filter(r => r.page === "happy_flow" && r.metric.startsWith("step_"));
  const filteredRuns = filter === "all" ? runs : filter === "happy" ? happyFlowRuns : runs;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <Link href="/" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700 mb-1">
              <ArrowLeft className="h-3.5 w-3.5" /> Back
            </Link>
            <h1 className="text-2xl sm:text-3xl font-bold text-slate-900 flex items-center gap-3">
              <Activity className="h-6 w-6 sm:h-7 sm:w-7 text-indigo-600" />
              Synthetic Monitor
            </h1>
            <p className="text-sm text-slate-500">
              gajab.com — hourly checks via Playwright + Lighthouse
              {lastRunTime && <span className="ml-2">· Last run: {lastRunTime.toLocaleTimeString()}</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              className="text-xs border border-slate-200 rounded-md px-2 py-1.5 bg-white text-slate-600"
              value={filter} onChange={e => setFilter(e.target.value)}
            >
              <option value="all">All metrics</option>
              <option value="happy">Happy flow only</option>
            </select>
            <button onClick={fetchRuns} className="text-xs px-3 py-1.5 bg-white border border-slate-200 rounded-md text-slate-600 hover:bg-slate-50">
              Refresh
            </button>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-24">
            <Loader2 className="h-8 w-8 animate-spin text-slate-300" />
          </div>
        ) : runs.length === 0 ? (
          <Card className="border-dashed border-2">
            <CardContent className="py-12 text-center text-slate-400">
              <Activity className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p className="font-medium">No monitoring runs yet</p>
              <p className="text-sm mt-1">The hourly GitHub Action will produce the first results within 60 minutes.</p>
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
              <StatCard value={uniqueRunCount} label="Total Runs" />
              <StatCard value={passCount} label="Passing (latest)" color="green" />
              <StatCard value={failCount} label="Failing (latest)" color="red" />
              <StatCard value={degCount} label="Degraded (latest)" color="yellow" />
            </div>

            {failCount > 0 && (
              <div className="flex items-center gap-2 p-3 mb-4 bg-red-50 border border-red-200 rounded-lg">
                <AlertTriangle className="h-4 w-4 text-red-500 shrink-0" />
                <span className="text-sm text-red-700">
                  {failCount} check{failCount !== 1 ? "s" : ""} failing in the latest run
                  {failedHappySteps > 0 && ` (${failedHappySteps}/${totalHappySteps} happy flow steps)`}
                </span>
              </div>
            )}

            <RunTimeline runs={filteredRuns} />
          </>
        )}
      </div>
    </div>
  );
}

function StatCard({ value, label, color }: { value: number | string; label: string; color?: string }) {
  const colors: Record<string, string> = {
    green: "text-green-600", red: "text-red-600", yellow: "text-yellow-600",
  };
  return (
    <Card>
      <CardContent className="py-3 text-center">
        <div className={`text-2xl font-bold ${color ? colors[color] : "text-slate-800"}`}>{value}</div>
        <div className="text-xs text-slate-500 uppercase tracking-wider mt-0.5">{label}</div>
      </CardContent>
    </Card>
  );
}
