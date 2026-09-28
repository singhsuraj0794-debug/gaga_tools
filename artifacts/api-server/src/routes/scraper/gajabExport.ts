import ExcelJS from "exceljs";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import fs from "fs";
import os from "os";
import { logger } from "../../lib/logger";

const execFileAsync = promisify(execFile);

/** Python enricher: resolves categories and assigns the attributes that
 *  "Attributes Final With Valid Entries.xlsx" defines for each one. */
const PREPARE_SCRIPT = path.resolve(__dirname, "_prepare_gajab_export.py");

/** Group-header bands of the template's fixed columns (row 1 of
 *  "Product excel sheet"). [fromIndex, toIndex, label] */
const FIXED_GROUPS: [number, number, string][] = [
  [0, 1, "Listing Identity"],
  [2, 12, "Product Identity"],
  [13, 16, "Shipping"],
  [17, 19, "Variantions"],
  [20, 21, "Price Details"],
  [22, 24, "SEO Optimization"],
  [25, 26, "COD"],
  [27, 37, "Images"],
];

interface PrepareResult {
  fixed_columns: string[];
  spec_columns: {
    specification: string;
    attribute: string;
    leaf: string;
    type: string;
    mandatory: boolean;
    valid: string[];
  }[];
  audit_columns: string[];
  rows: Record<string, string>[];
  stats: Record<string, number>;
}

export async function prepareGajabRows(products: unknown[]): Promise<PrepareResult> {
  // The enricher takes a file path; write the batch to a temp file so large
  // sheets do not hit argv/stdio limits.
  const tmp = path.join(
    os.tmpdir(),
    `gajab-export-${Date.now()}-${Math.random().toString(36).slice(2)}.json`,
  );
  fs.writeFileSync(tmp, JSON.stringify({ products }), "utf-8");
  try {
    const { stdout } = await execFileAsync("python3", [PREPARE_SCRIPT, tmp], {
      timeout: 300000,
      maxBuffer: 64 * 1024 * 1024,
    });
    return JSON.parse(stdout);
  } finally {
    try {
      fs.unlinkSync(tmp);
    } catch {
      /* best effort */
    }
  }
}

/**
 * Build the workbook in the Gajab ProductImport template layout:
 *   38 fixed columns (same order/spelling as the template), then one column per
 *   attribute the *present* categories declare — group header
 *   "Specifications - <leaf> | <attribute>", column header "<attribute>".
 *   Rows whose category could not be mapped carry a blank Category Name and an
 *   audit block at the end so they are visibly flagged rather than guessed.
 */
export async function buildGajabWorkbook(
  products: unknown[],
  sheetName = "Product excel sheet",
): Promise<{ workbook: ExcelJS.Workbook; stats: Record<string, number> }> {
  const data = await prepareGajabRows(products);
  const workbook = new ExcelJS.Workbook();
  const ws = workbook.addWorksheet(sheetName);

  const fixed = data.fixed_columns;
  const specs = data.spec_columns;
  const audit = data.audit_columns;

  // ── row 1: group headers ──────────────────────────────────────────────
  const groupRow = ws.getRow(1);
  for (const [from, to, label] of FIXED_GROUPS) {
    groupRow.getCell(from + 1).value = label;
    if (to > from) ws.mergeCells(1, from + 1, 1, to + 1);
  }
  const specStart = fixed.length + 1;
  const auditStart = specStart + specs.length;
  for (let i = 0; i < specs.length; i++) {
    groupRow.getCell(specStart + i).value = `Specifications - ${specs[i].specification}`;
  }
  for (let i = 0; i < audit.length; i++) {
    groupRow.getCell(auditStart + i).value = "Mapping audit";
  }
  if (audit.length > 1) ws.mergeCells(1, auditStart, 1, auditStart + audit.length - 1);

  // ── row 2: column headers ─────────────────────────────────────────────
  const headerRow = ws.getRow(2);
  fixed.forEach((h, i) => (headerRow.getCell(i + 1).value = h));
  specs.forEach((s, i) => (headerRow.getCell(specStart + i).value = s.attribute));
  audit.forEach((h, i) => (headerRow.getCell(auditStart + i).value = h));

  // ── data rows ─────────────────────────────────────────────────────────
  data.rows.forEach((row, r) => {
    const excelRow = ws.getRow(3 + r);
    fixed.forEach((h, i) => (excelRow.getCell(i + 1).value = row[h] ?? ""));
    specs.forEach((s, i) => {
      excelRow.getCell(specStart + i).value = row["spec::" + s.specification] ?? "";
    });
    audit.forEach((h, i) => (excelRow.getCell(auditStart + i).value = row[h] ?? ""));
  });

  // ── styling: header bands, freeze, sensible widths ────────────────────
  const bandColors = ["FFFF9900", "FF28A745", "FF17A2B8", "FF6F42C1", "FFE83E8C", "FFFD7E14", "FF20C997", "FF6610F2"];
  for (const [from, to] of FIXED_GROUPS.map(([a, b]) => [a, b] as [number, number])) {
    for (let c = from; c <= to; c++) {
      const cell = headerRow.getCell(c + 1);
      cell.font = { bold: true, color: { argb: "FFFFFFFF" } };
      cell.fill = {
        type: "pattern",
        pattern: "solid",
        fgColor: { argb: bandColors[c % bandColors.length] },
      };
    }
  }
  for (let i = 0; i < specs.length; i++) {
    const cell = headerRow.getCell(specStart + i);
    cell.font = { bold: true };
    if (specs[i].mandatory) cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFFFF3CD" } };
  }
  for (let i = 0; i < audit.length; i++) {
    headerRow.getCell(auditStart + i).font = { bold: true, italic: true };
  }

  fixed.forEach((h, i) => (ws.getColumn(i + 1).width = Math.max(14, Math.min(50, h.length + 6))));
  specs.forEach((_, i) => (ws.getColumn(specStart + i).width = 20));
  audit.forEach((_, i) => (ws.getColumn(auditStart + i).width = 34));

  ws.views = [{ state: "frozen", ySplit: 2, xSplit: 1 }];
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: 2, column: auditStart + audit.length - 1 } };

  logger.info(
    { products: data.stats.products, specColumns: specs.length, ...data.stats },
    "Gajab export prepared",
  );
  return { workbook, stats: data.stats };
}

/** Shared route body for all three platform exports. */
export async function sendGajabExport(
  products: unknown[],
  filename: string,
  res: any,
): Promise<void> {
  const { workbook } = await buildGajabWorkbook(products);
  res.setHeader(
    "Content-Type",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  );
  res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
  await workbook.xlsx.write(res);
  res.end();
}
