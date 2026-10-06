import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine, CartesianGrid, Legend,
} from "recharts";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Upload, Link2, Loader2, Brain, PlayCircle, Sparkles, Clock,
  AlertTriangle, ChevronDown, ChevronUp, Wand2, Target, History, TrendingUp,
} from "lucide-react";
import { API_BASE } from "@/lib/api";

const API = ((import.meta.env as Record<string, string | undefined>).VITE_TREND_API_URL) || API_BASE;

type JobStatus = "queued" | "downloading" | "processing" | "completed" | "failed";

interface Segment { name: string; start: number; end: number; score: number | null; available: boolean; }
interface CritiqueSegment {
  segment: string; score: number | null; verdict: string;
  issues?: string[]; fixes?: string[]; rewriteIdea?: string;
  strengths?: string[]; weaknesses?: string[];
}
interface BreakdownItem {
  t: number; what: string; why: string; fix: string;
  lowDimensions?: string[]; severity?: string;
}
interface Critique {
  summary: string; segments: CritiqueSegment[]; breakdown?: BreakdownItem[];
  source?: string; model?: string;
}
interface BrainFunction {
  name: string; network: string; means: string; hookRole: string; switchOff: string;
  score: number | null; status: string; bySegment: Record<string, number | null>; reason: string;
}
interface BenchStat { count: number; mean: number; p25: number; p50: number; p75: number; min: number; max: number; }
interface BenchGroup { count: number; overall: BenchStat | null; segments: Record<string, BenchStat | null>; scores: number[]; }
interface BenchTop {
  jobId: string; title?: string; url?: string; likeCount?: number | null;
  commentCount?: number | null; successScore?: number | null;
  overallScore: number; verdict?: string;
}
interface Benchmark extends BenchGroup {
  benchmarkCount: number; localCount: number;
  benchmark: BenchGroup; local: BenchGroup; top: BenchTop[];
}
interface BenchStatusItem {
  jobId: string; status: string; title?: string; url?: string;
  likeCount?: number | null; overallScore?: number | null;
  progress: { stage: string; pct: number; message: string };
}
interface BenchStatus {
  corpusCount: number; queueLength: number; running: boolean; items: BenchStatusItem[];
}
interface AnalysisResult {
  durationSec: number; frames: string[]; device: string; nTimesteps: number;
  timestamps: number[]; dimensions: Record<string, number[]>;
  worksScore: number[]; overall: number; overallScore: number; verdict: string;
  segments: Segment[];
  dropoffs: { t: number; drop: number; severity: string }[];
  critique: Critique;
  topMoments: { t: number; score: number; dominant: string }[];
  weakMoments: { t: number; score: number; dominant: string }[];
  flags: { kind: string; at: number; label: string }[];
  brainFunctions?: BrainFunction[];
  clipText?: string;
}
interface JobSummary {
  jobId: string; kind?: string; status: JobStatus; title?: string; sourceType: "url" | "upload";
  sourceUrl?: string; createdAt: string;
  progress: { stage: string; pct: number; message: string };
  overall?: number; verdict?: string; durationSec?: number; overallScore?: number;
}
interface JobDetail extends JobSummary { result?: AnalysisResult; error?: string; }

const DIM_COLORS: Record<string, string> = {
  "Visual Attention": "#ef4444",
  "Emotional Engagement": "#ec4899",
  "Reward & Motivation": "#f59e0b",
  "Memory Encoding": "#8b5cf6",
  "Language & Message": "#3b82f6",
  "Social Connection": "#14b8a6",
  "Auditory Impact": "#22c55e",
};

const STATUS_STYLES: Record<JobStatus, string> = {
  queued: "bg-slate-100 text-slate-700",
  downloading: "bg-blue-100 text-blue-700",
  processing: "bg-amber-100 text-amber-700",
  completed: "bg-emerald-100 text-emerald-700",
  failed: "bg-rose-100 text-rose-700",
};

function scoreColor(n: number) {
  if (n >= 70) return "#10b981";
  if (n >= 50) return "#f59e0b";
  return "#ef4444";
}
function verdictLabel(n: number) {
  return n >= 70 ? "strong" : n >= 50 ? "ok" : "weak";
}

export default function TrendFinder() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/trend/jobs`);
      const d = await r.json();
      setJobs(d.jobs || []);
    } catch { /* ignore */ }
  }, []);

  const refreshDetail = useCallback(async (id: string) => {
    try {
      const r = await fetch(`${API}/api/trend/analyze/${id}`);
      if (r.ok) setDetail(await r.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    refreshJobs();
    const t = setInterval(refreshJobs, 4000);
    return () => clearInterval(t);
  }, [refreshJobs]);

  useEffect(() => {
    if (!selected) return;
    refreshDetail(selected);
    const t = setInterval(() => refreshDetail(selected), 3000);
    return () => clearInterval(t);
  }, [selected, refreshDetail]);

  const submit = useCallback(async (opts: { file?: File; url?: string; title?: string }) => {
    setBusy(true);
    setError(null);
    try {
      let res: Response;
      if (opts.file) {
        const fd = new FormData();
        fd.append("file", opts.file);
        if (opts.title) fd.append("title", opts.title);
        res = await fetch(`${API}/api/trend/analyze`, { method: "POST", body: fd });
      } else {
        res = await fetch(`${API}/api/trend/analyze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: opts.url, title: opts.title }),
        });
      }
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Failed to start analysis");
      setUrl("");
      if (fileRef.current) fileRef.current.value = "";
      setSelected(d.jobId);
      refreshJobs();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refreshJobs]);

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b bg-white">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-2">
          <Brain className="h-6 w-6 text-violet-600" />
          <span className="font-semibold text-slate-900">Hook Analyzer</span>
          <Badge variant="secondary" className="ml-2 text-[10px] bg-violet-100 text-violet-700">
            Meta TRIBE v2
          </Badge>
          <div className="flex-1" />
          <span className="text-xs text-slate-500 flex items-center gap-1">
            <History className="h-3.5 w-3.5" /> {jobs.length} analyzed
          </span>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        <div className="text-center mb-8">
          <h1 className="text-3xl md:text-4xl font-bold text-slate-900 tracking-tight">
            Know which hook is worth testing
          </h1>
          <p className="text-slate-600 mt-2 max-w-2xl mx-auto">
            Drop in a short video ad. Get Hook / Bridge / Offer scores, a strategist
            critique with fixes, drop-off annotations — plus a TRIBE v2 brain-response panel.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[340px_1fr] gap-6">
          {/* Left: upload + history */}
          <div className="space-y-4">
            <Card className="border-violet-200 shadow-sm">
              <CardContent className="pt-6">
                <Tabs defaultValue="upload">
                  <TabsList className="grid grid-cols-2 w-full">
                    <TabsTrigger value="upload"><Upload className="h-4 w-4 mr-1" />Upload</TabsTrigger>
                    <TabsTrigger value="url"><Link2 className="h-4 w-4 mr-1" />URL</TabsTrigger>
                  </TabsList>
                  <TabsContent value="upload" className="space-y-2 mt-3">
                    <div className="rounded-lg border-2 border-dashed border-violet-200 bg-violet-50/40 p-4 text-center">
                      <Upload className="h-6 w-6 mx-auto text-violet-500 mb-1" />
                      <Input ref={fileRef} type="file" accept="video/*" className="mt-1" />
                      <p className="text-[11px] text-slate-500 mt-2">MP4 or MOV · clips capped at 8s</p>
                    </div>
                    <Button
                      className="w-full bg-violet-600 hover:bg-violet-700"
                      disabled={busy}
                      onClick={() => {
                        const f = fileRef.current?.files?.[0];
                        if (f) submit({ file: f, title: f.name });
                        else setError("Choose a video file first");
                      }}
                    >
                      {busy ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Sparkles className="h-4 w-4 mr-2" />}
                      Analyze hook
                    </Button>
                  </TabsContent>
                  <TabsContent value="url" className="space-y-2 mt-3">
                    <Input placeholder="https://www.instagram.com/reel/..." value={url} onChange={(e) => setUrl(e.target.value)} />
                    <Button
                      className="w-full bg-violet-600 hover:bg-violet-700"
                      disabled={busy || !url.trim()}
                      onClick={() => submit({ url: url.trim() })}
                    >
                      {busy ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Sparkles className="h-4 w-4 mr-2" />}
                      Analyze hook
                    </Button>
                  </TabsContent>
                </Tabs>
                <p className="text-[11px] text-slate-500 mt-3 flex items-start gap-1">
                  <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  Local CPU: ~2–4 min per video-second. Long jobs (up to ~30 min) are normal.
                </p>
                {error && <p className="text-sm text-rose-600 mt-2">{error}</p>}
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-5 space-y-2 max-h-[420px] overflow-auto">
                <div className="text-sm font-medium text-slate-700 mb-1">History</div>
                {jobs.filter((j) => j.kind !== "benchmark").length === 0 && (
                  <p className="text-sm text-slate-500">No analyses yet.</p>
                )}
                {jobs.filter((j) => j.kind !== "benchmark").map((j) => (
                  <button
                    key={j.jobId}
                    onClick={() => setSelected(j.jobId)}
                    className={`w-full text-left rounded-lg border p-3 transition-colors ${
                      selected === j.jobId ? "border-violet-400 bg-violet-50" : "border-slate-200 hover:bg-slate-50"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-sm truncate">
                        {j.title || j.sourceUrl || j.jobId.slice(0, 8)}
                      </span>
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${STATUS_STYLES[j.status]}`}>
                        {j.status}
                      </span>
                    </div>
                    {j.status === "completed" && (
                      <div className="flex items-center gap-2 mt-2">
                        <span className="text-xs font-semibold" style={{ color: scoreColor(j.overallScore ?? 0) }}>
                          {j.overallScore ?? "—"}/100
                        </span>
                        <span className="text-[11px] text-slate-500">
                          {j.durationSec}s · {verdictLabel(j.overallScore ?? 0)}
                        </span>
                      </div>
                    )}
                    {j.status !== "completed" && j.status !== "failed" && (
                      <Progress value={j.progress.pct} className="mt-2 h-1.5" />
                    )}
                  </button>
                ))}
              </CardContent>
            </Card>

            <BenchmarkPanel onChanged={refreshJobs} />
          </div>

          {/* Right: results */}
          <div>
            {!detail && <EmptyState />}
            {detail && detail.status !== "completed" && <Processing job={detail} />}
            {detail?.status === "completed" && detail.result && (
              <Results job={detail} result={detail.result} />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function BenchmarkPanel({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<BenchStatus | null>(null);
  const [top, setTop] = useState<BenchTop[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, b] = await Promise.all([
        fetch(`${API}/api/trend/benchmark/status`).then((r) => r.json()),
        fetch(`${API}/api/trend/benchmark`).then((r) => r.json()),
      ]);
      setStatus(s);
      setTop(b.top || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const post = async (path: string, body?: unknown) => {
    setBusy(true);
    try {
      await fetch(`${API}${path}`, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      onChanged();
      refresh();
    } finally {
      setBusy(false);
    }
  };

  const addUrls = async () => {
    const urls = text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
    if (!urls.length) return;
    await post("/api/trend/benchmark/ingest", { urls });
    setText("");
  };

  const pending = status?.items?.filter((i) => i.status !== "completed") ?? [];

  return (
    <Card>
      <CardContent className="pt-5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium text-slate-700 flex items-center gap-1.5">
            <Target className="h-4 w-4 text-violet-600" /> Benchmark corpus
          </div>
          <Badge variant="secondary" className="text-[10px]">{status?.corpusCount ?? 0} reels</Badge>
        </div>
        <p className="text-[11px] text-slate-500">
          Successful public reels (ranked by likes) analysed in the background, so your
          cuts are ranked against real performers. Discovery is manual — paste reel URLs.
        </p>
        {(status?.running || (status?.queueLength ?? 0) > 0) && (
          <div className="text-[11px] text-amber-600 flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" /> Processing… {status?.queueLength} queued
          </div>
        )}
        <Button size="sm" variant="outline" disabled={busy} onClick={() => post("/api/trend/benchmark/seed")}>
          Seed sample reels
        </Button>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste Instagram reel URLs (one per line)…"
          className="w-full text-xs rounded-md border p-2 h-16 resize-none"
        />
        <Button
          size="sm"
          className="w-full bg-violet-600 hover:bg-violet-700"
          disabled={busy || !text.trim()}
          onClick={addUrls}
        >
          Add to benchmark
        </Button>
        {top.length > 0 && (
          <div className="pt-1">
            <div className="text-xs font-medium text-slate-600 mb-1">Top performers</div>
            <div className="space-y-1">
              {top.slice(0, 5).map((t) => (
                <a
                  key={t.jobId} href={t.url} target="_blank" rel="noreferrer"
                  className="flex items-center justify-between text-[11px] hover:bg-slate-50 rounded px-1 py-0.5"
                >
                  <span className="truncate max-w-[140px]">{t.title}</span>
                  <span className="text-slate-500">♥ {t.likeCount ?? "—"} · {t.overallScore}/100</span>
                </a>
              ))}
            </div>
          </div>
        )}
        {pending.length > 0 && (
          <div className="space-y-1 max-h-32 overflow-auto">
            {pending.map((i) => (
              <div key={i.jobId} className="text-[11px] text-slate-500 flex items-center gap-2">
                <span className={`px-1.5 py-0.5 rounded-full ${STATUS_STYLES[i.status as JobStatus] || "bg-slate-100"}`}>
                  {i.status}
                </span>
                <span className="truncate">{i.title}</span>
                <span className="ml-auto">{i.progress.pct}%</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function EmptyState() {
  const rows = [
    { name: "Hook", win: "0–3s", score: 82 },
    { name: "Bridge", win: "3–7s", score: 71 },
    { name: "Offer", win: "7–15s", score: 65 },
  ];
  return (
    <Card className="border-dashed">
      <CardContent className="pt-8 pb-8 text-center">
        <Target className="h-10 w-10 mx-auto text-violet-400 mb-3" />
        <div className="text-slate-700 font-medium">Sample result</div>
        <div className="text-5xl font-bold text-slate-900 mt-2">84<span className="text-xl text-slate-400">/100</span></div>
        <div className="space-y-3 max-w-md mx-auto mt-6">
          {rows.map((r) => (
            <div key={r.name} className="flex items-center gap-3">
              <div className="w-20 text-left text-sm text-slate-600">{r.name} <span className="text-slate-400 text-xs">{r.win}</span></div>
              <div className="flex-1 h-2.5 rounded-full bg-slate-100 overflow-hidden">
                <div className="h-full rounded-full" style={{ width: `${r.score}%`, background: scoreColor(r.score) }} />
              </div>
              <div className="w-8 text-right text-sm font-medium">{r.score}</div>
            </div>
          ))}
        </div>
        <p className="text-sm text-slate-500 mt-6">Upload a reel to generate your own scores, critique and brain panel.</p>
      </CardContent>
    </Card>
  );
}

function Processing({ job }: { job: JobDetail }) {
  const segs = ["Hook", "Bridge", "Offer"];
  return (
    <Card>
      <CardContent className="pt-6 space-y-4">
        <div className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-violet-600" />
          <span className="font-medium">
            {job.status === "failed" ? "Analysis failed" : "Running TRIBE v2…"}
          </span>
        </div>
        <Progress value={job.progress.pct} />
        <p className="text-sm text-slate-600 capitalize">{job.progress.message || job.progress.stage}</p>
        <div className="grid grid-cols-3 gap-3 pt-2">
          {segs.map((s) => (
            <div key={s} className="rounded-lg border p-3 text-center">
              <div className="text-xs text-slate-500">{s}</div>
              <div className="text-2xl font-bold text-slate-300 mt-1">—</div>
            </div>
          ))}
        </div>
        <p className="text-xs text-slate-400 flex items-center gap-1">
          <Clock className="h-3.5 w-3.5" /> Long jobs are normal on CPU. This page updates automatically.
        </p>
        {job.error && <p className="text-sm text-rose-600">{job.error}</p>}
      </CardContent>
    </Card>
  );
}

function Results({ job, result }: { job: JobDetail; result: AnalysisResult }) {
  const [showBrain, setShowBrain] = useState(false);
  const [showDims, setShowDims] = useState(false);
  const [frameIdx, setFrameIdx] = useState(0);
  const [bench, setBench] = useState<Benchmark | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const seek = (t: number) => { if (videoRef.current) videoRef.current.currentTime = t; };

  useEffect(() => {
    fetch(`${API}/api/trend/benchmark`).then((r) => r.json()).then(setBench).catch(() => {});
  }, [job.jobId]);

  const chartData = useMemo(
    () => result.timestamps.map((t, i) => {
      const row: Record<string, number> = { t, works: result.worksScore[i] ?? 0 };
      for (const [k, v] of Object.entries(result.dimensions)) row[k] = v[i] ?? 0;
      return row;
    }),
    [result],
  );

  const overall = result.overallScore ?? 0;
  const useBench = !!bench && bench.benchmarkCount > 0;
  const benchGroup: BenchGroup | null = bench ? (useBench ? bench.benchmark : bench) : null;
  const benchCount = benchGroup?.count ?? 0;
  const benchLabel = useBench
    ? `${benchCount} benchmark reel${benchCount === 1 ? "" : "s"}`
    : `${benchCount} reel${benchCount === 1 ? "" : "s"} you've analyzed locally`;
  const weakest = [...(result.segments || [])]
    .filter((s) => s.score != null)
    .sort((a, b) => (a.score! - b.score!))[0];

  return (
    <div className="space-y-4">
      {/* Score summary */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap items-center gap-6">
            <div className="relative h-24 w-24">
              <svg viewBox="0 0 36 36" className="h-24 w-24 -rotate-90">
                <circle cx="18" cy="18" r="15.9" fill="none" stroke="#eef2f7" strokeWidth="3" />
                <circle
                  cx="18" cy="18" r="15.9" fill="none" stroke={scoreColor(overall)} strokeWidth="3"
                  strokeDasharray={`${overall} 100`} strokeLinecap="round"
                />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-2xl font-bold">{overall}</span>
                <span className="text-[10px] text-slate-400">/100</span>
              </div>
            </div>
            <div className="flex-1 min-w-[240px]">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-900">Overall hook score</span>
                <Badge className="text-white" style={{ background: scoreColor(overall) }}>
                  {verdictLabel(overall)}
                </Badge>
              </div>
              <p className="text-sm text-slate-600 mt-1">
                {result.critique?.summary ||
                  `Weakest window: ${weakest?.name ?? "—"}.`}
              </p>
            </div>
          </div>

          <div className="space-y-3 mt-6">
            {result.segments?.map((s) => (
              <div key={s.name} className="flex items-center gap-3">
                <div className="w-24 text-left">
                  <div className="text-sm font-medium text-slate-700">{s.name}</div>
                  <div className="text-[11px] text-slate-400">{s.start}–{s.end}s</div>
                </div>
                <div className="flex-1 h-3 rounded-full bg-slate-100 overflow-hidden">
                  {s.score != null && (
                    <div className="h-full rounded-full" style={{ width: `${s.score}%`, background: scoreColor(s.score) }} />
                  )}
                </div>
                <div className="w-10 text-right text-sm font-semibold">
                  {s.score ?? "—"}
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* What went wrong — second by second */}
      {result.critique?.breakdown && result.critique.breakdown.length > 0 && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 mb-3">
              <Target className="h-4 w-4 text-rose-600" />
              <span className="font-semibold text-slate-900">What went wrong</span>
              <Badge variant="secondary" className="text-[10px]">second by second</Badge>
            </div>
            <div className="space-y-2">
              {result.critique.breakdown.map((b, i) => (
                <div key={i} className="flex gap-3 rounded-lg border p-3">
                  <button
                    onClick={() => seek(b.t)}
                    className="shrink-0 w-12 h-8 rounded bg-slate-900 text-white text-xs font-semibold hover:bg-violet-600"
                  >
                    {b.t}s
                  </button>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-800">{b.what}</div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      {b.why}
                      {b.lowDimensions && b.lowDimensions.length > 0 && (
                        <> · weak: {b.lowDimensions.join(", ")}</>
                      )}
                    </div>
                    <div className="text-xs text-emerald-700 mt-1 flex gap-1">
                      <Sparkles className="h-3.5 w-3.5 shrink-0 mt-0.5" /> {b.fix}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Critique */}
      {result.critique && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 mb-3">
              <Wand2 className="h-4 w-4 text-violet-600" />
              <span className="font-semibold text-slate-900">Strategist critique</span>
              {result.critique.source === "groq" && (
                <Badge variant="secondary" className="text-[10px]">AI · {result.critique.model}</Badge>
              )}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {result.critique.segments?.filter((c) => c.score != null).map((c) => (
                <div key={c.segment} className="rounded-lg border p-4 bg-white">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-slate-800">{c.segment}</span>
                    <span className="text-sm font-semibold" style={{ color: scoreColor(c.score ?? 0) }}>
                      {c.score}/100
                    </span>
                  </div>
                  {c.issues && c.issues.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {c.issues.map((it, i) => (
                        <li key={i} className="text-xs text-rose-600 flex gap-1">
                          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" /> {it}
                        </li>
                      ))}
                    </ul>
                  )}
                  {c.fixes && c.fixes.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {c.fixes.map((f, i) => (
                        <li key={i} className="text-xs text-emerald-700 flex gap-1">
                          <Sparkles className="h-3.5 w-3.5 shrink-0 mt-0.5" /> {f}
                        </li>
                      ))}
                    </ul>
                  )}
                  {c.rewriteIdea && (
                    <p className="text-xs text-slate-500 mt-2 italic">Rewrite: “{c.rewriteIdea}”</p>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Brain functions — what's triggered and what's switched off */}
      {result.brainFunctions && result.brainFunctions.length > 0 && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2">
              <Brain className="h-4 w-4 text-violet-600" />
              <span className="font-semibold text-slate-900">Brain functions — what the hook triggers</span>
            </div>
            <p className="text-xs text-slate-500 mt-1 mb-4">
              TRIBE v2 predicts activity across 7 brain networks. “Switched off” means the
              predicted response in that network is below average for this clip — with the
              concrete reason from the footage.
            </p>
            <div className="space-y-3">
              {result.brainFunctions.map((b) => {
                const sc = b.score ?? 0;
                const under = b.status === "under";
                return (
                  <div key={b.name} className="rounded-lg border p-3">
                    <div className="flex items-center gap-3">
                      <div className="w-40 shrink-0">
                        <div className="text-sm font-medium text-slate-800">{b.name}</div>
                        <div className="text-[10px] text-slate-400">{b.network}</div>
                      </div>
                      <div className="flex-1 h-2.5 rounded-full bg-slate-100 overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${sc}%`, background: scoreColor(sc) }} />
                      </div>
                      <div className="w-10 text-right text-sm font-semibold">{b.score ?? "—"}</div>
                      <Badge
                        className={`text-[10px] ${under ? "bg-rose-100 text-rose-700" : "bg-emerald-100 text-emerald-700"}`}
                        variant="secondary"
                      >
                        {under ? "switched off" : "engaged"}
                      </Badge>
                    </div>
                    <div className="text-xs text-slate-500 mt-1.5">{b.means}</div>
                    <div className="text-xs mt-1">
                      {under ? (
                        <span className="text-rose-600">
                          Why: {b.reason || `no strong cue — usually ${b.switchOff}`}.
                          {" "}Fix: {b.hookRole}
                        </span>
                      ) : (
                        <span className="text-emerald-700">{b.hookRole}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Comparison */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="h-4 w-4 text-violet-600" />
            <span className="font-semibold text-slate-900">How it compares</span>
          </div>
          {!benchGroup || benchCount < 2 ? (
            <p className="text-sm text-slate-500">
              Only {benchCount} reel{benchCount === 1 ? "" : "s"} available so far. Seed the benchmark
              (left panel) or analyze more reels to get a ranking.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-6">
                <div>
                  <div className="text-3xl font-bold" style={{ color: scoreColor(overall) }}>
                    {overall}
                    <span className="text-sm text-slate-400">/100</span>
                  </div>
                  <div className="text-xs text-slate-500">your overall</div>
                </div>
                <div>
                  <div className="text-3xl font-bold text-slate-700">{benchGroup.overall?.p50 ?? "—"}</div>
                  <div className="text-xs text-slate-500">median of {benchLabel}</div>
                </div>
                <div>
                  <div className="text-3xl font-bold text-violet-600">
                    {Math.round((benchGroup.scores.filter((s) => s <= overall).length / benchCount) * 100)}
                    <span className="text-sm text-slate-400">th</span>
                  </div>
                  <div className="text-xs text-slate-500">percentile</div>
                </div>
              </div>
              <div className="space-y-2 mt-5">
                {["Hook", "Bridge", "Offer"].map((name) => {
                  const mine = result.segments?.find((s) => s.name === name)?.score ?? null;
                  const med = benchGroup.segments?.[name]?.p50 ?? null;
                  return (
                    <div key={name} className="flex items-center gap-3 text-sm">
                      <div className="w-16 text-slate-600">{name}</div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-2.5 rounded-full bg-slate-100 overflow-hidden">
                            {mine != null && <div className="h-full rounded-full" style={{ width: `${mine}%`, background: scoreColor(mine) }} />}
                          </div>
                          <span className="w-8 text-right font-medium">{mine ?? "—"}</span>
                        </div>
                        <div className="text-[10px] text-slate-400">median {med ?? "—"}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
              <p className="text-[11px] text-slate-400 mt-4">
                Compared against {benchLabel} — a relative ranking, not an external
                labelled-retention benchmark.
              </p>
            </>
          )}
        </CardContent>
      </Card>

      {/* Drop-offs + clip */}
      <div className="grid grid-cols-1 xl:grid-cols-[320px_1fr] gap-4">
        <Card>
          <CardContent className="pt-5">
            <div className="text-sm font-medium text-slate-700 mb-2 flex items-center gap-2">
              <PlayCircle className="h-4 w-4 text-violet-600" /> Clip
            </div>
            <video ref={videoRef} src={`${API}/api/trend/media/${job.jobId}`} controls className="w-full rounded-md bg-black" />
            {result.dropoffs?.length > 0 && (
              <div className="mt-3">
                <div className="text-xs font-medium text-slate-600 mb-1">Drop-offs</div>
                {result.dropoffs.map((d, i) => (
                  <button
                    key={i} onClick={() => seek(d.t)}
                    className="w-full text-left text-xs flex items-center justify-between rounded px-2 py-1 hover:bg-rose-50"
                  >
                    <span className={d.severity === "high" ? "text-rose-600" : "text-amber-600"}>
                      ▼ {d.t}s · {d.severity}
                    </span>
                    <span className="text-slate-400">−{(d.drop * 100).toFixed(0)}%</span>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="text-sm font-medium text-slate-700 mb-2">Engagement timeline</div>
            <div className="h-[240px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} onClick={(e: { activeLabel?: string | number } | null) => e?.activeLabel != null && seek(Number(e.activeLabel))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                  <XAxis dataKey="t" unit="s" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <ReferenceLine y={result.overall} stroke="#a855f7" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="works" name="Engagement" stroke="#7c3aed" strokeWidth={3} dot />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Brain panel (secondary) */}
      <Card>
        <button
          className="w-full flex items-center justify-between px-6 py-4"
          onClick={() => setShowBrain(!showBrain)}
        >
          <span className="flex items-center gap-2 font-semibold text-slate-900">
            <Brain className="h-4 w-4 text-violet-600" /> Brain response panel
            <Badge variant="secondary" className="text-[10px]">TRIBE v2 · directional</Badge>
          </span>
          {showBrain ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>
        {showBrain && (
          <CardContent className="space-y-4">
            <p className="text-xs text-slate-500">
              Predicted fMRI response on the cortical surface (fsaverage5). <b>Warmer = stronger
              predicted activation</b>; the timeline shows how that rises and falls per second. This is
              a directional simulation of an average viewer, not a real scan — use it to compare
              moments, not as an absolute measurement.
            </p>
            <div className="flex justify-end">
              <Button variant="outline" size="sm" onClick={() => setShowDims(!showDims)}>
                {showDims ? "Hide" : "Show"} brain dimensions
              </Button>
            </div>
            <div className="h-[260px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                  <XAxis dataKey="t" unit="s" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="works" name="Engagement" stroke="#7c3aed" strokeWidth={2} dot={false} />
                  {showDims && Object.keys(result.dimensions).map((d) => (
                    <Line key={d} type="monotone" dataKey={d} name={d} stroke={DIM_COLORS[d] || "#94a3b8"} strokeWidth={1.5} dot={false} opacity={0.85} />
                  ))}
                  {showDims && <Legend wrapperStyle={{ fontSize: 11 }} />}
                </LineChart>
              </ResponsiveContainer>
            </div>
            {result.frames.length > 0 && (
              <div className="space-y-2">
                <img
                  src={`${API}/api/trend/frames/${job.jobId}/${result.frames[Math.min(frameIdx, result.frames.length - 1)]}`}
                  alt="brain activation"
                  className="w-full rounded-md border bg-white"
                />
                <input
                  type="range" min={0} max={result.frames.length - 1}
                  value={Math.min(frameIdx, result.frames.length - 1)}
                  onChange={(e) => setFrameIdx(Number(e.target.value))}
                  className="w-full accent-violet-600"
                />
                <div className="flex gap-2 overflow-auto">
                  {result.frames.map((f, i) => (
                    <img
                      key={f} src={`${API}/api/trend/frames/${job.jobId}/${f}`}
                      onClick={() => setFrameIdx(i)}
                      className={`h-14 rounded border cursor-pointer ${i === frameIdx ? "border-violet-500" : "border-slate-200"}`}
                    />
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        )}
      </Card>
    </div>
  );
}
