#!/usr/bin/env python3
"""Centralized L2 listing-template store + registry (SQLite).

The export needs one thing: for a given listing category, the latest APPROVED
template workbook. Templates must not be read from someone's Downloads folder,
they must survive restarts, keep their history, and be validated before use.

Layout on the Windows box
-------------------------
    C:\\gajab\\templates\\templates.db        registry (SQLite)
    C:\\gajab\\templates\\files\\<slug>.vN.xlsx managed copies, one per version

Registry row = one VERSION of one template. `active=1` marks the version an
export may use; the newest active version wins. Importing the same file again
is a no-op (identity is the file hash); a changed file becomes version N+1 and
the previous version stays on disk and in the registry.

Where the category comes from
-----------------------------
Each template embeds its own marketplace category, e.g. its "Specification
List" sheet holds `Sports & Fitness > Athletics > Baton`. That is the listing
taxonomy the sheet uploads against (the `Category Name *` dropdown lists
`#144| Sports & Fitness > Athletics > Baton` values). So the template is
registered under the L2 segment of that path (here: `Athletics`), and keeps the
full path plus the marketplace category id when one can be read.

Templates whose category cannot be resolved are still imported - the file is
preserved and its columns readable - but they are registered inactive and
reported, exactly as the spec asks ("report files whose corresponding L2
category cannot be identified reliably").

CLI
---
    python3 _l2_templates.py import --src "C:\\gajab\\L2_Listing_Templates"
    python3 _l2_templates.py import --src ... --dry-run
    python3 _l2_templates.py list [--json]
    python3 _l2_templates.py show --l2 "Athletics"
    python3 _l2_templates.py validate [--all]
    python3 _l2_templates.py deactivate --id 12 | --l2 "Athletics" [--version 2]
    python3 _l2_templates.py activate   --id 13
    python3 _l2_templates.py report --out C:\\gajab\\templates\\import_report.xlsx
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

DB_PATH = Path(os.environ.get("L2_TEMPLATES_DB", r"C:\gajab\templates\templates.db"))
FILE_DIR = Path(os.environ.get("L2_TEMPLATES_DIR", r"C:\gajab\templates\files"))
PRODUCT_SHEET = "Product excel sheet"
# Columns every listing template must offer, by normalised name.
REQUIRED_COLUMNS = ("sku", "categoryname", "productname", "description", "hsn", "tax",
                    "mrp price", "transfer price", "product image1")

SCHEMA = """
create table if not exists templates (
    id                integer primary key autoincrement,
    l2_name           text not null,
    l2_path           text,               -- 'L1 > L2' (the export groups on this)
    l2_id             text,
    category_path     text,               -- marketplace path(s) found in the template
    source_name       text not null,      -- original filename
    slug              text not null,
    version           integer not null,
    file_path         text not null,      -- managed copy
    sha256            text not null,
    size_bytes        integer,
    sheet_name        text,
    column_count      integer,
    spec_category_count integer,
    validation_status text not null,      -- ok | warning | unmapped | invalid
    validation_notes  text,
    active            integer not null default 1,
    imported_at       text not null,
    updated_at        text,
    unique (slug, version)
);
create index if not exists templates_l2 on templates (l2_name, active);
create index if not exists templates_sha on templates (sha256);
"""


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def connect(db: Path = DB_PATH) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # forward-compatible migration for registries created by an older build
    cols = {r[1] for r in conn.execute("pragma table_info(templates)")}
    if "updated_at" not in cols:
        conn.execute("alter table templates add column updated_at text")
    if "l2_path" not in cols:
        conn.execute("alter table templates add column l2_path text")
        # backfill from the stored category path (first two segments)
        for row in conn.execute("select id, l2_name, category_path from templates").fetchall():
            path = (row["category_path"] or "").split("; ")[0]
            parts = [x.strip() for x in path.split(">") if x.strip()]
            conn.execute("update templates set l2_path = ? where id = ?",
                         (" > ".join(parts[:2]) if len(parts) >= 2 else row["l2_name"], row["id"]))
        conn.commit()
    return conn


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "template"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_col(v) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())


def read_template(path: Path) -> dict:
    """Structure of a template workbook, without trusting the filename."""
    wb = load_workbook(path, read_only=True, data_only=True)
    sheet = PRODUCT_SHEET if PRODUCT_SHEET in wb.sheetnames else wb.sheetnames[0]
    ws = wb[sheet]
    it = ws.iter_rows(values_only=True)
    group_row = next(it, []) or []
    header_row = next(it, []) or []
    headers = [str(h).strip() for h in header_row if h not in (None, "")]
    fixed = [h for h in headers if not h.lower().startswith(("specifications",))]
    spec_cols = [h for h in headers if h.lower().startswith("specifications")]

    paths: list[str] = []
    spec_rows = 0
    if "Specification List" in wb.sheetnames:
        for i, r in enumerate(wb["Specification List"].iter_rows(values_only=True)):
            if i == 0 or not r:
                continue
            cat = str(r[0] or "").strip().rstrip("> ").strip()
            if not cat:
                continue
            spec_rows += 1
            paths.append(cat)

    # marketplace category ids look like "#144| Sports & Fitness > Athletics > Baton"
    cat_ids: dict[str, str] = {}
    if "ValidationLists" in wb.sheetnames:
        for r in wb["ValidationLists"].iter_rows(values_only=True):
            for v in r:
                s = str(v or "")
                m = re.match(r"\s*#(\d+)\|\s*(.+)", s)
                if m:
                    cat_ids[m.group(2).strip().rstrip("> ").strip()] = m.group(1)

    l2_names: list[str] = []
    for p in paths:
        parts = [x.strip() for x in p.split(">") if x.strip()]
        if len(parts) >= 2:
            l2_names.append(parts[1])
    return {
        "sheet": sheet,
        "group_row": [str(g).strip() if g else "" for g in group_row],
        "headers": headers,
        "fixed_columns": fixed,
        "spec_columns": spec_cols,
        "paths": sorted(set(paths)),
        "l2_names": l2_names,
        "cat_ids": cat_ids,
        "spec_rows": spec_rows,
        "sheets": wb.sheetnames,
    }


def resolve_category(info: dict, filename: str) -> tuple[str, str, str, str, str]:
    """(l2_name, l2_path, l2_id, category_path, status_note)."""
    names = info["l2_names"]
    if names:
        common = Counter(names)
        l2 = common.most_common(1)[0][0]
        conflict = "" if len(common) == 1 else f"; {len(common)} L2s in template"
        path = "; ".join(info["paths"])
        l2_id = ""
        for p, cid in info["cat_ids"].items():
            parts = [x.strip() for x in p.split(">") if x.strip()]
            if len(parts) >= 2 and parts[1] == l2:
                l2_id = cid
                break
        l2_path = ""
        for p in info["paths"]:
            parts = [x.strip() for x in p.split(">") if x.strip()]
            if len(parts) >= 2 and parts[1] == l2:
                l2_path = " > ".join(parts[:2]); break
        return l2, l2_path, l2_id, path, f"from Specification List{conflict}"

    # no embedded category: fall back to the filename (strip the _NN copy suffix)
    base = re.sub(r"_\d+$", "", Path(filename).stem).strip()
    if base:
        return base, "", "", "", "from filename (no Specification List category)"
    return "", "", "", "", "unidentified"


def validate(info: dict) -> tuple[str, list[str]]:
    notes: list[str] = []
    status = "ok"
    missing = [c for c in REQUIRED_COLUMNS
               if c.replace(" ", "") not in {norm_col(h) for h in info["headers"]}]
    if missing:
        status = "invalid"
        notes.append("missing required columns: " + ", ".join(missing))
    if info["sheet"] != PRODUCT_SHEET:
        status = "warning" if status != "invalid" else status
        notes.append(f"product sheet is {info['sheet']!r}, expected {PRODUCT_SHEET!r}")
    if not info["spec_rows"]:
        # Many marketplace categories define no specifications at all. Such a
        # template is complete for its category (fixed columns only) - flagging it
        # as degraded made 65 healthy templates look broken.
        notes.append("no specifications for this category (fixed columns only)")
    dup = [h for h, n in Counter(info["headers"]).items() if n > 1]
    if dup:
        notes.append("duplicate column names: " + ", ".join(dup[:5]))
    return status, notes


def import_templates(src: Path, db: Path = DB_PATH, store: Path = FILE_DIR,
                     dry_run: bool = False, verbose: bool = True) -> dict:
    if not src.is_dir():
        raise SystemExit(f"not a folder: {src}")
    conn = connect(db)
    created = updated = skipped = failed = 0
    rows: list[dict] = []
    files = sorted(src.glob("*.xls*"))
    for f in files:
        if f.name.startswith("~$"):
            continue
        digest = sha256(f)
        prev = conn.execute(
            "select id, version, l2_name, active from templates where sha256 = ? order by version desc limit 1",
            (digest,),
        ).fetchone()
        if prev:
            skipped += 1
            rows.append({"file": f.name, "l2": prev["l2_name"], "version": prev["version"],
                         "status": "unchanged", "note": "identical file already imported"})
            continue
        try:
            info = read_template(f)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            rows.append({"file": f.name, "l2": "", "version": 0, "status": "invalid",
                         "note": f"unreadable: {type(exc).__name__}: {exc}"})
            continue

        l2, l2_path, l2_id, path, source_note = resolve_category(info, f.name)
        status, notes = validate(info)
        if not l2:
            status = "unmapped"
            notes = notes + [source_note]
        else:
            notes = notes + [source_note]
        if status == "unmapped":
            active = 0
        else:
            active = 1

        slug = slugify(l2 or f.stem)
        last = conn.execute(
            "select coalesce(max(version), 0) v from templates where slug = ?", (slug,)
        ).fetchone()["v"]
        version = last + 1
        target = store / f"{slug}.v{version}.xlsx"

        # A copy must not silently replace a better template: several files map
        # to the same category ("Athletics" and "Athletics_13", where the _13
        # copy has no Specification List). A version only becomes active when it
        # is at least as good as the current one.
        rank = {"ok": 0, "warning": 1, "unmapped": 2, "invalid": 3}
        cur = conn.execute(
            "select validation_status, spec_category_count, column_count, version "
            "from templates where slug = ? and active = 1 order by version desc limit 1",
            (slug,),
        ).fetchone()
        new_rank = rank.get(status, 3)
        if active == 0:
            make_active, keep_note = False, "not activated (category unresolved)"
        elif cur is None:
            make_active, keep_note = True, "activated (first version)"
        elif new_rank < rank.get(cur["validation_status"], 3):
            make_active, keep_note = True, f"activated (better than v{cur['version']})"
        elif new_rank == rank.get(cur["validation_status"], 3) and (
                info["spec_rows"] >= (cur["spec_category_count"] or 0)
                and len(info["headers"]) >= (cur["column_count"] or 0)):
            make_active, keep_note = True, f"activated (v{cur['version']} superseded)"
        else:
            make_active = False
            keep_note = f"kept v{cur['version']} active (same or better template)"

        if dry_run:
            action = "update" if last else "create"
        else:
            store.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            if make_active:
                conn.execute("update templates set active = 0, updated_at = ? where slug = ?",
                             (now(), slug))
            conn.execute(
                """insert into templates
                   (l2_name, l2_path, l2_id, category_path, source_name, slug, version, file_path, sha256,
                    size_bytes, sheet_name, column_count, spec_category_count, validation_status,
                    validation_notes, active, imported_at, updated_at)
                   values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (l2, l2_path or l2, l2_id, path, f.name, slug, version, str(target), digest, f.stat().st_size,
                 info["sheet"], len(info["headers"]), info["spec_rows"], status,
                 "; ".join(notes + [keep_note])[:900], 1 if make_active else 0, now(), now()),
            )
            action = "update" if last else "create"
        if last:
            updated += 1
        else:
            created += 1
        rows.append({"file": f.name, "l2": l2, "version": version,
                     "status": "ok" if status in ("ok", "warning") else status,
                     "note": "; ".join(notes)[:150], "action": action})
    if not dry_run:
        conn.commit()
    if verbose:
        print(f"scanned {len(files)} files: {created} new, {updated} updated, {skipped} unchanged, {failed} failed")
        for r in rows:
            mark = {"ok": "  ", "warning": "! ", "unmapped": "? ", "invalid": "x ",
                    "unchanged": "= "}.get(str(r["status"]), "  ")
            print(f"  {mark}{r['file'][:44]:44} -> {r['l2'][:24]:24} v{r['version']}  {r['note'][:70]}")
    conn.close()
    return {"rows": rows, "created": created, "updated": updated,
            "skipped": skipped, "failed": failed}



TAXONOMY = Path(__file__).with_name("category_l1l4.json")


def _taxonomy_nodes() -> list[str]:
    import json as _json
    try:
        tax = _json.loads(TAXONOMY.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list((tax.get("ids") or {}))


def _depth(path: str) -> int:
    return len([x for x in path.split(">") if x.strip()])


def resolve_taxonomy_node(name: str, nodes: list[str] | None = None) -> str | None:
    """Best node whose last segment equals `name`, preferring one with children."""
    nodes = nodes or _taxonomy_nodes()
    target = re.sub(r"_\d+$", "", (name or "").strip()).lower()
    if not target:
        return None
    cands = [p for p in nodes if [x.strip() for x in p.split(">")][-1].lower() == target]
    best, best_score = None, -1
    for cand in cands:
        kids = [p for p in nodes if p.startswith(cand + " > ")]
        deepest = max((_depth(k) for k in kids), default=0)
        score = deepest * 1000 + len(kids)
        if score > best_score:
            best, best_score = cand, score
    return best


def spec_leaves(node: str, nodes: list[str] | None = None) -> list[str]:
    """Deepest descendants of `node` (L4 when present, else L3)."""
    nodes = nodes or _taxonomy_nodes()
    kids = [p for p in nodes if p.startswith(node + " > ")]
    if not kids:
        return []
    maxd = max(_depth(k) for k in kids)
    # a spec group is a node that has no children of its own
    leaves = [k for k in kids if not any(p.startswith(k + " > ") for p in nodes)]
    return leaves or [k for k in kids if _depth(k) == maxd]


def generate_specs(template: dict, db: Path = DB_PATH, store: Path = FILE_DIR,
                   apply: bool = False) -> dict:
    """Build the missing Specification List + attribute columns for one template.

    Uses our taxonomy + attribute catalog: for each L4 leaf under the template's
    category, one `Specifications - <leaf>` group whose columns are the attributes
    that leaf declares. Writes a NEW VERSION - the original is never modified.
    """
    from _prepare_gajab_export import SpecAttributeMapper, load_catalog

    nodes = _taxonomy_nodes()
    node = resolve_taxonomy_node(template["l2_name"] or template["source_name"], nodes)
    if not node:
        return {"ok": False, "reason": f"cannot resolve {template['l2_name']!r} in the taxonomy"}
    # A template can name a LEAF category ("Suitcases"): then that leaf is the one
    # spec group and its own attributes apply.
    leaves = spec_leaves(node, nodes) or [node]

    mapper = SpecAttributeMapper(load_catalog())
    groups = []
    for leaf in leaves:
        attrs = [a["attribute"] for a in mapper.attrs_for(leaf)]
        if attrs:
            groups.append((leaf, attrs))
    if not groups:
        return {"ok": False, "reason": f"no attributes declared for any leaf under {node!r}"}

    src = Path(template["file_path"])
    if not apply:
        return {"ok": True, "node": node, "specs": len(groups),
                "columns_to_add": sum(len(a) for _, a in groups), "apply": False}

    wb = load_workbook(src)
    sheet = wb[template["sheet_name"]] if template["sheet_name"] in wb.sheetnames else wb.active
    last_col = sheet.max_column
    spec_sheet = wb["Specification List"] if "Specification List" in wb.sheetnames else wb.create_sheet("Specification List")
    if spec_sheet.max_row == 0 or not spec_sheet.cell(row=1, column=1).value:
        spec_sheet.cell(row=1, column=1, value="Categories")
        spec_sheet.cell(row=1, column=2, value="Specifications")
        spec_sheet.cell(row=1, column=3, value="Attributes")
    row = spec_sheet.max_row + 1
    for leaf, attrs in groups:
        spec_sheet.cell(row=row, column=1, value=leaf)
        spec_sheet.cell(row=row, column=2, value=[x.strip() for x in leaf.split(">")][-1])
        spec_sheet.cell(row=row, column=3, value=" | ".join(attrs))
        row += 1
        for attr in attrs:
            last_col += 1
            sheet.cell(row=1, column=last_col, value=f"Specifications - {[x.strip() for x in leaf.split('>')][-1]}")
            sheet.cell(row=2, column=last_col, value=attr)

    store.mkdir(parents=True, exist_ok=True)
    slug = template["slug"]
    version = template["version"] + 1
    target = store / f"{slug}.v{version}.xlsx"
    wb.save(target)

    # register the new version in place (the file already lives in the store)
    info = read_template(target)
    status, notes = validate(info)
    conn = connect(db)
    conn.execute("update templates set active = 0, updated_at = ? where slug = ?", (now(), slug))
    conn.execute(
        """insert into templates
           (l2_name, l2_path, l2_id, category_path, source_name, slug, version, file_path, sha256,
            size_bytes, sheet_name, column_count, spec_category_count, validation_status,
            validation_notes, active, imported_at, updated_at)
           values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (template["l2_name"], template.get("l2_path"), template.get("l2_id"),
         "; ".join(groups and [g[0] for g in groups])[:900],
         template["source_name"], slug, version, str(target), sha256(target),
         target.stat().st_size, info["sheet"], len(info["headers"]), len(groups),
         status, "; ".join(notes + ["specifications generated from the taxonomy"])[:900],
         1, now(), now()),
    )
    conn.commit(); conn.close()
    return {"ok": True, "node": node, "specs": len(groups),
            "columns_to_add": sum(len(a) for _, a in groups),
            "file": str(target), "version": version, "apply": True}


def get_template(l2_name: str, db: Path = DB_PATH) -> dict | None:
    """Latest ACTIVE, validated template. Accepts 'L1 > L2' or a bare L2 name."""
    conn = connect(db)
    row = conn.execute(
        """select * from templates
            where (lower(l2_path) = lower(?) or lower(l2_name) = lower(?))
              and active = 1 and validation_status in ('ok', 'warning')
            order by (l2_path is not null) desc, version desc limit 1""",
        (l2_name, l2_name),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_templates(db: Path = DB_PATH, active_only: bool = False) -> list[dict]:
    conn = connect(db)
    q = "select * from templates"
    if active_only:
        q += " where active = 1"
    q += " order by l2_name, version desc"
    rows = [dict(r) for r in conn.execute(q)]
    conn.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="L2 listing template registry")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import")
    p.add_argument("--src", required=True)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--store", default=str(FILE_DIR))
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.add_argument("--active-only", action="store_true")

    p = sub.add_parser("show")
    p.add_argument("--l2", required=True)

    p = sub.add_parser("activate")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("deactivate")
    p.add_argument("--id", type=int)
    p.add_argument("--l2")
    p.add_argument("--version", type=int)

    p = sub.add_parser("generate-specs")
    p.add_argument("--l2", help="only this template category")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--store", default=str(FILE_DIR))

    p = sub.add_parser("report")
    p.add_argument("--out", default=r"C:\gajab\templates\import_report.xlsx")

    args = ap.parse_args()
    if args.cmd == "import":
        res = import_templates(Path(args.src), Path(args.db), Path(args.store), args.dry_run)
        return 0 if not res["failed"] else 1

    if args.cmd == "list":
        rows = list_templates(getattr(args, "db", DB_PATH), args.active_only)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(f"{len(rows)} template versions")
            for r in rows:
                print(f"  {r['l2_name'][:30]:30} v{r['version']:<3} {r['validation_status']:8} "
                      f"{'active' if r['active'] else 'inactive':8} {r['column_count']:5} cols  {r['source_name'][:34]}")
        return 0

    if args.cmd == "show":
        t = get_template(args.l2)
        print(json.dumps(t, indent=2) if t else f"no active template for {args.l2!r}")
        return 0 if t else 1

    if args.cmd == "activate":
        conn = connect()
        row = conn.execute("select slug from templates where id = ?", (args.id,)).fetchone()
        if not row:
            raise SystemExit(f"no row with id {args.id}")
        conn.execute("update templates set active = 0 where slug = ?", (row["slug"],))
        conn.execute("update templates set active = 1, updated_at = ? where id = ?", (now(), args.id))
        conn.commit(); conn.close()
        print(f"activated id {args.id}")
        return 0

    if args.cmd == "deactivate":
        conn = connect()
        if args.id:
            cur = conn.execute("update templates set active = 0, updated_at = ? where id = ?", (now(), args.id))
        elif args.l2 and args.version:
            cur = conn.execute("update templates set active = 0, updated_at = ? where lower(l2_name)=lower(?) and version = ?",
                               (now(), args.l2, args.version))
        else:
            raise SystemExit("pass --id, or --l2 with --version")
        conn.commit()
        print(f"deactivated {cur.rowcount} row(s)")
        conn.close()
        return 0

    if args.cmd == "generate-specs":
        conn = connect(Path(args.db))
        q = "select * from templates where active = 1 and spec_category_count = 0"
        params: tuple = ()
        if args.l2:
            q += " and (lower(l2_name) = lower(?) or lower(l2_path) = lower(?))"
            params = (args.l2, args.l2)
        rows = [dict(r) for r in conn.execute(q, params)]
        print(f"{len(rows)} template(s) without specifications")
        done = 0
        for t in rows:
            res = generate_specs(t, db=Path(args.db), store=Path(args.store), apply=args.apply)
            if not res.get("ok"):
                print(f"  - {t['l2_name'][:32]:32} {res['reason']}")
                continue
            done += 1
            if not args.apply:
                print(f"  + {t['l2_name'][:32]:32} {res['specs']} specs, {res['columns_to_add']} columns  ({res['node']})")
            else:
                print(f"  + {t['l2_name'][:32]:32} v{res['version']} {res['specs']} specs, {res['columns_to_add']} columns")
        print(f"{done} template(s) {'updated' if args.apply else 'can be enriched'}")
        return 0

    if args.cmd == "report":
        from openpyxl import Workbook
        rows = list_templates()
        wb = Workbook(); ws = wb.active; ws.title = "Template Registry"
        ws.append(["L2 category", "L2 id", "Version", "Status", "Active", "Columns",
                   "Spec rows", "Source file", "Category path", "Notes", "Imported"])
        for r in rows:
            ws.append([r["l2_name"], r["l2_id"], r["version"], r["validation_status"],
                       "yes" if r["active"] else "no", r["column_count"],
                       r["spec_category_count"], r["source_name"], r["category_path"],
                       r["validation_notes"], r["imported_at"]])
        for col, w in zip("ABCDEFGHIJK", (30, 8, 8, 10, 8, 9, 10, 44, 60, 60, 22)):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        wb.save(args.out)
        print(f"report -> {args.out}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
