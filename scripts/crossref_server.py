#!/usr/bin/env python3
"""
Local upload-and-match UI for scripts/amazon_crossref.py.

Serves a small web page (no build step, stdlib only) where you can bulk-upload
several Amazon sheets at once. They are processed ONE BY ONE; each finished sheet
gets its own tab with a preview and its own Excel download, while the rest keep
running — so nothing gets mixed up.

Usage:
    python3 scripts/crossref_server.py --port 8788
    open http://localhost:8788
"""
from __future__ import annotations

import argparse
import cgi
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "amazon_crossref.py")
JOBS_DIR = os.path.join(HERE, ".crossref_jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

JOBS = {}          # sheet_id -> job dict
BATCHES = {}       # batch_id -> {"sheets": [sheet_id...], "created": ts}
PROCS = {}         # sheet_id -> live Popen (for cancel)
LOCK = threading.Lock()
BATCH_LOCK = threading.Lock()   # only one batch runs its subprocesses at a time


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "sheet"

DEFAULTS = {
    "gajab": os.path.expanduser("~/Downloads/All_products.xlsx"),
    "threshold": "85",
    "workers": "8",
    "no_images": "0",
}


def _job_update(jid, **kw):
    with LOCK:
        JOBS.setdefault(jid, {}).update(kw)


def _parse_progress(log_text):
    """Extract (done, total) from the image-pass 'X/Y' lines, if present."""
    m = None
    for m2 in re.finditer(r"(\d+)/(\d+)\b", log_text):
        m = m2
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def _run_one(jid, upload_path, gajab, out_path, threshold, workers, no_images, log_path):
    """Run the matcher subprocess for one sheet (blocking)."""
    cmd = [sys.executable, SCRIPT, "--amazon", upload_path, "--gajab", gajab,
           "--out", out_path, "--threshold", str(threshold), "--workers", str(workers)]
    if no_images:
        cmd.append("--no-images")
    _job_update(jid, status="running", started=time.time(), cmd=" ".join(cmd),
                cancelled=False, error=None)
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=HERE)
        with LOCK:
            JOBS.setdefault(jid, {})["proc"] = proc
            JOBS[jid]["pid"] = proc.pid
            PROCS[jid] = proc
        proc.wait()
        with LOCK:
            cancelled = bool(JOBS.get(jid, {}).get("cancelled"))
            PROCS.pop(jid, None)
            JOBS.get(jid, {}).pop("proc", None)
        with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
            log = lf.read()
        summary = {}
        m = re.search(r"Exists (\d+), Review (\d+), Not Found (\d+)", log)
        if m:
            summary = {"Exists": int(m.group(1)), "Review": int(m.group(2)),
                       "Not Found": int(m.group(3))}
        if cancelled:
            _job_update(jid, status="cancelled", finished=time.time(),
                        log=log[-4000:], summary=summary, error=None)
        else:
            ok = proc.returncode == 0 and os.path.exists(out_path)
            _job_update(jid, status="done" if ok else "error",
                        finished=time.time(), log=log[-4000:], summary=summary,
                        error=None if ok else f"exit {proc.returncode}")
    except Exception as e:
        with LOCK:
            PROCS.pop(jid, None)
            JOBS.get(jid, {}).pop("proc", None)
        _job_update(jid, status="error", finished=time.time(), error=str(e))


def run_batch(batch_id, specs):
    """Process one batch's sheets sequentially. `specs` = list of job ids."""
    with BATCH_LOCK:  # serialise whole batches so vision models aren't thrashed
        _job_update_batch(batch_id, status="running")
        for jid in specs:
            with LOCK:
                job = dict(JOBS.get(jid, {}))
            if job.get("cancelled") or job.get("status") == "cancelled":
                continue
            args = job.get("args") or {}
            _run_one(jid, job["upload"], args["gajab"], job["out_path"],
                     args["threshold"], args["workers"], args["no_images"], job["log_path"])
        _job_update_batch(batch_id, status="done", finished=time.time())


def _job_update_batch(bid, **kw):
    with LOCK:
        BATCHES.setdefault(bid, {}).update(kw)


def _sheet_public(jid):
    with LOCK:
        j = JOBS.get(jid, {})
    return {
        "id": jid,
        "name": j.get("name", ""),
        "status": j.get("status", "queued"),
        "summary": j.get("summary", {}),
        "error": j.get("error"),
        "out_name": j.get("out_name", ""),
    }


def cancel_sheet(jid):
    with LOCK:
        job = JOBS.get(jid)
        if not job:
            return False
        job["cancelled"] = True
        proc = PROCS.get(jid)
        if job.get("status") == "queued":
            job["status"] = "cancelled"
            job["finished"] = time.time()
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception:
            pass
    return True


def retry_sheet(jid):
    with LOCK:
        job = JOBS.get(jid)
        if not job or job.get("status") in ("running", "queued"):
            return False
        job["status"] = "queued"
        job["cancelled"] = False
        job["summary"] = {}
        job["error"] = None
        job.pop("finished", None)
        out = job.get("out_path")
    if out and os.path.exists(out):
        try:
            os.remove(out)
        except Exception:
            pass
    threading.Thread(target=_retry_worker, args=(jid,), daemon=True).start()
    return True


def _retry_worker(jid):
    with BATCH_LOCK:   # keep retries serialised with batches
        with LOCK:
            job = dict(JOBS.get(jid, {}))
        if job.get("cancelled") or job.get("status") == "cancelled":
            return
        args = job.get("args") or {}
        _run_one(jid, job["upload"], args["gajab"], job["out_path"],
                 args["threshold"], args["workers"], args["no_images"], job["log_path"])


def cancel_batch(bid):
    with LOCK:
        ids = list(BATCHES.get(bid, {}).get("sheets", []))
    for jid in ids:
        cancel_sheet(jid)
    return ids


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Amazon <-> Gajab Cross-Reference</title>
<style>
  :root { --bg:#0f172a; --card:#1e293b; --line:#334155; --txt:#e2e8f0; --muted:#94a3b8;
          --accent:#38bdf8; --green:#16a34a; --amber:#d97706; --red:#dc2626; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--txt); }
  header { padding:20px 28px; border-bottom:1px solid var(--line); }
  h1 { margin:0; font-size:20px; } h1 span { color:var(--accent); }
  .sub { color:var(--muted); font-size:13px; margin-top:4px; }
  main { max-width:1200px; margin:0 auto; padding:24px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:20px; margin-bottom:20px; }
  label { display:block; font-size:12px; color:var(--muted); margin:10px 0 4px; }
  input[type=text],input[type=number],input[type=file] {
      width:100%; padding:10px 12px; background:#0b1220; color:var(--txt);
      border:1px solid var(--line); border-radius:8px; }
  .row { display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; }
  .chk { display:flex; align-items:center; gap:8px; margin-top:16px; color:var(--txt); }
  button { padding:11px 22px; border:0; border-radius:8px; cursor:pointer;
           background:var(--accent); color:#04263a; font-weight:700; font-size:14px; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  #run { margin-top:18px; }
  .tabs { display:flex; flex-wrap:wrap; gap:6px; border-bottom:1px solid var(--line); margin-bottom:16px; }
  .tab { background:#0b1220; color:var(--txt); border:1px solid var(--line); border-bottom:0;
         border-radius:8px 8px 0 0; padding:9px 14px; font-weight:600; font-size:13px;
         display:flex; align-items:center; gap:8px; max-width:260px; }
  .tab.active { background:var(--card); color:var(--accent); border-color:var(--accent); }
  .tab .nm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--muted); flex:0 0 auto; }
  .dot.running { background:var(--accent); animation:pulse 1s infinite; }
  .dot.done { background:var(--green); }
  .dot.error { background:var(--red); }
  .dot.cancelled { background:var(--muted); }
  @keyframes pulse { 50%{opacity:.35} }
  .tabbar { display:flex; justify-content:space-between; align-items:center; gap:12px; }
  .btn.small { padding:6px 12px; font-size:12px; border-radius:6px; }
  .btn.danger { background:#7f1d1d; color:#fecaca; }
  .btn.ghost { background:#0b1220; color:var(--txt); border:1px solid var(--line); font-weight:600; }
  .btn.ghost:hover { border-color:var(--accent); color:var(--accent); }
  .bar { height:8px; background:#0b1220; border-radius:99px; overflow:hidden; margin-top:14px; }
  .bar > div { height:100%; width:0; background:var(--accent); transition:width .3s; }
  .bar.indet > div { width:35% !important; animation:slide 1.2s infinite; }
  @keyframes slide { 0%{margin-left:-35%} 100%{margin-left:100%} }
  pre { background:#0b1220; border:1px solid var(--line); border-radius:8px; padding:12px;
        max-height:200px; overflow:auto; font-size:12px; color:#cbd5e1; white-space:pre-wrap; }
  .cards { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:14px; }
  .stat { background:#0b1220; border:1px solid var(--line); border-radius:10px; padding:16px; }
  .stat b { display:block; font-size:28px; } .stat small { color:var(--muted); }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
          vertical-align:top; max-width:340px; }
  th { position:sticky; top:0; background:var(--card); color:var(--muted); font-size:11px;
       text-transform:uppercase; letter-spacing:.04em; }
  tr.Exists td:first-child { border-left:4px solid var(--green); }
  tr.Review td:first-child { border-left:4px solid var(--amber); }
  tr.NotFound td:first-child { border-left:4px solid var(--red); }
  .pill { padding:2px 8px; border-radius:99px; font-weight:700; font-size:11px; }
  .pill.Exists { background:rgba(22,163,74,.2); color:#4ade80; }
  .pill.Review { background:rgba(217,119,6,.2); color:#fbbf24; }
  .pill.NotFound { background:rgba(220,38,38,.2); color:#f87171; }
  .hidden { display:none; } a.dl { color:var(--accent); font-weight:700; text-decoration:none; }
  .empty { color:var(--muted); padding:20px 0; }
</style></head>
<body>
<header>
  <h1>Amazon <span>&#8644;</span> Gajab Cross-Reference</h1>
  <div class="sub">Bulk-upload Amazon sheets &rarr; they run one by one &rarr; each gets its own tab, preview and Excel export.</div>
</header>
<main>
  <div class="card">
    <label>Amazon sheets (.xlsx) &mdash; select one or many (processed in order)</label>
    <input type="file" id="file" accept=".xlsx,.xls" multiple/>
    <div class="row">
      <div><label>Gajab source (All_products.xlsx)</label>
        <input type="text" id="gajab"/></div>
      <div><label>Exists threshold</label>
        <input type="number" id="threshold" min="1" max="100"/></div>
      <div><label>Image-pass workers</label>
        <input type="number" id="workers" min="1" max="16"/></div>
    </div>
    <div class="chk"><input type="checkbox" id="no_images"/>
      <label for="no_images" style="margin:0">Skip image comparison (fast, text only)</label></div>
    <button id="run">Run match</button>
  </div>

  <div class="card hidden" id="batchCard">
    <div class="tabbar">
      <div class="tabs" id="tabs"></div>
      <button class="btn danger small" id="cancelAll">Cancel all</button>
    </div>
    <div id="panel"></div>
  </div>
</main>
<script>
const $ = s => document.querySelector(s);
const state = { batchId:null, sheets:[], active:null, timer:null, previews:{} };

fetch('/api/config').then(r=>r.json()).then(c=>{
  $('#gajab').value = c.gajab; $('#threshold').value = c.threshold; $('#workers').value = c.workers;
});

$('#run').onclick = async () => {
  const files = [...$('#file').files];
  if (!files.length) { alert('Pick at least one Amazon .xlsx'); return; }
  const fd = new FormData();
  files.forEach(f => fd.append('file', f));
  fd.append('gajab', $('#gajab').value);
  fd.append('threshold', $('#threshold').value);
  fd.append('workers', $('#workers').value);
  fd.append('no_images', $('#no_images').checked ? '1' : '0');
  $('#run').disabled = true;
  const r = await (await fetch('/api/match', { method:'POST', body: fd })).json();
  if (r.error) { alert('Error: ' + r.error); $('#run').disabled = false; return; }
  state.batchId = r.batchId; state.sheets = r.sheets; state.active = r.sheets[0].id;
  state.previews = {};
  $('#batchCard').classList.remove('hidden');
  renderTabs(); renderPanel(); poll();
};

function sheetById(id) { return state.sheets.find(s => s.id === id); }

async function poll() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(async () => {
    const b = await (await fetch('/api/batch/' + state.batchId)).json();
    state.sheets = b.sheets;
    renderTabs();
    renderPanel();
    if (b.status === 'done') { clearInterval(state.timer); state.timer = null; $('#run').disabled = false; }
  }, 1500);
}

function renderTabs() {
  const bar = $('#tabs'); bar.innerHTML = '';
  state.sheets.forEach(s => {
    const b = document.createElement('button');
    b.className = 'tab' + (s.id === state.active ? ' active' : '');
    b.innerHTML = `<span class="dot ${s.status}"></span><span class="nm">${esc(s.name)}</span>`;
    b.onclick = () => { state.active = s.id; renderTabs(); renderPanel(); };
    bar.appendChild(b);
  });
}

function renderPanel() {
  const s = sheetById(state.active);
  const p = $('#panel');
  if (!s) { p.innerHTML = ''; return; }
  if (s.status === 'queued') {
    p.innerHTML = `<div class="empty">Queued &mdash; waiting for earlier sheets…</div>
      <button class="btn ghost small" onclick="doCancel('${s.id}')">Cancel this sheet</button>`;
    return;
  }
  if (s.status === 'running') {
    p.innerHTML = `<div id="status">Matching…</div>
      <div class="bar indet" id="bar"><div></div></div>
      <pre id="log"></pre>
      <button class="btn danger small" onclick="doCancel('${s.id}')">Cancel this sheet</button>`;
    refreshRunning(s.id);
    return;
  }
  if (s.status === 'cancelled') {
    p.innerHTML = `<div class="empty">Cancelled.</div>
      <button class="btn ghost small" onclick="doRetry('${s.id}')">Retry</button>`;
    return;
  }
  if (s.status === 'error') {
    p.innerHTML = `<div class="empty" style="color:#f87171">Error: ${esc(s.error||'run failed')}</div>
      <pre>${esc(s.log||'')}</pre>
      <button class="btn ghost small" onclick="doRetry('${s.id}')">Retry</button>`;
    return;
  }
  // done
  const sm = s.summary || {};
  p.innerHTML = `
    <div class="cards">
      <div class="stat"><b style="color:#4ade80">${sm['Exists']??0}</b><small>Exists</small></div>
      <div class="stat"><b style="color:#fbbf24">${sm['Review']??0}</b><small>Review (near-similar)</small></div>
      <div class="stat"><b style="color:#f87171">${sm['Not Found']??0}</b><small>Not Found</small></div>
    </div>
    <p style="margin:6px 0 16px"><a class="dl" href="/api/download/${s.id}">&#11123; Download ${esc(s.out_name||'Excel report')}</a></p>
    <div id="preview"></div>`;
  loadPreview(s.id);
}

async function doCancel(id) {
  await fetch('/api/cancel/' + id, { method:'POST' });
  pollOnce();
}
async function doRetry(id) {
  const r = await (await fetch('/api/retry/' + id, { method:'POST' })).json();
  if (!r.ok) { alert('Cannot retry right now (still running).'); return; }
  delete state.previews[id];
  poll();
}
async function doCancelAll() {
  if (!state.batchId) return;
  await fetch('/api/cancel-batch/' + state.batchId, { method:'POST' });
  pollOnce();
}
async function pollOnce() {
  if (!state.batchId) return;
  const b = await (await fetch('/api/batch/' + state.batchId)).json();
  state.sheets = b.sheets; renderTabs(); renderPanel();
}
$('#cancelAll').onclick = doCancelAll;

async function refreshRunning(id) {
  const j = await (await fetch('/api/jobs/' + id)).json();
  const logEl = $('#log'); if (!logEl) return;
  logEl.textContent = j.log || '';
  logEl.scrollTop = logEl.scrollHeight;
  const bar = $('#bar'), st = $('#status');
  if (j.progress && j.progress[1]) {
    bar.classList.remove('indet');
    bar.firstElementChild.style.width = Math.round(100*j.progress[0]/j.progress[1]) + '%';
    st.textContent = `Matching… image pass ${j.progress[0]}/${j.progress[1]}`;
  }
}

async function loadPreview(id) {
  if (state.previews[id]) { paintPreview(state.previews[id]); return; }
  const p = await (await fetch('/api/preview/' + id)).json();
  state.previews[id] = p.rows || [];
  if (sheetById(id) && sheetById(id).status === 'done') paintPreview(state.previews[id]);
}

function paintPreview(rows) {
  const box = $('#preview'); if (!box) return;
  if (!rows.length) { box.innerHTML = '<div class="empty">No preview rows.</div>'; return; }
  const cols = Object.keys(rows[0]);
  let h = '<div class="sub" style="margin-bottom:8px">Preview (first 200 rows)</div><div style="overflow:auto;max-height:70vh"><table><thead><tr>';
  cols.forEach(c => h += `<th>${esc(c)}</th>`);
  h += '</tr></thead><tbody>';
  rows.forEach(row => {
    h += `<tr class="${(row['Match Status']||'').replace(/\s/g,'')}">`;
    cols.forEach(c => {
      if (c === 'Match Status') h += `<td><span class="pill ${esc(row[c]||'')}">${esc(row[c]||'')}</span></td>`;
      else h += `<td>${esc(row[c]==null?'':row[c])}</td>`;
    });
    h += '</tr>';
  });
  h += '</tbody></table></div>';
  box.innerHTML = h;
}

function esc(v) { return String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
</script>
</body></html>
"""

PREVIEW_COLS = ["#", "Match Status", "Match Confidence", "Match Type", "Amazon Title",
                "Amazon Seller Name", "Amazon Seller Store Link", "Matched Gajab SKU",
                "Matched Gajab Product Name", "Matched Gajab Category (L1>L2>L3>L4)",
                "Matched Gajab URL", "Text Score", "Image Score", "DINOv2 Cos", "CLIP Cos",
                "SigLIP Cos", "Semantic Min", "pHash Distance"]


class Handler(BaseHTTPRequestHandler):
    server_version = "CrossrefUI/2.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if p == "/api/config":
            return self._send(200, json.dumps(DEFAULTS))
        m = re.match(r"^/api/batch/([\w-]+)$", p)
        if m:
            return self._batch(m.group(1))
        m = re.match(r"^/api/jobs/([\w-]+)$", p)
        if m:
            return self._job(m.group(1))
        m = re.match(r"^/api/preview/([\w-]+)$", p)
        if m:
            return self._preview(m.group(1))
        m = re.match(r"^/api/download/([\w-]+)$", p)
        if m:
            return self._download(m.group(1))
        self._send(404, json.dumps({"error": "not found"}))

    def _batch(self, bid):
        with LOCK:
            b = dict(BATCHES.get(bid, {}))
            ids = list(b.get("sheets", []))
        if not ids:
            return self._send(404, json.dumps({"error": "unknown batch"}))
        sheets = [_sheet_public(i) for i in ids]
        any_running = any(s["status"] in ("queued", "running") for s in sheets)
        status = "running" if any_running else "done"
        return self._send(200, json.dumps({"status": status, "sheets": sheets}))

    def _job(self, jid):
        with LOCK:
            job = dict(JOBS.get(jid, {"status": "unknown"}))
        job.pop("proc", None)
        job.pop("args", None)
        if job.get("status") == "running" and job.get("log_path"):
            try:
                with open(job["log_path"], "r", encoding="utf-8", errors="replace") as f:
                    log = f.read()
                job["log"] = log[-2000:]
                job["progress"] = _parse_progress(log)
            except Exception:
                pass
        return self._send(200, json.dumps(job))

    def _preview(self, jid):
        with LOCK:
            job = dict(JOBS.get(jid, {}))
        out = job.get("out_path")
        if not out or not os.path.exists(out):
            return self._send(200, json.dumps({"rows": []}))
        try:
            df = pd.read_excel(out, sheet_name="Results", nrows=200)
            cols = [c for c in PREVIEW_COLS if c in df.columns]
            df = df[cols].where(pd.notna(df[cols]), None)
            rows = json.loads(df.to_json(orient="records"))
            return self._send(200, json.dumps({"rows": rows}))
        except Exception as e:
            return self._send(200, json.dumps({"rows": [], "error": str(e)}))

    def _download(self, jid):
        with LOCK:
            job = dict(JOBS.get(jid, {}))
        out = job.get("out_path")
        if not out or not os.path.exists(out):
            return self._send(404, json.dumps({"error": "no file"}))
        with open(out, "rb") as f:
            data = f.read()
        name = job.get("out_name") or os.path.basename(out)
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        p = urlparse(self.path).path
        if p != "/api/match":
            m = re.match(r"^/api/cancel-batch/([\w-]+)$", p)
            if m:
                ids = cancel_batch(m.group(1))
                return self._send(200, json.dumps({"ok": True, "cancelled": ids}))
            m = re.match(r"^/api/cancel/([\w-]+)$", p)
            if m:
                return self._send(200, json.dumps({"ok": cancel_sheet(m.group(1))}))
            m = re.match(r"^/api/retry/([\w-]+)$", p)
            if m:
                return self._send(200, json.dumps({"ok": retry_sheet(m.group(1))}))
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                                    environ={"REQUEST_METHOD": "POST",
                                             "CONTENT_TYPE": self.headers.get("Content-Type", "")})
            # Multipart parses every part into form.list; collect the file parts.
            items = [fs for fs in getattr(form, "list", [])
                     if getattr(fs, "name", None) == "file" and getattr(fs, "file", None)]
            if not items:
                single = form["file"] if "file" in form else None
                if single is not None and getattr(single, "filename", None):
                    items = [single]
            if not items:
                return self._send(200, json.dumps({"error": "no files uploaded"}))
            gajab = (form.getfirst("gajab") or DEFAULTS["gajab"]).strip()
            gajab = os.path.abspath(os.path.expanduser(gajab))
            if not os.path.exists(gajab):
                return self._send(200, json.dumps({"error": f"Gajab source not found: {gajab}"}))
            threshold = form.getfirst("threshold") or DEFAULTS["threshold"]
            workers = form.getfirst("workers") or DEFAULTS["workers"]
            no_images = (form.getfirst("no_images") or "0") == "1"

            bid = uuid.uuid4().hex[:12]
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            specs = []
            sheets = []
            seen_names = {}
            for item in items:
                fname = os.path.basename(item.filename or "amazon.xlsx")
                jid = uuid.uuid4().hex[:12]
                jdir = os.path.join(JOBS_DIR, jid)
                os.makedirs(jdir, exist_ok=True)
                upload = os.path.join(jdir, fname)
                with open(upload, "wb") as f:
                    f.write(item.file.read())
                # Descriptive, unique export name per sheet (source + purpose + time).
                stem = _safe_name(os.path.splitext(fname)[0])
                n = seen_names.get(stem, 0) + 1
                seen_names[stem] = n
                suffix = f"_{n}" if n > 1 else ""
                out_name = f"{stem}_amazon_gajab_match_{ts}{suffix}.xlsx"
                out_path = os.path.join(jdir, out_name)
                log_path = os.path.join(jdir, "run.log")
                with LOCK:
                    JOBS[jid] = {
                        "status": "queued", "name": fname, "upload": upload,
                        "out_path": out_path, "out_name": out_name, "log_path": log_path,
                        "summary": {},
                        "args": {"gajab": gajab, "threshold": threshold,
                                 "workers": workers, "no_images": no_images},
                    }
                specs.append(jid)
                sheets.append({"id": jid, "name": fname})
            with LOCK:
                BATCHES[bid] = {"sheets": specs, "created": time.time(), "status": "queued"}
            threading.Thread(target=run_batch, args=(bid, specs), daemon=True).start()
            return self._send(200, json.dumps({"batchId": bid, "sheets": sheets}))
        except Exception as e:
            return self._send(200, json.dumps({"error": str(e)}))


def main():
    ap = argparse.ArgumentParser(description="Local bulk upload-and-match UI")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--gajab", default=DEFAULTS["gajab"])
    ap.add_argument("--threshold", default="85")
    ap.add_argument("--workers", default="8")
    ap.add_argument("--no-images", action="store_true")
    a = ap.parse_args()
    DEFAULTS["gajab"] = os.path.abspath(os.path.expanduser(a.gajab))
    DEFAULTS["threshold"] = str(a.threshold)
    DEFAULTS["workers"] = str(a.workers)
    DEFAULTS["no_images"] = "1" if a.no_images else "0"
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[crossref-ui] http://{a.host}:{a.port}  (Gajab: {DEFAULTS['gajab']})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
