import { useState, useEffect, useMemo } from "react";
import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Activity, ArrowLeft, BarChart3, Loader2, AlertTriangle,
  CheckCircle, MinusCircle, Clock, Camera, Info, Globe,
  Bug, Search, Image, DollarSign, Server, Cpu, ShoppingCart,
  Smartphone, Monitor, Apple,
} from "lucide-react";

const _SUPABASE_URL = "https://okxyskmjsmtykblrtmyi.supabase.co";
const _SUPABASE_KEY = "sb_publishable_reTKPSKU-oZ9XkcfiTv96w_9zxMARBp";
const REFRESH_INTERVAL = 30000;

interface Run {
  id: string; run_at: string; page: string; metric: string;
  run_id?: string | null;
  value: number | null; status: "pass" | "fail" | "degraded";
  step_failed: string | null; duration_ms: number | null;
  details?: {
    screenshot_base64?: string; screenshot_url?: string; failure_reason?: string;
    console_errors?: {type:string;text:string}[];
    sub_steps?: {check:string;status:string;detail:string}[];
    detail?: string; url?: string; product_count?: number;
    session_recording_url?: string;
    rca?: { summary?: string; causes?: string[]; actions?: string[] };
  };
}

function runKey(r: Run): string { return r.run_id || r.run_at; }

const METRIC_INFO: Record<string, string> = {
  performance_score: "Lighthouse Performance score (0-100). Measures page load speed.",
  lcp_ms: "Largest Contentful Paint (ms). Perceived load speed. Target: <2500ms.",
  cls: "Cumulative Layout Shift. Visual stability. Target: <0.1.",
  tbt_ms: "Total Blocking Time (ms). Interactivity. Target: <300ms.",
  si_ms: "Speed Index (ms). How fast content displays. Target: <4000ms.",
  inp_ms: "Interaction to Next Paint (ms). Responsiveness. Target: <200ms.",
  response_time_ms: "Server response time (ms).",
  step_home_load: "Home page load time + title verification.",
  step_category_load: "Category page load time + product count check.",
  step_product_detail_load: "Product detail load time + price section check.",
  step_bargain_flow: "Bargain flow: click Start, set offer, submit, accept.",
  step_checkout_flow: "Add to cart, navigate checkout, verify Razorpay payment gateway opens.",
};

function InfoTip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex ml-1 align-middle">
      <span className="text-slate-300 hover:text-slate-500 cursor-help transition-colors"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}>
        <Info className="h-3 w-3 inline" />
      </span>
      {show && <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2.5 py-1.5 bg-slate-800 text-white text-xs rounded-lg shadow-lg w-56 pointer-events-none leading-relaxed">{text}</div>}
    </span>
  );
}

function Badge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pass: "bg-green-100 text-green-700 border-green-200",
    fail: "bg-red-100 text-red-700 border-red-200",
    degraded: "bg-yellow-100 text-yellow-700 border-yellow-200",
  };
  const icons: Record<string, React.ReactNode> = {
    pass: <CheckCircle className="h-3 w-3" />,
    fail: <AlertTriangle className="h-3 w-3" />,
    degraded: <MinusCircle className="h-3 w-3" />,
  };
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium border ${colors[status] || ""}`}>
      {icons[status]} {status}
    </span>
  );
}

function Val({ metric, value }: { metric: string; value: number | null }) {
  if (value === null) return <span className="text-gray-300">—</span>;
  const fmt = metric.includes("ms") ? `${value.toFixed(0)}ms`
    : metric === "cls" ? value.toFixed(3)
    : metric.includes("score") ? `${value.toFixed(0)}`
    : String(value);
  return <span className="font-mono text-sm font-medium">{fmt}</span>;
}

function VideoPlayer({ url, label }: { url?: string; label: string }) {
  const [open, setOpen] = useState(false);
  if (!url) return null;
  return (
    <div className="mt-1.5">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-xs text-indigo-500 hover:text-indigo-700">
        ▶ {open ? "Hide" : "Watch"} session recording
      </button>
      {open && (
        <video controls className="mt-1.5 rounded border max-w-full max-h-64 bg-black">
          <source src={url} type="video/webm" />
        </video>
      )}
    </div>
  );
}

function Screenshot({ base64, url, label }: { base64?: string; url?: string; label: string }) {
  const [open, setOpen] = useState(false);
  const src = url || base64;
  if (!src) return null;
  return (
    <div className="mt-1.5">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-xs text-indigo-500 hover:text-indigo-700">
        <Camera className="h-3 w-3" /> {open ? "Hide" : "View"} screenshot
      </button>
      {open && <img src={src} alt={label} className="mt-1.5 rounded border max-w-full max-h-48 object-contain bg-white" loading="lazy" />}
    </div>
  );
}

function MetricRow({ metric, value, status, detail, tip }: {
  metric: string; value: number | null; status: string;
  detail?: React.ReactNode; tip?: string;
}) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-slate-100 last:border-b-0 text-sm">
      <span className="text-slate-600 flex items-center gap-0.5">
        {metric.replace(/_/g, " ")}
        {tip && <InfoTip text={tip} />}
      </span>
      <div className="flex items-center gap-2">
        <Val metric={metric} value={value} />
        <Badge status={status} />
      </div>
    </div>
  );
}

function StepCard({ run, stepName, icon }: { run: Run; stepName: string; icon: React.ReactNode }) {
  const [showConsole, setShowConsole] = useState(false);
  return (
    <Card className={`border-l-4 ${run.status === "fail" ? "border-l-red-500" : run.status === "degraded" ? "border-l-yellow-500" : "border-l-green-500"}`}>
      <CardContent className="p-3 sm:p-4">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 text-slate-400">{icon}</div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-sm text-slate-800">{stepName}</span>
              <Badge status={run.status} />
              <span className="text-xs text-slate-400 ml-auto">{run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : "—"}</span>
            </div>
            {run.details?.detail && <p className="text-xs text-slate-500 mt-0.5">{run.details.detail}</p>}
            {run.details?.failure_reason && (
              <div className="flex items-start gap-1.5 mt-1.5 p-1.5 bg-red-50 rounded text-xs text-red-700">
                <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" /> {run.details.failure_reason}
              </div>
            )}
            {run.details?.rca?.summary && (
              <div className="mt-1.5 p-2 bg-orange-50 border border-orange-200 rounded text-xs">
                <div className="font-medium text-orange-800 mb-0.5">🔍 RCA: {run.details.rca.summary}</div>
                {run.details.rca.causes && run.details.rca.causes.length > 0 && (
                  <div className="mt-1 text-orange-700">
                    <span className="font-medium">Causes:</span>
                    <ul className="list-disc pl-4 mt-0.5 space-y-0.5">
                      {run.details.rca.causes.map((c, i) => <li key={i}>{c}</li>)}
                    </ul>
                  </div>
                )}
                {run.details.rca.actions && run.details.rca.actions.length > 0 && (
                  <div className="mt-1 text-orange-700">
                    <span className="font-medium">Actions:</span>
                    <ul className="list-disc pl-4 mt-0.5 space-y-0.5">
                      {run.details.rca.actions.map((a, i) => <li key={i}>{a}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}
            {run.details?.sub_steps && run.details.sub_steps.length > 0 && (
              <div className="mt-1.5 space-y-0.5">
                {run.details.sub_steps.map((s, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-xs text-slate-500">
                    {s.status === "pass" ? <CheckCircle className="h-3 w-3 text-green-500 shrink-0" />
                      : s.status === "degraded" ? <MinusCircle className="h-3 w-3 text-yellow-500 shrink-0" />
                      : <AlertTriangle className="h-3 w-3 text-red-500 shrink-0" />}
                    <span>{s.check}</span>
                    {s.detail && <span className="text-slate-400">— {s.detail}</span>}
                  </div>
                ))}
              </div>
            )}
            {run.details?.console_errors && run.details.console_errors.length > 0 && (
              <div className="mt-1.5">
                <button onClick={() => setShowConsole(!showConsole)} className="flex items-center gap-1 text-xs text-orange-600 hover:text-orange-800">
                  <Bug className="h-3 w-3" /> {run.details.console_errors.length} console error{run.details.console_errors.length > 1 ? "s" : ""} {showConsole ? "▾" : "▸"}
                </button>
                {showConsole && (
                  <div className="mt-1 space-y-1 max-h-48 overflow-y-auto">
                    {run.details.console_errors.map((e, i) => (
                      <div key={i} className="p-1.5 bg-orange-50 border border-orange-100 rounded text-xs font-mono break-all text-orange-700">{e.text}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <Screenshot base64={run.details?.screenshot_base64} url={run.details?.screenshot_url} label={stepName} />
            <VideoPlayer url={run.details?.session_recording_url} label={stepName} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function SectionCard({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <Card className="mb-4 sm:mb-6">
      <CardHeader className="pb-2 sm:pb-3 px-3 sm:px-4 pt-3 sm:pt-4">
        <CardTitle className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
          {icon} {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 sm:px-4 pb-3 sm:pb-4 pt-0">
        {children}
      </CardContent>
    </Card>
  );
}

function GroupMetrics({ runs, title, icon }: { runs: Run[]; title: string; icon: React.ReactNode }) {
  const latestRun = runs[0] ? runKey(runs[0]) : undefined;
  const latest = runs.filter(r => runKey(r) === latestRun);
  const pageStatus = latest.some(r => r.status === "fail") ? "fail" : latest.some(r => r.status === "degraded") ? "degraded" : "pass";

  const seen = new Set<string>();
  const unique: Run[] = [];
  for (const r of latest) {
    if (!seen.has(r.metric)) { seen.add(r.metric); unique.push(r); }
  }

  return (
    <SectionCard title={title} icon={icon}>
      <div className="flex items-center gap-2 mb-2">
        <Badge status={pageStatus} />
        <span className="text-xs text-slate-400">{unique.length} metrics</span>
      </div>
      {unique.map(r => (
        <MetricRow key={r.metric} metric={r.metric} value={r.value} status={r.status} tip={METRIC_INFO[r.metric]} />
      ))}
    </SectionCard>
  );
}

export default function MonitoringDashboard() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [platform, setPlatform] = useState<"android" | "ios" | "web">("android");

  async function fetchRuns() {
    const url = import.meta.env.VITE_SUPABASE_URL || _SUPABASE_URL;
    const key = import.meta.env.VITE_SUPABASE_KEY || _SUPABASE_KEY;
    if (!url || !key) return;
    try {
      const u = new URL(`${url}/rest/v1/monitoring_runs`);
      u.searchParams.set("select", "*");
      u.searchParams.set("order", "run_at.desc");
      u.searchParams.set("limit", "600");
      console.log("Fetching from:", u.toString().slice(0, 60) + "...");
      const r = await fetch(u.toString(), { headers: { apikey: key, Authorization: `Bearer ${key}` } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      console.log(`Fetched ${data.length} rows`);
      setRuns(data);
    } catch (e: any) { console.error("Supabase fetch error:", e); }
    finally { setLoading(false); }
  }

  useEffect(() => { fetchRuns(); const i = setInterval(fetchRuns, REFRESH_INTERVAL); return () => clearInterval(i); }, []);

  const grouped = useMemo(() => {
    const happy = runs.filter(r => r.page === "happy_flow" && r.metric.startsWith("step_"));
    const lh = runs.filter(r => r.page.startsWith("lighthouse/") || ["home", "category", "product_detail"].includes(r.page));
    const server = runs.filter(r => r.page.startsWith("server/"));
    const api = runs.filter(r => r.page.startsWith("api/"));
    const feature = runs.filter(r => r.page.startsWith("feature/"));
    return { happy, lh, server, api, feature };
  }, [runs]);

  function getLatestRunTime(): string | null {
    if (runs.length === 0) return null;
    return runKey(runs[0]);
  }
  const latestRun = getLatestRunTime();
  const latestAll = runs.filter(r => runKey(r) === latestRun);
  const pCount = latestAll.filter(r => r.status === "pass").length;
  const fCount = latestAll.filter(r => r.status === "fail").length;
  const dCount = latestAll.filter(r => r.status === "degraded").length;
  const totalRuns = new Set(runs.map(r => runKey(r))).size;

  const lastTime = runs[0] ? new Date(runs[0].run_at) : null;

  function groupLatestHappy() {
    if (!latestRun) return [];
    const stepRuns = runs.filter(r => r.page === "happy_flow" && r.metric.startsWith("step_") && runKey(r) === latestRun);
    const map = new Map<string, Run>();
    for (const r of stepRuns) map.set(r.metric, r);
    return map;
  }
  const latestHappy = groupLatestHappy();

  const STEP_ICONS: Record<string, React.ReactNode> = {
    step_home_load: <Globe className="h-4 w-4" />,
    step_home_products_populate: <Clock className="h-4 w-4" />,
    step_category_load: <Search className="h-4 w-4" />,
    step_product_detail_load: <Image className="h-4 w-4" />,
    step_bargain_flow: <DollarSign className="h-4 w-4" />,
    step_checkout_flow: <ShoppingCart className="h-4 w-4" />,
    step_search_products: <Search className="h-4 w-4" />,
    step_banners_check: <Image className="h-4 w-4" />,
    step_bargain2_flow: <DollarSign className="h-4 w-4" />,
  };
  const STEP_LABELS: Record<string, string> = {
    step_home_load: "Home Page Load",
    step_home_products_populate: "Products Populate Time",
    step_category_load: "Category Page Load",
    step_product_detail_load: "Product Detail Load",
    step_bargain_flow: "Bargain Flow",
    step_checkout_flow: "Checkout + Razorpay",
    step_search_products: "Search Products",
    step_banners_check: "Banners Check",
    step_bargain2_flow: "2nd Bargain",
  };

  // Base step keys matched to labels/names
  const HAPPY_STEPS = [
    { key: "home_load", label: "Home Page" },
    { key: "home_products_populate", label: "Product Populate" },
    { key: "category_all_load", label: "Category All" },
    { key: "category_home-kitchen_load", label: "Home & Kitchen" },
    { key: "category_toys-games_load", label: "Toys & Games" },
    { key: "category_fashion-accessories_load", label: "Fashion Accessories" },
    { key: "category_electronics_load", label: "Electronics" },
    { key: "product_detail_load", label: "Product Detail" },
    { key: "bargain_flow", label: "Bargain" },
    { key: "checkout_flow", label: "Checkout" },
    { key: "search_products", label: "Search" },
    { key: "banners_check", label: "Banners" },
    { key: "bargain2_flow", label: "Bargain 2" },
  ];

  const PLATFORMS = ["android", "ios", "web"];

  function pagePage(page: string) {
    if (page === "home" || page === "lighthouse/home") return "home";
    if (page === "category" || page === "lighthouse/category") return "category";
    if (page === "product_detail" || page === "lighthouse/product_detail") return "product_detail";
    return page;
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-6xl mx-auto px-3 sm:px-6 py-4 sm:py-8">
        <div className="flex items-center justify-between mb-4 sm:mb-6">
          <div>
            <Link href="/" className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600 mb-0.5">
              <ArrowLeft className="h-3 w-3" /> Home
            </Link>
            <h1 className="text-xl sm:text-2xl font-bold text-slate-900 flex items-center gap-2">
              <Activity className="h-5 w-5 sm:h-6 sm:w-6 text-indigo-600" />
              Synthetic Monitor
              <span className="text-xs font-normal text-slate-400 ml-1">gajab.com</span>
            </h1>
            {lastTime && <p className="text-xs text-slate-400 mt-0.5">Latest run: {lastTime.toLocaleDateString()} {lastTime.toLocaleTimeString()} · Auto-refreshes every 30s</p>}
          </div>
          <div className="flex items-center gap-2">
            <button onClick={fetchRuns} className="text-xs px-2.5 py-1.5 bg-white border border-slate-200 rounded text-slate-500 hover:bg-slate-50 whitespace-nowrap">
              Refresh
            </button>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center py-24"><Loader2 className="h-8 w-8 animate-spin text-slate-300" /></div>
        ) : runs.length === 0 ? (
          <Card className="border-dashed border-2"><CardContent className="py-12 text-center text-slate-400">
            <Activity className="h-10 w-10 mx-auto mb-2 opacity-50" />
            <p className="font-medium">No monitoring data yet</p>
            <p className="text-xs mt-1">First results appear after the hourly GitHub Action runs.</p>
          </CardContent></Card>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3 mb-4 sm:mb-6">
              <Card><CardContent className="py-3 text-center"><div className="text-xl sm:text-2xl font-bold text-slate-800">{totalRuns}</div><div className="text-xs text-slate-400 uppercase tracking-wider mt-0.5">Total Runs</div></CardContent></Card>
              <Card><CardContent className="py-3 text-center"><div className={`text-xl sm:text-2xl font-bold ${pCount > 0 ? "text-green-600" : "text-slate-300"}`}>{pCount}</div><div className="text-xs text-slate-400 uppercase tracking-wider mt-0.5">Passing</div></CardContent></Card>
              <Card><CardContent className="py-3 text-center"><div className={`text-xl sm:text-2xl font-bold ${fCount > 0 ? "text-red-600" : "text-slate-300"}`}>{fCount}</div><div className="text-xs text-slate-400 uppercase tracking-wider mt-0.5">Failing</div></CardContent></Card>
              <Card><CardContent className="py-3 text-center"><div className={`text-xl sm:text-2xl font-bold ${dCount > 0 ? "text-yellow-600" : "text-slate-300"}`}>{dCount}</div><div className="text-xs text-slate-400 uppercase tracking-wider mt-0.5">Degraded</div></CardContent></Card>
            </div>

            {fCount > 0 && (
              <div className="flex items-center gap-2 p-2.5 mb-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                {fCount} check{fCount > 1 ? "s" : ""} failing in the latest run
              </div>
            )}

            <details className="mb-4 bg-white border border-slate-200 rounded-lg text-xs">
              <summary className="cursor-pointer px-3 py-2 font-medium text-slate-600 flex items-center gap-1.5">
                <Info className="h-3.5 w-3.5 text-indigo-500" /> How checks are scored
              </summary>
              <div className="px-3 pb-3 space-y-1.5 text-slate-500 leading-relaxed">
                <div className="flex items-center gap-2"><Badge status="pass" /> <span>Met its target (element visible, metric within threshold).</span></div>
                <div className="flex items-center gap-2"><Badge status="fail" /> <span>Exceeded threshold or element missing — likely a real site problem.</span></div>
                <div className="flex items-center gap-2"><Badge status="degraded" /> <span>Completed but slower than its time budget — a warning, not a hard failure.</span></div>
                <p className="pt-1.5 border-t border-slate-100 text-slate-400">Thresholds: LCP &lt; 2500ms · TBT &lt; 300ms · SI &lt; 4000ms · CLS &lt; 0.1 · Performance ≥ 50. Every fail/degraded check sends a Slack alert.</p>
              </div>
            </details>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
              <div>
                <SectionCard title="Happy Flow" icon={<Activity className="h-3.5 w-3.5" />}>
                  {/* Platform tabs */}
                  <div className="flex gap-1 mb-3 p-0.5 bg-slate-100 rounded-lg">
                    <button
                      onClick={() => setPlatform("android")}
                      className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-medium rounded-md transition-colors ${
                        platform === "android" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
                      }`}
                    >
                      <Smartphone className="h-3.5 w-3.5" /> Android
                    </button>
                    <button
                      onClick={() => setPlatform("ios")}
                      className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-medium rounded-md transition-colors ${
                        platform === "ios" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
                      }`}
                    >
                      <Apple className="h-3.5 w-3.5" /> iOS
                    </button>
                    <button
                      onClick={() => setPlatform("web")}
                      className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-medium rounded-md transition-colors ${
                        platform === "web" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
                      }`}
                    >
                      <Monitor className="h-3.5 w-3.5" /> Desktop
                    </button>
                  </div>
                  {/* Steps for selected platform */}
                  {HAPPY_STEPS.map(({ key: stepKey, label }) => {
                    const metric = `step_${platform}_${stepKey}`;
                    const run = latestHappy.get(metric);
                    if (!run) return <div key={metric} className="text-xs text-slate-400 py-1.5 pl-1">No data for {label.toLowerCase()}</div>;
                    return <StepCard key={metric} run={run} stepName={label} icon={<Activity className="h-4 w-4" />} />;
                  })}
                </SectionCard>
              </div>

              <div>
                <SectionCard title="Lighthouse Performance" icon={<BarChart3 className="h-3.5 w-3.5" />}>
                  {["home", "category", "product_detail"].map(p => {
                    const pageRuns = runs.filter(r => (r.page === p || r.page === `lighthouse/${p}`) && runKey(r) === latestRun);
                    const seen = new Set<string>();
                    const unique: Run[] = [];
                    for (const r of pageRuns) { if (!seen.has(r.metric)) { seen.add(r.metric); unique.push(r); } }
                    const st = unique.some(r => r.status === "fail") ? "fail" : unique.some(r => r.status === "degraded") ? "degraded" : "pass";
                    return (
                      <div key={p} className="mb-3 last:mb-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-sm font-medium text-slate-700 capitalize">{p.replace(/_/g, " ")}</span>
                          <Badge status={st} />
                        </div>
                        {unique.map(r => <MetricRow key={r.metric} metric={r.metric} value={r.value} status={r.status} tip={METRIC_INFO[r.metric]} />)}
                      </div>
                    );
                  })}
                </SectionCard>

                {grouped.server.length > 0 && (() => {
                  const latestRun = getLatestRunTime();
                  const serverLatest = grouped.server.filter(r => runKey(r) === latestRun);
                  const byEndpoint = new Map<string, typeof serverLatest>();
                  for (const r of serverLatest) {
                    const name = r.page.replace("server/", "");
                    if (!byEndpoint.has(name)) byEndpoint.set(name, []);
                    byEndpoint.get(name)!.push(r);
                  }
                  return (
                    <SectionCard title="Server Health" icon={<Server className="h-3.5 w-3.5" />}>
                      {[...byEndpoint.entries()].map(([name, endpointRuns]) => (
                        <div key={name} className="mb-2 last:mb-0">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs font-medium text-slate-700">{name}</span>
                            <Badge status={endpointRuns.some(r => r.status === "fail") ? "fail" : endpointRuns.some(r => r.status === "degraded") ? "degraded" : "pass"} />
                          </div>
                          {endpointRuns.map(r => (
                            <MetricRow key={r.metric} metric={r.metric} value={r.value} status={r.status} />
                          ))}
                        </div>
                      ))}
                    </SectionCard>
                  );
                })()}
                {grouped.api.length > 0 && (() => {
                  const latestRun = getLatestRunTime();
                  const apiLatest = grouped.api.filter(r => runKey(r) === latestRun);
                  const byEndpoint = new Map<string, typeof apiLatest>();
                  for (const r of apiLatest) {
                    const name = r.page.replace("api/", "");
                    if (!byEndpoint.has(name)) byEndpoint.set(name, []);
                    byEndpoint.get(name)!.push(r);
                  }
                  return (
                    <SectionCard title="API Endpoints" icon={<Cpu className="h-3.5 w-3.5" />}>
                      {[...byEndpoint.entries()].map(([name, endpointRuns]) => (
                        <div key={name} className="mb-2 last:mb-0">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs font-medium text-slate-700">{name}</span>
                            <Badge status={endpointRuns.some(r => r.status === "fail") ? "fail" : endpointRuns.some(r => r.status === "degraded") ? "degraded" : "pass"} />
                          </div>
                          {endpointRuns.map(r => (
                            <MetricRow key={r.metric} metric={r.metric} value={r.value} status={r.status} />
                          ))}
                        </div>
                      ))}
                    </SectionCard>
                  );
                })()}
                {grouped.feature.length > 0 && (() => {
                  const featureLatest = grouped.feature.filter(r => runKey(r) === latestRun);
                  const pages = ["home", "category", "product_detail"];
                  return (
                    <SectionCard title="Feature Element Checks" icon={<Bug className="h-3.5 w-3.5" />}>
                      {pages.map(p => {
                        const checks = featureLatest.filter(r => r.page === `feature/${p}`);
                        if (checks.length === 0) return null;
                        const st = checks.some(r => r.status === "fail") ? "fail" : checks.some(r => r.status === "degraded") ? "degraded" : "pass";
                        return (
                          <div key={p} className="mb-2 last:mb-0">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-xs font-medium text-slate-700 capitalize">{p.replace(/_/g, " ")}</span>
                              <Badge status={st} />
                            </div>
                            {checks.map(r => (
                              <div key={r.id} className="flex items-center justify-between py-1 border-b border-slate-100 last:border-b-0 text-sm">
                                <span className="text-slate-600 flex items-center gap-0.5">
                                  {r.metric.replace("elem_", "").replace("_visible", "").replace(/_/g, " ")}
                                  {r.step_failed && <InfoTip text={r.step_failed} />}
                                </span>
                                <Badge status={r.status} />
                              </div>
                            ))}
                          </div>
                        );
                      })}
                    </SectionCard>
                  );
                })()}
              </div>
            </div>

            <Card className="mt-4 sm:mt-6">
              <CardHeader className="pb-2 px-3 sm:px-4 pt-3 sm:pt-4">
                <CardTitle className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5" /> Recent Runs
                </CardTitle>
              </CardHeader>
              <CardContent className="px-3 sm:px-4 pb-3 sm:pb-4 pt-0">
                <div className="overflow-x-auto -mx-3 sm:mx-0">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-slate-200 text-slate-400 uppercase tracking-wider">
                        <th className="text-left px-3 py-2 font-medium">Time</th>
                        <th className="text-left px-3 py-2 font-medium">Page</th>
                        <th className="text-left px-3 py-2 font-medium">Metric</th>
                        <th className="text-right px-3 py-2 font-medium">Value</th>
                        <th className="text-center px-3 py-2 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.slice(0, 50).map(r => (
                        <tr key={r.id} className="border-b border-slate-100 hover:bg-slate-50">
                          <td className="px-3 py-2 text-slate-500 whitespace-nowrap">{new Date(r.run_at).toLocaleTimeString()}</td>
                          <td className="px-3 py-2 text-slate-700 font-medium">{r.page.replace("lighthouse/", "").replace("server/", "").replace("api/", "").replace("feature/", "")}</td>
                          <td className="px-3 py-2 text-slate-500 max-w-[120px] truncate">{r.metric}</td>
                          <td className="px-3 py-2 text-right font-mono"><Val metric={r.metric} value={r.value} /></td>
                          <td className="px-3 py-2 text-center"><Badge status={r.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
