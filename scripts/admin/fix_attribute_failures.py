#!/usr/bin/env python3
"""Repair groups/specs that failed with transient errors during the final
attribute run, and (re)assign their specifications to the L4 families.
Robust: retries timeouts/5xx and treats 'already exists' as success."""
import json, re, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
import openpyxl

SCRIPT_DIR = Path(__file__).parent
TOKEN_FILE = SCRIPT_DIR / ".admin_token"
EXCEL_FILE = Path("/Users/gajabmarketing/Downloads/Attributes Final With Valid Entries.xlsx")
LOG = SCRIPT_DIR / "attr_final.log"
API = "https://gatewayservice.gajab.com"
tok = TOKEN_FILE.read_text().strip()


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def api(method, path, payload=None, tries=6, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    for i in range(tries):
        r = urllib.request.Request(API + path, data=data, method=method)
        for k, v in [("Authorization", tok), ("Origin", "https://admin.gajab.com"),
                     ("Referer", "https://admin.gajab.com/"),
                     ("Accept", "application/json, text/plain, */*")]:
            r.add_header(k, v)
        if data:
            r.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(r, timeout=timeout)
            return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                j = json.loads(body)
            except Exception:
                j = {"error": body[:200]}
            if e.code < 500:
                return e.code, j
            time.sleep(1 + i)
        except Exception as e:
            time.sleep(1 + i)
    return 0, {"error": "exhausted retries"}


def find_group(name):
    d = api("GET", f"/product/api/attribute-group?limit=100&offset=0&keyword={urllib.parse.quote(name)}&count=0")[1]
    for x in d.get("data", []):
        if x["name"].strip() == name.strip():
            return x["id"]
    return None


def find_spec(name):
    d = api("GET", f"/product/api/specification?limit=100&offset=0&keyword={urllib.parse.quote(name)}&count=0")[1]
    for x in d.get("data", []):
        if x["name"].strip() == name.strip():
            return x["id"]
    return None


def ensure_group(name, aid):
    gid = find_group(name)
    if gid:
        return gid
    st, resp = api("POST", "/product/api/attribute-group/",
                   {"name": name, "sortOrder": 90000, "attributeIds": [aid] if aid else []})
    gid = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
    return gid or find_group(name)


def ensure_spec(name, gid, aid, mand):
    sid = find_spec(name)
    if sid:
        return sid
    st, resp = api("POST", "/product/api/specification", {
        "name": name, "status": 1,
        "attributeGroup": [{"attributeGroupId": gid,
                            "attributeIds": [{"attributeId": aid, "isMandatory": 1 if mand else 0}]}],
        "attributeGroupId": []})
    sid = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
    return sid or find_spec(name)


gtext = LOG.read_text()
group_fails = set(re.findall(r"GROUP FAIL '([^']+)'", gtext))
spec_fails = set(re.findall(r"SPEC FAIL '([^']+)'", gtext))
log(f"failed groups: {len(group_fails)} | failed specs: {len(spec_fails)}")

wb = openpyxl.load_workbook(EXCEL_FILE, data_only=True, read_only=True)
ws = wb["Attribute Final"]


def g(r, i): return str(r[i]).strip() if len(r) > i and r[i] is not None else ""


by_group, by_spec = {}, {}
for r in ws.iter_rows(min_row=2, values_only=True):
    if not r or not g(r, 0):
        continue
    grp, sp = g(r, 8), g(r, 9)
    if grp in group_fails or sp in spec_fails:
        row = {"attr": g(r, 5), "group": grp, "spec": sp,
               "mand": g(r, 10).lower() == "yes", "path": g(r, 4)}
        by_group.setdefault(grp, row)
        by_spec.setdefault(sp, row)

attrs = api("GET", "/product/api/attribute?limit=1000&offset=0&keyword=&count=0")[1]["data"]
attr_by_name = {a["name"].strip().lower(): a["id"] for a in attrs}

group_gid = {}
for gn in sorted(group_fails):
    row = by_group.get(gn)
    if not row:
        continue
    gid = ensure_group(gn, attr_by_name.get(row["attr"].lower()))
    group_gid[gn] = gid
    log(f"  group {gn[:55]!r} -> {gid}")

spec_sid = {}
for sn in sorted(spec_fails):
    row = by_spec.get(sn)
    if not row:
        continue
    gid = group_gid.get(row["group"]) or find_group(row["group"])
    aid = attr_by_name.get(row["attr"].lower())
    if not gid or not aid:
        log(f"  spec {sn[:55]!r}: missing gid={gid} aid={aid}")
        continue
    sid = ensure_spec(sn, gid, aid, row["mand"])
    spec_sid[sn] = sid
    log(f"  spec {sn[:55]!r} -> {sid}")

cats = None
for _ in range(5):
    st, resp = api("GET", "/product/api/category?limit=5000&offset=0&keyword=&status=&name=")
    if "data" in resp:
        cats = resp["data"]
        break
    log(f"  category fetch retry ({st})")
cat_by_path = {c.get("levels", "").strip(): c["categoryId"] for c in (cats or [])}

ok = miss = 0
for sn, sid in spec_sid.items():
    if not sid:
        continue
    path = by_spec[sn]["path"]
    cid = cat_by_path.get(path) or cat_by_path.get(path.replace("Books & General Merchandise", "Toys & General Merchandise"))
    if not cid:
        log(f"  family missing for {sn!r}: {path}")
        miss += 1
        continue
    st, resp = api("PUT", f"/product/api/spec-category/{cid}",
                   {"specification": [{"specificationId": sid}], "deleteSpecificationIds": []}, tries=5)
    if st in (200, 201):
        ok += 1
    else:
        log(f"  assign FAIL {sn!r} cat={cid}: {st} {str(resp)[:120]}")
log(f"assignments ok={ok}, family-missing={miss}")
missing_specs = [sn for sn, sid in spec_sid.items() if not sid]
if missing_specs:
    log(f"STILL UNRESOLVED specs: {missing_specs}")
