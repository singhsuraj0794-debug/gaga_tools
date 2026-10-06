#!/usr/bin/env python3
"""
Create product attributes, attribute groups, specifications, and assign the
specifications to L4 families -- all via the Gajab backend API.

Source: Attributes - Sample File for Testing.xlsx

Pipeline per Excel row:
  1. POST /product/api/attribute/            -> attribute  (reused by name)
  2. POST /product/api/attribute-group/      -> group      (attributeIds=[attr_id])
  3. POST /product/api/specification         -> spec        (group + attr + isMandatory)
  4. PUT  /product/api/spec-category/<l4id>  -> assign spec to the L4 family

Usage:
  python3 create_attributes.py --dry-run
  python3 create_attributes.py
"""
from __future__ import annotations
import json, sys, time, urllib.error, urllib.request
from pathlib import Path
import openpyxl

SCRIPT_DIR = Path(__file__).parent
TOKEN_FILE = SCRIPT_DIR / ".admin_token"
EXCEL_FILE = Path("/Users/gajabmarketing/Downloads/Attributes - Sample File for Testing.xlsx")

API = "https://gatewayservice.gajab.com"

TYPE_MAP = {
    "Integer": "integer",
    "Checkbox": "check-box",
    "Paragraph": "paragraph",
    "Dropdown": "Drop-down",
    "Date": "date",
    "Decimal": "decimal",
    "Short Text": "short-text",
    "Radio": "radio",
}


def log(msg):
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
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def api_send(method, path, payload, tok):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(API + path, data=data, method=method)
    _head(req, tok)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:400]}


def g(row, i):
    return str(row[i]).strip() if len(row) > i and row[i] is not None else ""


def load_rows():
    wb = openpyxl.load_workbook(EXCEL_FILE, data_only=True)
    ws = wb["Sheet1"]
    out = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[0]:
            continue
        out.append({
            "l1": g(r, 0), "l2": g(r, 1), "l3": g(r, 2), "l4": g(r, 3),
            "node_path": g(r, 4), "attr": g(r, 5), "type": g(r, 6),
            "desc": str(r[7]).strip() if r[7] else "",
            "group": g(r, 8), "spec": g(r, 9),
            "mandatory": g(r, 10).lower() == "yes",
            "valid": str(r[11]).strip() if len(r) > 11 and r[11] else "",
        })
    return out


def main():
    dry = "--dry-run" in sys.argv
    tok = token()
    rows = load_rows()
    log(f"{len(rows)} rows in sheet")

    # Existing state
    attrs = api_get("/product/api/attribute?limit=1000&offset=0&keyword=&count=0", tok)["data"]
    groups = api_get("/product/api/attribute-group?limit=1000&offset=0&keyword=&count=0", tok)["data"]
    specs = api_get("/product/api/specification?limit=1000&offset=0&keyword=&count=0", tok)["data"]
    cats = api_get("/product/api/category?limit=5000&offset=0&keyword=&status=&name=", tok)["data"]

    attr_by_name = {a["name"].strip().lower(): a["id"] for a in attrs}
    group_by_name = {gr["name"].strip(): gr["id"] for gr in groups}
    spec_by_name = {s["name"].strip(): s["id"] for s in specs}
    cat_by_path = {c.get("levels", "").strip(): c.get("categoryId") for c in cats}
    log(f"existing: {len(attrs)} attrs, {len(groups)} groups, {len(specs)} specs, {len(cats)} cats")

    attr_sort = len(attrs)
    summary = []

    for idx, row in enumerate(rows, 1):
        atype = TYPE_MAP.get(row["type"], row["type"].lower())
        log(f"--- row {idx}: {row['attr']!r} ({row['type']}->{atype}) | group={row['group']!r} ---")

        if dry:
            log(f"   would create attribute/group/spec + assign to {row['l4']!r}")
            continue

        # 1. Attribute (reuse by name)
        key = row["attr"].strip().lower()
        if key in attr_by_name:
            attr_id = attr_by_name[key]
            log(f"   attr exists: id={attr_id}")
        else:
            options = ([{"value": v.strip()} for v in row["valid"].split(";") if v.strip()]
                       if row["valid"] else [{"value": ""}])
            attr_sort += 1
            payload = {
                "name": row["attr"], "type": atype, "isMandatory": 0, "useAsFilter": 1,
                "isActive": 1, "description": row["desc"], "label": row["attr"],
                "sectionName": "", "defaultValue": "", "attributeValues": options,
                "sortOrder": attr_sort,
            }
            st, resp = api_send("POST", "/product/api/attribute/", payload, tok)
            attr_id = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
            if st not in (200, 201) or not attr_id:
                log(f"   ATTR FAIL {st}: {str(resp)[:200]}")
                summary.append((row["attr"], "attr-fail"))
                continue
            attr_by_name[key] = attr_id
            log(f"   attr created: id={attr_id}")

        # 2. Attribute group
        gkey = row["group"].strip()
        if gkey in group_by_name:
            group_id = group_by_name[gkey]
            log(f"   group exists: id={group_id}")
        else:
            payload = {"name": row["group"], "sortOrder": len(group_by_name) + 1, "attributeIds": [attr_id]}
            st, resp = api_send("POST", "/product/api/attribute-group/", payload, tok)
            group_id = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
            if st not in (200, 201) or not group_id:
                log(f"   GROUP FAIL {st}: {str(resp)[:200]}")
                summary.append((row["group"], "group-fail"))
                continue
            group_by_name[gkey] = group_id
            log(f"   group created: id={group_id}")

        # 3. Specification
        skey = row["spec"].strip()
        if skey in spec_by_name:
            spec_id = spec_by_name[skey]
            log(f"   spec exists: id={spec_id}")
        else:
            payload = {
                "name": row["spec"], "status": 1,
                "attributeGroup": [{"attributeGroupId": group_id,
                                    "attributeIds": [{"attributeId": attr_id,
                                                      "isMandatory": 1 if row["mandatory"] else 0}]}],
                "attributeGroupId": [],
            }
            st, resp = api_send("POST", "/product/api/specification", payload, tok)
            spec_id = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
            if st not in (200, 201) or not spec_id:
                log(f"   SPEC FAIL {st}: {str(resp)[:200]}")
                summary.append((row["spec"], "spec-fail"))
                continue
            spec_by_name[skey] = spec_id
            log(f"   spec created: id={spec_id}")

        # 4. Assign to L4 family
        if not row["node_path"]:
            log("   no L4 node_path -- skip assignment")
            summary.append((row["spec"], "ok-no-family"))
            continue
        cat_id = cat_by_path.get(row["node_path"].strip())
        if not cat_id:
            # The sheet uses an L1 alias that differs from the live tree.
            alt = row["node_path"].strip().replace("Books & General Merchandise", "Toys & General Merchandise")
            cat_id = cat_by_path.get(alt)
        if not cat_id:
            log(f"   family not found for path {row['node_path']!r} -- skip assignment")
            summary.append((row["spec"], "ok-family-missing"))
            continue
        payload = {"specification": [{"specificationId": spec_id}], "deleteSpecificationIds": []}
        st, resp = api_send("PUT", f"/product/api/spec-category/{cat_id}", payload, tok)
        if st in (200, 201) and (resp.get("status") in (1, "1", True) or "data" in resp):
            log(f"   assigned spec {spec_id} to family {cat_id}")
            summary.append((row["spec"], "ok"))
        else:
            log(f"   ASSIGN FAIL {st}: {str(resp)[:200]}")
            summary.append((row["spec"], "assign-fail"))

    log("=" * 60)
    ok = sum(1 for _, s in summary if s.startswith("ok"))
    log(f"done: {ok}/{len(summary)} ok")
    for n, s in summary:
        if not s.startswith("ok"):
            log(f"   {s}: {n}")


if __name__ == "__main__":
    main()
