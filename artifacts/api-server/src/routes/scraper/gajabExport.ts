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

/** Group-header bands of the template's fixed columns (row 1).
 *  [fromIndex, toIndex, label] */
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

interface SpecColumn {
  specification: string;
  attribute: string;
  leaf?: string;
  type?: string;
  mandatory?: boolean;
  valid?: string[];
}

interface SheetData {
  /** full taxonomy path, "" for unmapped */
  category: string;
  sheet_name: string;
  fixed_columns: string[];
  audit_columns: string[];
  spec_columns: SpecColumn[];
  rows: Record<string, string>[];
}

interface PrepareResult {
  summary: {
    total_products: number;
    total_categories: number;
    categories: { category: string; sheet_name: string; products: number; attribute_columns: number }[];
  };
  sheets: SheetData[];
  fixed_columns: string[];
  spec_columns: SpecColumn[];
  audit_columns: string[];
  rows: Record<string, string>[];
  stats: Record<string, number>;
}

/** Run the Python enricher (or just its category pass) over a batch.
 *  The batch is written to a temp file: large sheets exceed argv/stdio limits. */
async function runEnricher(products: unknown[], extraArgs: string[] = []): Promise<any> {
  const tmp = path.join(
    os.tmpdir(),
    `gajab-export-${Date.now()}-${Math.random().toString(36).slice(2)}.json`,
  );
  fs.writeFileSync(tmp, JSON.stringify({ products }), "utf-8");
  try {
    const { stdout } = await execFileAsync(
      "python3",
      [PREPARE_SCRIPT, tmp, ...extraArgs],
      { timeout: 300000, maxBuffer: 64 * 1024 * 1024 },
    );
    return JSON.parse(stdout);
  } finally {
    try {
      fs.unlinkSync(tmp);
    } catch {
      /* best effort */
    }
  }
}

export async function prepareGajabRows(products: unknown[]): Promise<PrepareResult> {
  return (await runEnricher(products)) as PrepareResult;
}

/** Cheap pre-export pass: how many categories does this scraped file contain,
 *  and how many products/attribute columns each one will get. No attribute
 *  values are computed, so this runs in well under a second. */
export async function summarizeGajabCategories(
  products: unknown[],
): Promise<PrepareResult["summary"]> {
  const out = (await runEnricher(products, ["--summary"])) as PrepareResult["summary"];
  return out;
}

/** Render one category sheet: group headers (row 1), column headers (row 2),
 *  data from row 3 — same layout the ProductImport template uses. */
function renderSheet(
  ws: ExcelJS.Worksheet,
  sheet: { fixed_columns: string[]; spec_columns: SpecColumn[]; audit_columns: string[]; rows: Record<string, string>[] },
): void {
  const fixed = sheet.fixed_columns;
  const specs = sheet.spec_columns;
  const audit = sheet.audit_columns;

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
  specs.forEach((s, i) => (headerRow.getCell(specStart + i).value = s.attribute ?? s.specification));
  audit.forEach((h, i) => (headerRow.getCell(auditStart + i).value = h));

  // ── data rows ─────────────────────────────────────────────────────────
  sheet.rows.forEach((row, r) => {
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
    cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFDDEBF7" } };
    if (specs[i].mandatory)
      cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFFFF3CD" } };
  }
  for (let i = 0; i < audit.length; i++) {
    headerRow.getCell(auditStart + i).font = { bold: true, italic: true };
  }

  fixed.forEach((h, i) => (ws.getColumn(i + 1).width = Math.max(14, Math.min(50, h.length + 6))));
  specs.forEach((_, i) => (ws.getColumn(specStart + i).width = 22));
  audit.forEach((_, i) => (ws.getColumn(auditStart + i).width = 34));

  ws.views = [{ state: "frozen", ySplit: 2, xSplit: 1 }];
  ws.autoFilter = {
    from: { row: 1, column: 1 },
    to: { row: 2, column: auditStart + audit.length - 1 },
  };
}

/** Lead sheet: one line per category with product and attribute-column
 *  counts, so the export opens with an inventory of what it contains. */
function addSummarySheet(
  workbook: ExcelJS.Workbook,
  summary: PrepareResult["summary"],
  stats: Record<string, number>,
): void {
  const ws = workbook.addWorksheet("Category Summary");
  ws.columns = [
    { header: "Category Name *", key: "category", width: 68 },
    { header: "Sheet", key: "sheet", width: 26 },
    { header: "Products", key: "products", width: 12 },
    { header: "Attribute Columns", key: "cols", width: 18 },
  ];
  const header = ws.getRow(1);
  for (let c = 1; c <= 4; c++) {
    const cell = header.getCell(c);
    cell.font = { bold: true, color: { argb: "FFFFFFFF" } };
    cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF28A745" } };
  }
  for (const c of summary.categories) {
    ws.addRow({
      category: c.category,
      sheet: c.sheet_name,
      products: c.products,
      cols: c.attribute_columns,
    });
  }
  const totalRow = ws.addRow({
    category: `TOTAL — ${summary.total_products} products in ${summary.total_categories} categories`,
    sheet: "",
    products: summary.total_products,
    cols: summary.categories.reduce((n, c) => n + c.attribute_columns, 0),
  });
  totalRow.font = { bold: true };
  ws.views = [{ state: "frozen", ySplit: 1 }];
}

/**
 * Build the workbook: a "Category Summary" lead sheet, then ONE WORKSHEET PER
 * CATEGORY (38 fixed template columns + only the attributes that category
 * declares — never the template's 7,000 sample columns). Unmapped products go
 * to an "Unmapped" sheet with a blank Category Name and the audit block.
 */
export async function buildGajabWorkbook(
  products: unknown[],
): Promise<{ workbook: ExcelJS.Workbook; stats: Record<string, number> }> {
  const data = await prepareGajabRows(products);
  const workbook = new ExcelJS.Workbook();

  addSummarySheet(workbook, data.summary, data.stats);

  for (const sheet of data.sheets) {
    const ws = workbook.addWorksheet(sheet.sheet_name);
    renderSheet(ws, sheet);
  }

  logger.info(
    {
      products: data.stats.products,
      sheets: data.sheets.length,
      specColumns: data.spec_columns.length,
      ...data.stats,
    },
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

/** Route body: how many categories a scraped file contains and how each will
 *  be exported (one sheet per category). Cheap — no attribute values. */
export async function sendGajabSummary(products: unknown[], res: any): Promise<void> {
  const summary = await summarizeGajabCategories(products);
  res.json(summary);
}
