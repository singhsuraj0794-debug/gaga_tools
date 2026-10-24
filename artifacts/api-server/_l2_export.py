#!/usr/bin/env python3
"""L2-based export: one workbook per L2, built from that L2's approved template.

Products are grouped by the L2 of their approved L4 category. Each group is
written into a COPY of the registered template for that L2, so the template's
structure (column order, group headers, validation lists, sibling sheets) is
preserved exactly.

Attributes follow the product's L4, not the L2: a template carries one
`Specifications - <L4 leaf>` group per leaf it covers, and a product only fills
the group belonging to its own L4 - every other group stays blank. That is the
"map at L4, export at L2" rule in the spec.

Category Name * gets the marketplace value the template itself declares in its
ValidationLists sheet (`#144| Sports & Fitness > Athletics > Baton`), which is
what the upload expects - not our internal path.

Usage
-----
    python3 _l2_export.py --job C:\\...\\flipkart_jobs\\abc.json --limit 200
    python3 _l2_export.py --job job.json --out C:\\gajab\\Scraper_Exports\\run1 --platform flipkart
On macOS the registry defaults can be overridden with L2_TEMPLATES_DB / L2_TEMPLATES_DIR.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import json
import re
import shutil
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _l2_templates import DB_PATH, FILE_DIR, get_template  # noqa: E402
from _prepare_gajab_export import (  # noqa: E402
    FIXED_COLUMNS,
    SpecAttributeMapper,
    _platform_of,
    load_catalog,
    prepare,
)

EXPORT_ROOT = Path(r"C:\gajab\Scraper_Exports")
# Audit store for category decisions (spec 3.2: decisions must be stored so they
# can be audited and reused). Reuse happens through the curated map that
# apply_mapping_review.py writes back after a human confirms a row.
DECISIONS_DB = Path(os.environ.get("L2_DECISIONS_DB", r"C:\gajab\templates\decisions.db"))
REQUIRED_FIXED = ("Sku *", "Product  Name *", "Mrp  Price *", "Transfer  Price *",
                  "Product Image 1 *")


def _norm(v) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())


def _safe_name(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "-", name).strip()[:80] or "L2"


def _l2_of(path: str) -> str:
    parts = [p.strip() for p in (path or "").split(">") if p.strip()]
    return " > ".join(parts[:2]) if len(parts) >= 2 else ""


def _leaf_of(path: str) -> str:
    parts = [p.strip() for p in (path or "").split(">") if p.strip()]
    return parts[-1] if parts else ""


def _category_values(wb) -> dict:
    """leaf (lower) -> '#id| full path' from the template's ValidationLists."""
    out: dict[str, str] = {}
    if "ValidationLists" not in wb.sheetnames:
        return out
    for row in wb["ValidationLists"].iter_rows(values_only=True):
        for v in row:
            s = str(v or "").strip()
            m = re.match(r"#(\d+)\|\s*(.+)", s)
            if not m:
                continue
            path = m.group(2).strip().rstrip("> ").strip()
            leaf = _leaf_of(path)
            if leaf:
                out.setdefault(leaf.lower(), f"#{m.group(1)}| {path}")
    return out


def _layout(ws) -> tuple[dict, OrderedDict, set]:
    """(fixed header -> col, {spec leaf lower: {attr lower: col}}, spec leaves)."""
    rows = ws.iter_rows(min_row=1, max_row=2, values_only=True)
    group_row = next(rows, ()) or ()
    header_row = next(rows, ()) or ()
    fixed: dict[str, int] = {}
    groups: dict[str, dict[str, int]] = defaultdict(dict)
    current = ""
    for i, header in enumerate(header_row):
        group = str(group_row[i]).strip() if i < len(group_row) and group_row[i] else current
        current = group
        h = str(header or "").strip()
        if not h:
            continue
        if group.lower().startswith("specifications"):
            spec = re.sub(r"(?i)^specifications\s*-\s*", "", group).strip()
            if spec:
                groups[spec.lower()][_norm(h)] = i
        else:
            fixed.setdefault(_norm(h), i)
    return fixed, groups, set(groups)


def _write_group_rows(rows: list[dict], l2_path: str, template: dict, out_file: Path) -> dict:
    """Copy the template and append one row per product."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template["file_path"], out_file)

    wb = load_workbook(out_file)
    sheet = wb[template["sheet_name"]] if template["sheet_name"] in wb.sheetnames else wb.active
    fixed_cols, spec_groups, spec_leaves = _layout(sheet)
    cat_values = _category_values(wb)

    attr_mapper = SpecAttributeMapper(load_catalog())
    missing_required = Counter()
    attrs_written = 0
    start_row = sheet.max_row + 1 if sheet.max_row >= 2 else 3
    if start_row < 3:
        start_row = 3

    for offset, row in enumerate(rows):
        r = start_row + offset
        path = row.get("Mapped Category Path", "")
        segs = [x.strip() for x in path.split(">") if x.strip()]
        leaf = segs[-1] if segs else ""

        # Category Name *: the deepest category the template actually declares,
        # walking L4 -> L3 -> L2 so a coarser mapping still matches a real value.
        cat_value = ""
        for cand in reversed(segs[1:]):
            cat_value = cat_values.get(cand.lower(), "")
            if cat_value:
                break
        if not cat_value:
            cat_value = path

        # fixed columns, by header name
        for name in FIXED_COLUMNS:
            col = fixed_cols.get(_norm(name))
            if col is None:
                continue
            value = cat_value if name == "Category  Name *" else row.get(name, "")
            sheet.cell(row=r, column=col + 1).value = value

        # Attributes: each spec key is "<leaf> | <attribute>" for the node the
        # mapper resolved (L4, or L3/L2 when the finer level is unknown). Match it
        # against whichever spec group the template has - never cross-fill,
        # because prepare() only emits keys for the resolved node.
        for key, value in row.items():
            if not key.startswith("spec::") or value in (None, ""):
                continue
            spec = key[len("spec::"):]
            attr_leaf, _, attr = spec.partition("|")
            group = spec_groups.get(attr_leaf.strip().lower())
            if not group:
                continue
            col = group.get(_norm(attr))
            if col is None:
                continue
            sheet.cell(row=r, column=col + 1).value = value
            attrs_written += 1

        for name in REQUIRED_FIXED:
            if not str(row.get(name, "") or "").strip():
                missing_required[name] += 1
        for a in attr_mapper.attrs_for(row.get("Mapped Category Path", "")):
            if not a.get("mandatory"):
                continue
            if row.get(f"spec::{a['specification']}") in (None, ""):
                missing_required[f"attr: {a['attribute']}"] += 1

    wb.save(out_file)
    return {
        "file": str(out_file),
        "rows": len(rows),
        "attributes_written": attrs_written,
        "spec_groups_available": len(spec_leaves),
        "missing_required": dict(missing_required.most_common(12)),
    }


def zip_dir(out_dir: Path) -> Path:
    """Bundle the batch (L2 workbooks + Review_Required + export_log) for download."""
    import zipfile
    archive = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(out_dir))
    return archive


def _title_key(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).split())


def log_decisions(rows: list[dict], db: Path = DECISIONS_DB) -> int:
    """Record every product → L4 decision (audit trail, one row per product)."""
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute("""
            create table if not exists category_decisions (
                platform    text,
                source_path text,
                title_key   text,
                l4_path     text,
                method      text,
                confidence  real,
                status      text,
                uses        integer not null default 1,
                first_seen  text,
                last_seen   text,
                primary key (platform, source_path, title_key)
            )""")
        now = dt.datetime.now().isoformat(timespec="seconds")
        n = 0
        for row in rows:
            path = row.get("Mapped Category Path", "")
            if not path:
                continue
            conn.execute(
                """insert into category_decisions
                   (platform, source_path, title_key, l4_path, method, confidence, status,
                    uses, first_seen, last_seen)
                   values (?,?,?,?,?,?,?,1,?,?)
                   on conflict(platform, source_path, title_key) do update set
                     l4_path=excluded.l4_path, method=excluded.method,
                     confidence=excluded.confidence, status=excluded.status,
                     uses=category_decisions.uses + 1, last_seen=excluded.last_seen""",
                (row.get("_platform", ""), row.get("Source Category", ""),
                 _title_key(row.get("Product  Name *", "")), path,
                 row.get("_method", ""), float(row.get("_confidence") or 0),
                 row.get("Mapping Status", ""), now, now),
            )
            n += 1
        conn.commit(); conn.close()
        return n
    except Exception:
        return 0


def export_batch(products: list[dict], out_dir: Path, batch_id: str = "",
                 platform: str = "") -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_id = batch_id or dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Local category agent first (Qwen2.5-14B + EmbeddingGemma). It only overrides
    # a row when it has a validated taxonomy node; otherwise the rule-based
    # mapping stands and a gap is reported.
    rulings, gaps = {}, []
    try:
        from _category_agent import apply_to_prepare
        agent_out = apply_to_prepare(products)
        for p in products:
            r = agent_out.get(id(p))
            if not r:
                continue
            if r.get("path"):
                rulings[id(p)] = r
            elif r.get("gap"):
                gaps.append(r["gap"])
    except Exception as exc:  # noqa: BLE001
        print(f"category agent skipped: {type(exc).__name__}: {exc}", file=sys.stderr)

    data = prepare(products, rulings=rulings)   # category mapping + attributes
    rows = data["rows"]
    logged = log_decisions(rows)

    exportable: dict[str, list[dict]] = OrderedDict()
    review: list[dict] = []
    for row in rows:
        path = row.get("Mapped Category Path", "")
        status = row.get("Mapping Status", "")
        level = int(row.get("_level") or 0)
        l2 = _l2_of(path)
        # L4 is ideal, but a confident L3 (or L2) still belongs in its L2 template:
        # the attributes simply stop at that level. Only an L1-only or unmapped
        # product cannot be placed.
        if not path or not l2 or level < 2 or status == "unmapped":
            review.append({
                "Platform": row.get("_platform", platform),
                "Sku": row.get("Sku *", ""),
                "Product Name": row.get("Product  Name *", ""),
                "Source Category": row.get("Source Category", ""),
                "Mapped Category Path": path,
                "Mapping Status": status or "unmapped",
                "Method": row.get("_method", ""),
                "Confidence": row.get("_confidence", ""),
                "Level": str(level),
                "Reason": "no category" if not path else
                          ("only L1 resolved" if level < 2 else "unmapped"),
                "Suggested Alternatives": " | ".join(row.get("_candidates") or []),
            })
            continue
        exportable.setdefault(l2, []).append(row)

    level_counts = Counter(int(r.get("_level") or 0) for g in exportable.values() for r in g)
    results, missing_templates = [], []
    for l2, group in exportable.items():
        template = get_template(l2)
        if not template:
            missing_templates.append(l2)
            for row in group:
                review.append({
                    "Platform": row.get("_platform", platform),
                    "Sku": row.get("Sku *", ""),
                    "Product Name": row.get("Product  Name *", ""),
                    "Source Category": row.get("Source Category", ""),
                    "Mapped Category Path": row.get("Mapped Category Path", ""),
                    "Mapping Status": "template-missing",
                    "Method": row.get("_method", ""),
                    "Confidence": row.get("_confidence", ""),
                    "Level": str(row.get("_level") or ""),
                    "Reason": f"no active template for {l2}",
                    "Suggested Alternatives": "",
                })
            continue
        leaf = _safe_name(_leaf_of(l2))
        target = out_dir / f"{leaf}.xlsx"
        n = 2
        while target.exists():
            target = out_dir / f"{leaf}_{n}.xlsx"
            n += 1
        info = _write_group_rows(group, l2, template, target)
        info.update({"l2": l2, "template_version": template["version"],
                     "template_source": template["source_name"],
                     "category_id": template.get("l2_id") or ""})
        results.append(info)

    review_dir = out_dir / "Review_Required"
    if gaps:
        review_dir.mkdir(parents=True, exist_ok=True)
        gw = Workbook(); gws = gw.active; gws.title = "Taxonomy Gaps"
        gws.append(["Product", "Source category", "Suggested branch", "Why",
                    "Nearest existing candidates"])
        for g in gaps:
            gws.append([g.get("title"), g.get("source_category"), g.get("suggested_branch"),
                        g.get("why", ""), " | ".join(g.get("shortlist") or [])])
        for col, w in zip("ABCDE", (58, 52, 52, 44, 90)):
            gws.column_dimensions[col].width = w
        gws.freeze_panes = "A2"
        gw.save(review_dir / "Taxonomy_Gaps.xlsx")
    if review:
        review_dir.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "Unmapped Products"
        cols = ["Platform", "Sku", "Product Name", "Source Category", "Mapped Category Path",
                "Mapping Status", "Method", "Confidence", "Level", "Reason",
                "Suggested Alternatives"]
        ws.append(cols)
        for r in review:
            ws.append([r.get(c, "") for c in cols])
        for i, w in enumerate((12, 22, 60, 50, 60, 14, 14, 10, 6, 40, 70), start=1):
            ws.column_dimensions[chr(64 + i)].width = w
        ws.freeze_panes = "A2"
        wb.save(review_dir / "Unmapped_Products.xlsx")

    manifest = {
        "batch_id": batch_id,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "products": len(rows),
        "decisions_logged": logged,
        "agent_rulings": len(rulings),
        "taxonomy_gaps": len(gaps),
        "exported_products": sum(r["rows"] for r in results),
        "review_products": len(review),
        "exported_by_level": {f"L{k}": v for k, v in sorted(level_counts.items(), reverse=True)},
        "files": results,
        "missing_templates": missing_templates,
        "categories": {k: len(v) for k, v in exportable.items()},
    }
    (out_dir / "export_log.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="L2 template-based export")
    ap.add_argument("--job", required=True, help="scraper job json (uses its 'products')")
    ap.add_argument("--out", default="")
    ap.add_argument("--platform", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--zip", action="store_true", help="also write <out>.zip")
    args = ap.parse_args()

    payload = json.loads(Path(args.job).read_text(encoding="utf-8"))
    products = payload.get("products") if isinstance(payload, dict) else payload
    products = products or []
    if args.limit:
        products = products[: args.limit]

    out = Path(args.out) if args.out else EXPORT_ROOT / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest = export_batch(products, out, platform=args.platform)
    print(json.dumps({k: manifest[k] for k in
                      ("batch_id", "products", "exported_products", "review_products",
                       "missing_templates")}, indent=2))
    print(f"output: {out}")
    for f in manifest["files"]:
        print(f"  {Path(f['file']).name:44} {f['rows']:5} rows  attrs={f['attributes_written']:5}  "
              f"template v{f['template_version']} ({f['template_source']})")
    if manifest["review_products"]:
        print(f"  review -> {out / 'Review_Required' / 'Unmapped_Products.xlsx'}")
    if args.zip:
        print(f"zip: {zip_dir(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
