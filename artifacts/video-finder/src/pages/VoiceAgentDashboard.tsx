import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Activity, BrainCircuit, LockKeyhole, ShieldCheck } from "lucide-react";

type Product = { id: string; name: string; list_price: number };
type Model = { id: string; name: string; active: boolean; settings: Record<string, string | number> };
type Session = { event: string; product_id?: string; session_id?: string; final_offer_accepted?: number; timestamp: number };

const API = "http://localhost:8080";
const adminHeaders = { Authorization: "Bearer admin-token" };
const money = (value: number) => `₹${value.toLocaleString("en-IN")}`;

export default function VoiceAgentDashboard() {
  const [products, setProducts] = useState<Product[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      fetch(`${API}/products`).then((response) => response.json()),
      fetch(`${API}/model-config`, { headers: adminHeaders }).then((response) => response.json()),
      fetch(`${API}/sessions`, { headers: adminHeaders }).then((response) => response.json()),
    ])
      .then(([productData, modelData, sessionData]) => {
        setProducts(productData);
        setModels(modelData);
        setSessions(sessionData);
      })
      .catch(() => setError("Dashboard API is unavailable. Start it on localhost:8080."));
  }, []);

  return (
    <main className="min-h-screen bg-[#080d17] px-5 py-8 text-slate-100 sm:px-8">
      <div className="mx-auto max-w-7xl space-y-7">
        <header className="flex flex-col justify-between gap-5 border-b border-slate-800 pb-7 sm:flex-row sm:items-end">
          <div>
            <p className="mb-2 text-xs font-bold uppercase tracking-[0.22em] text-emerald-300">Voice agent / control room</p>
            <h1 className="text-4xl font-black tracking-[-0.05em] sm:text-6xl">Negotiation systems</h1>
            <p className="mt-3 max-w-xl text-slate-400">Configure models, inspect bounded pricing, and audit outcomes without exposing protected price data to the AI.</p>
          </div>
          <span className="inline-flex w-fit items-center gap-2 rounded-full border border-emerald-900 bg-emerald-950/60 px-3 py-2 text-xs font-semibold text-emerald-300"><Activity className="h-3.5 w-3.5" /> Pricing boundary healthy</span>
        </header>

        {error && <div className="rounded-xl border border-amber-900 bg-amber-950/40 p-4 text-sm text-amber-200">{error}</div>}

        <section className="grid gap-4 sm:grid-cols-3">
          <Metric icon={<Activity />} label="Sessions today" value={String(sessions.length)} detail="Human-visible audit events" />
          <Metric icon={<BrainCircuit />} label="Decision layer" value="Algorithmic" detail="LLM cannot access floor or target" />
          <Metric icon={<ShieldCheck />} label="AI access" value="Range only" detail="Short-lived, session-scoped grants" />
        </section>

        <section className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
          <Panel title="Model settings" icon={<BrainCircuit />}>
            {models.map((model) => <div key={model.id} className="flex items-center justify-between border-b border-slate-800 py-4 last:border-0"><div><p className="font-semibold">{model.name}</p><p className="text-xs text-slate-500">{model.id} · {Object.entries(model.settings).map(([key, value]) => `${key}: ${value}`).join(" · ")}</p></div><span className={`rounded-md px-2 py-1 text-xs ${model.active ? "bg-emerald-950 text-emerald-300" : "bg-slate-800 text-slate-400"}`}>{model.active ? "Active" : "Off"}</span></div>)}
          </Panel>
          <Panel title="Boundary controls" icon={<LockKeyhole />}>
            <Control label="Floor price exposure" value="Blocked" safe />
            <Control label="Offer validation" value="Required" safe />
            <Control label="Grant lifetime" value="5 min" />
            <Control label="Voice layer" value="STT + TTS" />
          </Panel>
        </section>

        <Panel title="Products and price anchors" icon={<LockKeyhole />} subtitle="List price is visible. Floor and target prices remain masked for standard administrators.">
          <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm"><thead className="text-xs uppercase tracking-wider text-slate-500"><tr><th className="pb-3">Product</th><th className="pb-3">ID</th><th className="pb-3">List price</th><th className="pb-3">Floor / target</th><th className="pb-3">Access</th></tr></thead><tbody>{products.map((product) => <tr key={product.id} className="border-t border-slate-800"><td className="py-4 font-medium">{product.name}</td><td className="py-4 text-slate-400">{product.id}</td><td className="py-4">{money(product.list_price)}</td><td className="py-4 tracking-widest text-slate-500">•••• / ••••</td><td className="py-4"><span className="rounded-md bg-emerald-950 px-2 py-1 text-xs text-emerald-300">Protected</span></td></tr>)}</tbody></table></div>
        </Panel>

        <Panel title="Negotiation audit stream" icon={<Activity />}>
          <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm"><thead className="text-xs uppercase tracking-wider text-slate-500"><tr><th className="pb-3">Event</th><th className="pb-3">Product / session</th><th className="pb-3">Outcome</th><th className="pb-3">Time</th></tr></thead><tbody>{sessions.length ? sessions.map((session, index) => <tr key={`${session.timestamp}-${index}`} className="border-t border-slate-800"><td className="py-4">{session.event}</td><td className="py-4 text-slate-400">{session.product_id || "—"} / {session.session_id || "internal"}</td><td className="py-4">{session.final_offer_accepted ? money(session.final_offer_accepted) : "Range issued"}</td><td className="py-4 text-slate-400">{new Date(session.timestamp * 1000).toLocaleString()}</td></tr>) : <tr><td className="py-5 text-slate-500" colSpan={4}>No sessions yet.</td></tr>}</tbody></table></div>
        </Panel>
      </div>
    </main>
  );
}

function Metric({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail: string }) {
  return <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-5"><div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-emerald-300">{icon} {label}</div><p className="mt-4 text-3xl font-black tracking-tight">{value}</p><p className="mt-1 text-sm text-slate-500">{detail}</p></div>;
}

function Panel({ title, subtitle, icon, children }: { title: string; subtitle?: string; icon: ReactNode; children: ReactNode }) {
  return <section className="rounded-2xl border border-slate-800 bg-slate-900/80 p-5 shadow-2xl shadow-black/10"><div className="mb-4 flex items-start gap-3"><span className="rounded-lg bg-slate-800 p-2 text-emerald-300">{icon}</span><div><h2 className="font-bold">{title}</h2>{subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}</div></div>{children}</section>;
}

function Control({ label, value, safe = false }: { label: string; value: string; safe?: boolean }) {
  return <div className="flex items-center justify-between border-b border-slate-800 py-3 text-sm last:border-0"><span className="text-slate-300">{label}</span><span className={`rounded-md px-2 py-1 text-xs ${safe ? "bg-emerald-950 text-emerald-300" : "bg-slate-800 text-slate-300"}`}>{value}</span></div>;
}
