#!/usr/bin/env python3
"""
Bulk-create attributes, attribute groups, specifications and family assignments
from 'Attributes Final With Valid Entries.xlsx' via the Gajab backend API.

Phases:
  A. attributes  (unique by name)          POST /product/api/attribute/
  B. groups      (unique by name)          POST /product/api/attribute-group/
  C. specs       (unique by name)          POST /product/api/specification
  D. assignments (batched per family)      PUT  /product/api/spec-category/<l4id>

Idempotent: fetches existing objects first and only creates what's missing, so
it can be re-run to resume after an interruption.

Usage:
  python3 create_attributes_final.py --workers 16
"""
from __future__ import annotations
import json, sys, threading, time, urllib.error, urllib.request
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import openpyxl

SCRIPT_DIR = Path(__file__).parent
TOKEN_FILE = SCRIPT_DIR / ".admin_token"
EXCEL_FILE = Path("/Users/gajabmarketing/Downloads/Attributes Final With Valid Entries.xlsx")
API = "https://gatewayservice.gajab.com"

TYPE_MAP = {"Integer": "integer", "Checkbox": "check-box", "Paragraph": "paragraph",
            "Dropdown": "Drop-down", "Date": "date", "Decimal": "decimal",
            "Short Text": "short-text", "Radio": "radio"}

_TL = threading.Lock()


def log(msg):
    with _TL:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def token():
    return TOKEN_FILE.read_text().strip()


def _head(req, tok):
    for k, v in [("Authorization", tok), ("Origin", "https://admin.gajab.com"),
                 ("Referer", "https://admin.gajab.com/"),
                 ("Accept", "application/json, text/plain, */*")]:
        req.add_header(k, v)


def api_get(path, tok):
    req = urllib.request.Request(API + path)
    _head(req, tok)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def api_send(method, path, payload, tok, retries=3):
    data = json.dumps(payload).encode()
    last = None
    for _ in range(retries):
        req = urllib.request.Request(API + path, data=data, method=method)
        _head(req, tok)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return r.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            last = (e.code, {"error": body[:300]})
            if e.code < 500:
                return last
        except Exception as e:
            last = (0, {"error": str(e)[:200]})
        time.sleep(0.5)
    return last


def fetch_all(path_root, tok, cache_name=None):
    """Fetch a paginated list endpoint fully, with retries and local caching."""
    cache = SCRIPT_DIR / f".cache_{cache_name}.json" if cache_name else None
    if cache and cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass
    out, offset, page = [], 0, 1000
    while True:
        chunk = None
        for attempt in range(6):
            try:
                d = api_get(f"{path_root}?limit={page}&offset={offset}&keyword=&count=0", tok)
                chunk = d.get("data", [])
                break
            except Exception as e:
                log(f"  fetch {path_root} offset={offset} attempt {attempt+1} failed: {str(e)[:80]}")
                time.sleep(2)
        if chunk is None:
            raise RuntimeError(f"could not fetch {path_root} at offset {offset}")
        out.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
    if cache:
        try:
            cache.write_text(json.dumps(out))
        except Exception:
            pass
    return out


def g(row, i):
    return str(row[i]).strip() if len(row) > i and row[i] is not None else ""


def parse_sheet():
    wb = openpyxl.load_workbook(EXCEL_FILE, data_only=True, read_only=True)
    ws = wb["Attribute Final"]
    attr_defs = defaultdict(Counter)          # name -> Counter[(type, opts)]
    attr_meta = {}                            # name -> (type, desc, opts)
    group_attr = {}                           # group_name -> attr_name
    spec_def = {}                             # spec_name -> (group, attr, mandatory)
    pairs = set()                             # (node_path, spec_name)
    rows = 0
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not g(r, 0):
            continue
        rows += 1
        a, t, d, grp, sp, mand, valid, path = (g(r, 5), g(r, 6), g(r, 7), g(r, 8),
                                               g(r, 9), g(r, 10), g(r, 11), g(r, 4))
        if not a:
            continue
        attr_defs[a][(t, valid)] += 1
        attr_meta.setdefault(a, (t, d, valid))
        if grp:
            group_attr[grp] = a
        if sp:
            spec_def[sp] = (grp, a, mand.lower() == "yes")
        if path and sp:
            pairs.add((path, sp))
    # pick most common definition per attribute name
    for a, c in attr_defs.items():
        (t, valid), _ = c.most_common(1)[0]
        attr_meta[a] = (t, attr_meta[a][1], valid)
    return rows, attr_meta, group_attr, spec_def, pairs


def create_options(valid):
    if not valid:
        return [{"value": ""}]
    return [{"value": v.strip()} for v in valid.split(";") if v.strip()]


def main():
    workers = 16
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    dry = "--dry-run" in sys.argv
    if "--refresh-cache" in sys.argv:
        for c in [".cache_attrs.json", ".cache_groups.json", ".cache_specs.json"]:
            (SCRIPT_DIR / c).unlink(missing_ok=True)

    tok = token()
    t0 = time.time()
    rows, attr_meta, group_attr, spec_def, pairs = parse_sheet()
    log(f"parsed {rows} rows | {len(attr_meta)} attrs | {len(group_attr)} groups | "
        f"{len(spec_def)} specs | {len(pairs)} assignments")

    attrs = fetch_all("/product/api/attribute", tok, "attrs")
    groups = fetch_all("/product/api/attribute-group", tok, "groups")
    specs = fetch_all("/product/api/specification", tok, "specs")
    cats = api_get("/product/api/category?limit=5000&offset=0&keyword=&status=&name=", tok)["data"]
    attr_by_name = {x["name"].strip().lower(): x["id"] for x in attrs}
    group_by_name = {x["name"].strip(): x["id"] for x in groups}
    spec_by_name = {x["name"].strip(): x["id"] for x in specs}
    cat_by_path = {c.get("levels", "").strip(): c["categoryId"] for c in cats}
    log(f"existing: {len(attrs)} attrs, {len(groups)} groups, {len(specs)} specs")

    if dry:
        log(f"would create {sum(1 for a in attr_meta if a.lower() not in attr_by_name)} attrs, "
            f"{sum(1 for g_ in group_attr if g_ not in group_by_name)} groups, "
            f"{sum(1 for s in spec_def if s not in spec_by_name)} specs")
        return

    # ---- Phase A: attributes ----
    todo_attr = [a for a in attr_meta if a.lower() not in attr_by_name]
    log(f"Phase A: {len(todo_attr)} attributes to create")
    attr_sort = len(attrs)

    def mk_attr(a):
        t, d, valid = attr_meta[a]
        payload = {"name": a, "type": TYPE_MAP.get(t, t.lower()), "isMandatory": 0,
                   "useAsFilter": 1, "isActive": 1, "description": d, "label": a,
                   "sectionName": "", "defaultValue": "",
                   "attributeValues": create_options(valid), "sortOrder": 1000}
        st, resp = api_send("POST", "/product/api/attribute/", payload, tok)
        aid = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
        return a, st, aid, resp

    if todo_attr:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(mk_attr, a): a for a in todo_attr}
            done = 0
            for f in as_completed(futs):
                a, st, aid, resp = f.result()
                done += 1
                if aid:
                    attr_by_name[a.lower()] = aid
                else:
                    log(f"  ATTR FAIL {a!r}: {st} {str(resp)[:150]}")
                if done % 50 == 0 or done == len(todo_attr):
                    log(f"  attrs {done}/{len(todo_attr)}")
    log(f"Phase A done ({time.time()-t0:.0f}s)")

    # ---- Phase B: groups ----
    todo_group = [gn for gn in group_attr if gn not in group_by_name]
    log(f"Phase B: {len(todo_group)} groups to create")
    gsort = [len(groups)]

    def mk_group(gn):
        with _TL:
            gsort[0] += 1
            so = gsort[0]
        aid = attr_by_name.get(group_attr[gn].lower())
        payload = {"name": gn, "sortOrder": so, "attributeIds": [aid] if aid else []}
        st, resp = api_send("POST", "/product/api/attribute-group/", payload, tok)
        gid = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
        return gn, st, gid

    if todo_group:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(mk_group, gn): gn for gn in todo_group}
            done = failed = 0
            for f in as_completed(futs):
                gn, st, gid = f.result()
                done += 1
                if gid:
                    group_by_name[gn] = gid
                else:
                    failed += 1
                    if failed <= 20:
                        log(f"  GROUP FAIL {gn!r}: {st}")
                if done % 500 == 0 or done == len(todo_group):
                    log(f"  groups {done}/{len(todo_group)} (failed {failed})")
    log(f"Phase B done ({time.time()-t0:.0f}s)")

    # ---- Phase C: specs ----
    todo_spec = [sn for sn in spec_def if sn not in spec_by_name]
    log(f"Phase C: {len(todo_spec)} specs to create")

    def mk_spec(sn):
        grp, a, mand = spec_def[sn]
        gid = group_by_name.get(grp)
        aid = attr_by_name.get(a.lower())
        if not gid or not aid:
            return sn, 0, None, {"error": f"missing gid={gid} aid={aid}"}
        payload = {"name": sn, "status": 1,
                   "attributeGroup": [{"attributeGroupId": gid,
                                       "attributeIds": [{"attributeId": aid,
                                                         "isMandatory": 1 if mand else 0}]}],
                   "attributeGroupId": []}
        st, resp = api_send("POST", "/product/api/specification", payload, tok)
        sid = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
        return sn, st, sid, resp

    if todo_spec:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(mk_spec, sn): sn for sn in todo_spec}
            done = failed = 0
            for f in as_completed(futs):
                sn, st, sid, resp = f.result()
                done += 1
                if sid:
                    spec_by_name[sn] = sid
                else:
                    failed += 1
                    if failed <= 20:
                        log(f"  SPEC FAIL {sn!r}: {st} {str(resp)[:120]}")
                if done % 500 == 0 or done == len(todo_spec):
                    log(f"  specs {done}/{len(todo_spec)} (failed {failed})")
    log(f"Phase C done ({time.time()-t0:.0f}s)")

    # ---- Phase D: assignments ----
    by_cat = defaultdict(set)
    missing_paths = set()
    for path, sp in pairs:
        cid = cat_by_path.get(path)
        if not cid:
            alt = path.replace("Books & General Merchandise", "Toys & General Merchandise")
            cid = cat_by_path.get(alt)
        if not cid:
            missing_paths.add(path)
            continue
        sid = spec_by_name.get(sp)
        if sid:
            by_cat[cid].add(sid)
    log(f"Phase D: {len(by_cat)} families to assign | {len(missing_paths)} paths unresolvable")

    def do_assign(cid):
        sids = sorted(by_cat[cid])
        ok = True
        for i in range(0, len(sids), 100):
            chunk = sids[i:i+100]
            payload = {"specification": [{"specificationId": s} for s in chunk],
                       "deleteSpecificationIds": []}
            st, resp = api_send("PUT", f"/product/api/spec-category/{cid}", payload, tok)
            if not (st in (200, 201) and (resp.get("status") in (1, "1", True) or "data" in resp)):
                ok = False
                log(f"  ASSIGN FAIL cat={cid}: {st} {str(resp)[:120]}")
                break
        return cid, ok

    assigned = afail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(do_assign, cid): cid for cid in by_cat}
        done = 0
        for f in as_completed(futs):
            cid, ok = f.result()
            done += 1
            if ok:
                assigned += 1
            else:
                afail += 1
            if done % 200 == 0 or done == len(by_cat):
                log(f"  families {done}/{len(by_cat)} (failed {afail})")

    log("=" * 60)
    log(f"DONE in {(time.time()-t0)/60:.1f} min | families assigned {assigned}, failed {afail}, "
        f"unresolvable paths {len(missing_paths)}")
    for p in sorted(missing_paths)[:40]:
        log(f"  SKIPPED (family missing): {p}")


if __name__ == "__main__":
    main()
