#!/usr/bin/env python3
"""
Parse GST_2.0_Rate_Notification_English.docx into a structured HSN dataset.

Output: hsn_entries.json
  [
    {
      "schedule": "Schedule I",
      "central_tax": 2.5,
      "gst_rate": 5.0,
      "coverage": "Essential goods, food staples and agricultural products",
      "hsn_codes": ["0101 21 00", "0101 29"],
      "description": "Live horses"
    }, ...
  ]
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, List

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# table index (after summary) -> schedule metadata
SCHEDULES: List[dict[str, Any]] = [
    {"schedule": "Schedule I", "central_tax": 2.5, "gst_rate": 5.0,
     "coverage": "Essential goods, food staples and agricultural products"},
    {"schedule": "Schedule II", "central_tax": 9.0, "gst_rate": 18.0,
     "coverage": "General / standard-rate goods (the largest schedule)"},
    {"schedule": "Schedule III", "central_tax": 20.0, "gst_rate": 40.0,
     "coverage": 'Luxury and "sin-adjacent" goods (large vehicles, aerated drinks, firearms, betting/gambling, etc.)'},
    {"schedule": "Schedule IV", "central_tax": 1.5, "gst_rate": 3.0,
     "coverage": "Precious metals, jewellery and coin"},
    {"schedule": "Schedule V", "central_tax": 0.125, "gst_rate": 0.25,
     "coverage": "Rough diamonds and precious stones"},
    {"schedule": "Schedule VI", "central_tax": 0.75, "gst_rate": 1.5,
     "coverage": "Other diamonds and precious/semi-precious stones"},
    {"schedule": "Schedule VII", "central_tax": 14.0, "gst_rate": 28.0,
     "coverage": "Pan masala and tobacco products"},
]


def parse_tables(docx_path: str) -> List[List[List[str]]]:
    if docx_path.lower().endswith(".docx"):
        with zipfile.ZipFile(docx_path) as zf:
            xml_data = zf.read("word/document.xml")
    else:
        with open(docx_path, "rb") as f:
            xml_data = f.read()
    root = ET.fromstring(xml_data)
    tables: List[List[List[str]]] = []
    for t in root.iter(W + "tbl"):
        rows: List[List[str]] = []
        for tr in t.iter(W + "tr"):
            cells: List[str] = []
            for tc in tr.iter(W + "tc"):
                cells.append("".join(x.text or "" for x in tc.iter(W + "t")).strip())
            rows.append(cells)
        tables.append(rows)
    return tables


def parse_hsn_codes(raw: str) -> List[str]:
    codes = []
    for chunk in re.split(r"[,\s]+", raw.strip()):
        chunk = chunk.strip()
        if chunk and re.match(r"^\d{2,4}(\.\d{1,2})?$", chunk):
            codes.append(chunk)
    return codes


def build_dataset(docx_path: str) -> List[dict[str, Any]]:
    tables = parse_tables(docx_path)
    entries: List[dict[str, Any]] = []
    for table_idx, meta in enumerate(SCHEDULES):
        table = tables[table_idx + 1]  # skip summary table (index 0)
        for row in table[1:]:  # skip header
            if len(row) < 3:
                continue
            hsn_raw, desc = row[1], row[2]
            if not desc:
                continue
            entries.append({
                "schedule": meta["schedule"],
                "central_tax": meta["central_tax"],
                "gst_rate": meta["gst_rate"],
                "coverage": meta["coverage"],
                "hsn_codes": parse_hsn_codes(hsn_raw),
                "description": desc,
            })
    return entries


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: parse_hsn.py <docx_path> [out_json]")
        sys.exit(1)
    docx_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "hsn_entries.json"

    entries = build_dataset(docx_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_sched = Counter(e["schedule"] for e in entries)
    print(f"Total entries: {len(entries)}")
    for sched, count in sorted(by_sched.items()):
        print(f"  {sched}: {count}")


if __name__ == "__main__":
    main()
