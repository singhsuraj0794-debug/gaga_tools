#!/usr/bin/env python3
"""
Live load-test monitor.

Workers POST progress to /report; this serves a dashboard at / that auto-refreshes.

Run on one machine (e.g. the Mac):
    python3 monitor_server.py --port 8099

Then start workers with (see worker flags):
    --monitor-url http://<this-host-ip>:8099/report --host-name PC1
Open http://<this-host-ip>:8099/ from any machine.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

HOSTS: dict[str, dict] = {}
LOCK = Lock()
STALE_AFTER = 20  # seconds without a report -> show as stale


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>gajab load test — live</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#8a93a3;font-size:13px;margin-bottom:18px}
 table{border-collapse:collapse;width:100%;max-width:1000px;background:#161a22;border-radius:10px;overflow:hidden}
 th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #232936;font-size:14px}
 th{background:#1c222c;color:#9fb0c7;font-weight:600} td.num{text-align:right;font-variant-numeric:tabular-nums}
 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
 .live{background:#3ecf8e}.stale{background:#e0b400}.dead{background:#e05252}
 .totals{max-width:1000px;background:#161a22;border-radius:10px;padding:14px 16px;margin-bottom:18px;display:flex;gap:34px;flex-wrap:wrap}
 .k{color:#8a93a3;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
 .v{font-size:22px;font-weight:700}
 .pct{color:#8a93a3;font-size:12px}
</style></head><body>
<h1>gajab load test — live</h1>
<div class="sub">updated <span id="ts">—</span> · auto-refresh 2s</div>
<div class="totals" id="totals"></div>
<table><thead><tr>
<th>machine</th><th class="num">concurrency</th><th class="num">running</th><th class="num">loads</th>
<th class="num">completed</th><th class="num">land</th><th class="num">offer</th><th class="num">accepted</th><th>age</th>
</tr></thead><tbody id="rows"></tbody></table>
<script>
function cell(v,pct){return v+(pct!==undefined&&pct!==null?` <span class="pct">${pct}%</span>`:'');}
async function tick(){
 try{
  const r=await fetch('/status'); const d=await r.json();
  document.getElementById('ts').textContent=new Date().toLocaleTimeString();
  const t=d.total;
  document.getElementById('totals').innerHTML=
   `<div><div class="k">machines</div><div class="v">${d.hosts.length}</div></div>`+
   `<div><div class="k">concurrency</div><div class="v">${t.sessions}</div></div>`+
   `<div><div class="k">running</div><div class="v">${t.running}</div></div>`+
   `<div><div class="k">loads</div><div class="v">${t.journeys}</div></div>`+
   `<div><div class="k">completed</div><div class="v">${t.completed}</div></div>`+
   `<div><div class="k">offer</div><div class="v">${t.offered}</div></div>`+
   `<div><div class="k">accepted</div><div class="v">${t.accepted}</div></div>`;
  const tb=document.getElementById('rows'); tb.innerHTML='';
  for(const h of d.hosts){
   const lp=h.landed&&h.journeys?Math.round(h.landed/h.journeys*100):0;
   const op=h.offered&&h.journeys?Math.round(h.offered/h.journeys*100):0;
   const cls=h.age>20?'dead':(h.age>8?'stale':'live');
   tb.innerHTML+=`<tr>
     <td><span class="dot ${cls}"></span>${h.host}</td>
     <td class="num">${h.sessions}</td>
     <td class="num">${h.running}</td>
     <td class="num">${h.journeys}</td>
     <td class="num">${h.completed}</td>
     <td class="num">${cell(h.landed,lp)}</td>
     <td class="num">${cell(h.offered,op)}</td>
     <td class="num">${h.accepted}</td>
     <td>${h.age}s</td></tr>`;
  }
 }catch(e){ document.getElementById('ts').textContent='connection lost'; }
}
setInterval(tick,2000); tick();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/status"):
            now = time.time()
            with LOCK:
                hosts = []
                for name, s in sorted(HOSTS.items()):
                    h = dict(s)
                    h["host"] = name
                    h["age"] = round(now - s.get("ts", now), 1)
                    hosts.append(h)
            total = {"sessions": 0, "running": 0, "journeys": 0, "completed": 0,
                     "landed": 0, "offered": 0, "accepted": 0}
            for h in hosts:
                for k in total:
                    total[k] += h.get(k, 0) or 0
            self._send(200, json.dumps({"hosts": hosts, "total": total}))
        elif self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path.startswith("/report"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                payload = {}
            host = payload.get("host") or self.client_address[0]
            payload["ts"] = time.time()
            with LOCK:
                HOSTS[host] = payload
            self._send(200, '{"ok":true}')
        else:
            self._send(404, "{}")

    def log_message(self, *args):  # quiet
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"monitor listening on http://{args.host}:{args.port}/  (workers POST to /report)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
