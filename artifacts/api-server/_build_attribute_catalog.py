#!/usr/bin/env python3
"""Build gajab_attributes.json from the master attributes workbook.

Source of truth: "Attributes Final With Valid Entries.xlsx" — sheet
"Attribute Final" (58k+ rows, one row per (node path, attribute)).

Columns used:
  L1, L2, L3, L4, Node_Path, Attribute, AttributeType, Description,
  Attribute Group Name, Attribute Specification, Mandatory,
  Valid Entries (Checkbox/Dropdown)

Output shape:
  {
    "version": "1.0",
    "source": "<file>",
    "by_path": {
        "<Node_Path>": [
            {"attribute": "color", "type": "Checkbox", "mandatory": true,
             "specification": "Vehicle Alloy Wheel | color",
             "leaf": "Vehicle Alloy Wheel",
             "group": "L1 - ... | L4 - ...",
             "description": "You may select...",
             "valid": ["Black", "White", ...]},
            ...
        ]
    },
    "columns": [ {"specification": "...", "attribute": "...", "leaf": "..."} ],
    "attribute_names": {"color": {"type": "Checkbox", "valid": [...]}},
    "stats": {...}
  }

Usage:
    python3 _build_attribute_catalog.py "Attributes Final With Valid Entries.xlsx" gajab_attributes.json
"""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path

import openpyxl

SHEET = "Attribute Final"


def _clean(v) -> str:
    if v is None:
        return ""
    return str(v).replace("\u00a0", " ").strip()


def _split_valid(raw: str) -> list[str]:
    """Valid entries are ';'-separated (sometimes with a 'Values Accepted - ' prefix)."""
    if not raw:
        return []
    parts = []
    for chunk in raw.split(";"):
        v = chunk.strip()
        if not v:
            continue
        # 'Values Accepted - L' means the actual value is the trailing token
        if v.lower().startswith("values accepted - "):
            v = v.split("-", 1)[1].strip()
        if v and v not in parts:
            parts.append(v)
    return parts


def build(xlsx_path: str) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        raise SystemExit(f"sheet {SHEET!r} not found; have {wb.sheetnames}")
    ws = wb[SHEET]

    by_path: "OrderedDict[str, list]" = OrderedDict()
    columns: "OrderedDict[str, dict]" = OrderedDict()
    names: dict[str, dict] = {}
    rows = 0
    skipped = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row:
            continue
        l1, l2, l3, l4 = (_clean(row[i]) for i in range(4))
        node_path = _clean(row[4])
        attribute = _clean(row[5])
        attr_type = _clean(row[6])
        description = _clean(row[7])
        group_name = _clean(row[8])
        specification = _clean(row[9])
        mandatory_raw = _clean(row[10])
        valid_raw = _clean(row[11]) if len(row) > 11 else ""

        if not node_path or not attribute:
            skipped += 1
            continue

        leaf = (specification.split("|")[-1] if "|" in specification else l4) or l4
        mandatory = mandatory_raw.lower().startswith("y")
        entry = {
            "attribute": attribute,
            "type": attr_type,
            "mandatory": mandatory,
            "specification": specification or f"{leaf} | {attribute}",
            "leaf": leaf,
            "group": group_name,
            "description": description,
            "valid": _split_valid(valid_raw),
        }

        bucket = by_path.setdefault(node_path, [])
        if not any(e["attribute"] == attribute for e in bucket):
            bucket.append(entry)

        spec_key = entry["specification"]
        if spec_key not in columns:
            columns[spec_key] = {
                "specification": spec_key,
                "attribute": attribute,
                "leaf": leaf,
                "type": attr_type,
                "valid": entry["valid"],
                "paths": [node_path],
            }
        elif node_path not in columns[spec_key]["paths"]:
            columns[spec_key]["paths"].append(node_path)

        nm = names.setdefault(attribute, {"type": attr_type, "valid": []})
        for v in entry["valid"]:
            if v not in nm["valid"]:
                nm["valid"].append(v)

        rows += 1

    wb.close()

    return {
        "version": "1.0",
        "source": Path(xlsx_path).name,
        "by_path": by_path,
        "columns": list(columns.values()),
        "attribute_names": names,
        "stats": {
            "rows": rows,
            "skipped": skipped,
            "paths": len(by_path),
            "spec_columns": len(columns),
            "attribute_names": len(names),
        },
    }


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage: python3 _build_attribute_catalog.py "
            "\"Attributes Final With Valid Entries.xlsx\" gajab_attributes.json"
        )
    out = build(sys.argv[1])
    dest = Path(sys.argv[2])
    dest.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    s = out["stats"]
    print(f"wrote {dest}")
    print(f"  rows parsed       : {s['rows']}")
    print(f"  rows skipped      : {s['skipped']}")
    print(f"  node paths        : {s['paths']}")
    print(f"  spec columns      : {s['spec_columns']}")
    print(f"  attribute names   : {s['attribute_names']}")


if __name__ == "__main__":
    main()
