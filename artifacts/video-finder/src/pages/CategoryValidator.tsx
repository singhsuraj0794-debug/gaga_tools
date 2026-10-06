import { useState, useMemo, useRef } from "react";
import { Link } from "wouter";
import * as XLSX from "xlsx";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Upload,
  FileSpreadsheet,
  ArrowLeft,
  Play,
  Loader2,
  Download,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ImageOff,
  Layers,
} from "lucide-react";
import {
  parseFile,
  runCategoryValidation,
  startCategoryJob,
  getCategoryJob,
  getPrelistingApiBase,
  setPrelistingApiBase,
  type ListingRow,
  type CategoryValidateProduct,
  type CategoryValidateResult,
  type CategoryValidateResponse,
  type CategoryJobProgress,
} from "@/lib/listingValidator";

type Phase = "upload" | "running" | "review";

const NAME_ALIASES = ["Product Name", "Product Title", "Title", "Name"];
const SKU_ALIASES = ["Sku Number", "SKU Number", "SKU", "Sku", "Sku *"];
const URL_ALIASES = ["Product URL", "Product Url", "Product Link", "URL", "Url"];
const L1_ALIASES = ["L1", "L1 Name"];
const L2_ALIASES = ["L2", "L2 Name"];
const L3_ALIASES = ["L3", "L3 Name"];
const L4_ALIASES = ["L4", "L4 Name"];
const FLOW_ALIASES = ["Mapped Flow", "Mapped Category", "Category Path"];

// Default rows per range (one incremental chunk).
const CHUNK_DEFAULT = 500;
const RANGE_STORAGE_KEY = "catval:lastRange";

// ── "All Categories.xlsx" import format ──────────────────────────────────────
// Assigned categories that are NOT in the DB are exported in the exact column
// layout of the source-of-truth workbook so the output can be handed to the
// catalogue team (or merged straight in). Category ID / Sort Order are left
// blank — they are assigned when the category is created.
const ALLCAT_HEADERS = [
  "Category ID", "L1", "L2", "L3", "L4", "Full Path",
  "Level", "Sort Order", "Status", "Commission", "Bargain %", "Slug",
];
// Dominant Commission / Bargain % of each L1's Active rows (from All Categories.xlsx).
const L1_DEFAULTS: Record<string, [string, string]> = {
  "Arts & Crafts": ["25.00", "20.00"],
  "Automobile Accessories": ["25.00", "20.00"],
  "Beauty & Health Care": ["30.00", "25.00"],
  "Electronics": ["27.00", "22.00"],
  "Fashion Accessories": ["25.00", "20.00"],
  "Furniture": ["25.00", "20.00"],
  "Gifting & Festive Essentials": ["25.00", "20.00"],
  "Grocery": ["30.00", "25.00"],
  "Home & Kitchen": ["30.00", "25.00"],
  "Kids & Baby": ["30.00", "25.00"],
  "Luggage & Bags": ["27.00", "22.00"],
  "Sporting Goods": ["30.00", "25.00"],
  "Stationery": ["27.00", "22.00"],
  "Toys & Games": ["30.00", "25.00"],
  "Toys & General Merchandise": ["30.00", "25.00"],
};
const L1_DEFAULT_FALLBACK: [string, string] = ["25.00", "20.00"];

// Matches the source workbook's slug convention exactly:
//   "Arts & Crafts"            -> "arts-crafts"
//   "Car A/C Belt"             -> "car-ac-belt"        (punctuation inside a token is removed, not hyphenated)
//   "Mangalsutra/Tanmaniya"    -> "mangalsutratanmaniya"
//   "Computer - Accessories"   -> "computer-accessories" (stray "-" tokens dropped)
//   "Anti-snore Wristbands"    -> "anti-snore-wristbands" (existing hyphens preserved)
function slugify(s: string): string {
  return s
    .toLowerCase()
    .split(/\s+/)
    .map((t) => t.replace(/[^a-z0-9-]/g, "").replace(/^-+|-+$/g, ""))
    .filter(Boolean)
    .join("-");
}

type SavedRange = { fileName: string; to: number };

function loadSavedRange(): SavedRange | null {
  try {
    const raw = localStorage.getItem(RANGE_STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (v && typeof v.fileName === "string" && typeof v.to === "number") return v;
  } catch {
    /* ignore corrupt storage */
  }
  return null;
}

function saveRange(fileName: string, to: number) {
  try {
    localStorage.setItem(RANGE_STORAGE_KEY, JSON.stringify({ fileName, to } satisfies SavedRange));
  } catch {
    /* quota / private mode — resume hint is best-effort */
  }
}

function findCol(headers: string[], aliases: string[]): string | null {
  const lower = headers.map((h) => h.trim().toLowerCase());
  for (const a of aliases) {
    const idx = lower.indexOf(a.toLowerCase());
    if (idx >= 0) return headers[idx];
  }
  return null;
}

function cell(row: ListingRow, col: string | null): string {
  if (!col) return "";
  const v = row[col];
  return v == null ? "" : String(v).trim();
}

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function productKey(p: CategoryValidateProduct): string {
  return [p.sku, p.url, p.l1, p.l2, p.l3, p.l4].join("|");
}

const STATUS_META: Record<string, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  correct: { label: "Correct", cls: "bg-green-100 text-green-700 border-green-300", Icon: CheckCircle2 },
  incorrect: { label: "Incorrect", cls: "bg-red-100 text-red-700 border-red-300", Icon: XCircle },
  review: { label: "Review", cls: "bg-blue-100 text-blue-700 border-blue-300", Icon: AlertTriangle },
  "not-in-db": { label: "Review", cls: "bg-blue-100 text-blue-700 border-blue-300", Icon: AlertTriangle },
  "no-images": { label: "No images", cls: "bg-slate-100 text-slate-600 border-slate-300", Icon: ImageOff },
  unclassified: { label: "Unclassified", cls: "bg-amber-100 text-amber-700 border-amber-300", Icon: AlertTriangle },
  error: { label: "Error", cls: "bg-red-100 text-red-700 border-red-300", Icon: XCircle },
};

function StatusBadge({ status }: { status?: string }) {
  const meta = STATUS_META[status || ""] || STATUS_META.unclassified;
  const Icon = meta.Icon;
  return (
    <Badge variant="outline" className={`${meta.cls} gap-1`}>
      <Icon className="h-3 w-3" />
      {meta.label}
    </Badge>
  );
}

export default function CategoryValidator() {
  const [phase, setPhase] = useState<Phase>("upload");
  const [fileName, setFileName] = useState("");
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<ListingRow[]>([]);
  const [products, setProducts] = useState<CategoryValidateProduct[]>([]);
  const [rowToProduct, setRowToProduct] = useState<number[]>([]);
  const [results, setResults] = useState<CategoryValidateResult[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [batchSize, setBatchSize] = useState(8);
  const [apiUrl, setApiUrl] = useState(getPrelistingApiBase());

  // Row range — validate a 30k-row sheet incrementally.
  // rangeFrom/rangeTo are 1-based inclusive sheet rows; every run operates on
  // rangeRows only. The last completed range is persisted per file name so
  // re-uploading the same sheet resumes where the previous chunk ended.
  const [rangeFrom, setRangeFrom] = useState(1);
  const [rangeTo, setRangeTo] = useState(CHUNK_DEFAULT);
  const [chunkRows, setChunkRows] = useState(CHUNK_DEFAULT);
  const [lastEnd, setLastEnd] = useState(0);
  // The range the CURRENT results belong to — pinned at run start so editing
  // the selector afterwards can't misalign results/export with rowToProduct.
  const [ranRange, setRanRange] = useState<{ from: number; to: number } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const cols = useMemo(
    () => ({
      name: findCol(headers, NAME_ALIASES),
      sku: findCol(headers, SKU_ALIASES),
      url: findCol(headers, URL_ALIASES),
      l1: findCol(headers, L1_ALIASES),
      l2: findCol(headers, L2_ALIASES),
      l3: findCol(headers, L3_ALIASES),
      l4: findCol(headers, L4_ALIASES),
      flow: findCol(headers, FLOW_ALIASES),
    }),
    [headers],
  );

  const missingRequired = useMemo(() => {
    const miss: string[] = [];
    if (!cols.name && !cols.sku) miss.push("Product Name / Sku Number");
    if (!cols.url) miss.push("Product URL");
    if (!cols.l1 || !cols.l2 || !cols.l3 || !cols.l4) miss.push("L1 / L2 / L3 / L4");
    return miss;
  }, [cols]);

  const rangeInvalid =
    rows.length > 0 &&
    (rangeFrom < 1 || rangeTo < rangeFrom || rangeTo > rows.length || rangeFrom > rows.length);

  const rangeRows = useMemo(() => {
    if (rows.length === 0) return [];
    const a = Math.max(0, Math.min(rangeFrom, rows.length) - 1);
    const b = Math.max(a, Math.min(Math.max(rangeTo, 1), rows.length));
    return rows.slice(a, b);
  }, [rows, rangeFrom, rangeTo]);

  const rangeLabel =
    rows.length > 0 && !rangeInvalid
      ? `rows ${rangeFrom}–${rangeTo} of ${rows.length} (${rangeRows.length} rows · ${Math.round(
          (rangeTo / rows.length) * 100,
        )}% of sheet)`
      : rangeInvalid
        ? `invalid range (sheet has ${rows.length} rows)`
        : "";

  // Label for what the displayed results/export actually cover.
  const ranLabel = ranRange
    ? `rows ${ranRange.from}–${ranRange.to} of ${rows.length}`
    : "";

  function ranRows(): ListingRow[] {
    if (!ranRange) return rangeRows;
    const a = Math.max(0, Math.min(ranRange.from, rows.length) - 1);
    const b = Math.max(a, Math.min(ranRange.to, rows.length));
    return rows.slice(a, b);
  }

  function applyFirstRange() {
    const from = 1;
    setRangeFrom(from);
    setRangeTo(Math.min(from + chunkRows - 1, rows.length));
  }

  function applyNextRange() {
    const from = Math.min(Math.max(rangeTo + 1, 1), rows.length);
    setRangeFrom(from);
    setRangeTo(Math.min(from + chunkRows - 1, rows.length));
  }

  function applyAllRange() {
    setRangeFrom(1);
    setRangeTo(rows.length);
  }

  function commitRange(nextFrom: number, nextTo: number) {
    // Clamp to the sheet and keep from <= to while typing.
    const total = rows.length;
    const f = Math.max(1, Math.min(nextFrom || 1, total || 1));
    const t = Math.max(f, Math.min(nextTo || f, total || 1));
    setRangeFrom(f);
    setRangeTo(t);
  }

  const stats = useMemo(() => {
    const s = { correct: 0, incorrect: 0, review: 0, noImages: 0, other: 0 };
    for (const r of results) {
      if (r.status === "correct") s.correct++;
      else if (r.status === "incorrect") s.incorrect++;
      else if (r.status === "review") s.review++;
      else if (r.status === "no-images") s.noImages++;
      else s.other++;
    }
    return s;
  }, [results]);

  // Unique assigned category paths that are absent from the DB (these are the
  // rows "Export Categories" writes out).
  const missingCategories = useMemo(() => {
    const seen = new Set<string>();
    products.forEach((p, i) => {
      const r = results[i];
      if (!r || r.assignedInDb !== false) return;
      const parts = [r.assigned?.l1 ?? p.l1, r.assigned?.l2 ?? p.l2, r.assigned?.l3 ?? p.l3, r.assigned?.l4 ?? p.l4]
        .map((x) => (x || "").trim())
        .filter(Boolean);
      if (parts.length) seen.add(parts.join(" > ").toLowerCase());
    });
    return seen.size;
  }, [products, results]);

  // Sheet rows in the validated range verdicted "incorrect" — what
  // "Export incorrect" writes out (counted over rows, not deduped products).
  const incorrectRowCount = useMemo(() => {
    if (rows.length === 0) return 0;
    let n = 0;
    ranRows().forEach((row, ri) => {
      if (results[rowToProduct[ri]]?.status === "incorrect") n++;
    });
    return n;
  }, [rows, results, rowToProduct, ranRange]);

  async function handleFile(selected: File) {
    setError(null);
    try {
      const parsed = await parseFile(selected);
      if (parsed.rowCount === 0) {
        setError("No data rows found in the file.");
        return;
      }
      setFileName(parsed.fileName);
      setHeaders(parsed.headers);
      setRows(parsed.rows);
      setResults([]);
      setRanRange(null);
      setLogs([]);
      setProgress(0);
      setPhase("upload");

      // Resume the range from the previous run on the same file, else start
      // with the first chunk.
      const saved = loadSavedRange();
      let from = 1;
      let end = 0;
      if (saved && saved.fileName === parsed.fileName && saved.to > 0 && saved.to < parsed.rowCount) {
        from = saved.to + 1;
        end = saved.to;
      }
      setLastEnd(end);
      setRangeFrom(from);
      setRangeTo(Math.min(from + chunkRows - 1, parsed.rowCount));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to parse file");
    }
  }

  async function start() {
    if (missingRequired.length > 0) {
      setError(`Missing columns: ${missingRequired.join(", ")}`);
      return;
    }
    if (rangeInvalid || rangeRows.length === 0) {
      setError(`Invalid row range — sheet has ${rows.length} rows.`);
      return;
    }
    setError(null);
    setPrelistingApiBase(apiUrl);

    const built: CategoryValidateProduct[] = [];
    const map: number[] = [];
    const index = new Map<string, number>();
    for (const row of rangeRows) {
      const p: CategoryValidateProduct = {
        sku: cell(row, cols.sku),
        title: cell(row, cols.name),
        url: cell(row, cols.url),
        l1: cell(row, cols.l1),
        l2: cell(row, cols.l2),
        l3: cell(row, cols.l3),
        l4: cell(row, cols.l4),
      };
      const key = productKey(p);
      let idx = index.get(key);
      if (idx == null) {
        idx = built.length;
        built.push(p);
        index.set(key, idx);
      }
      map.push(idx);
    }

    setProducts(built);
    setRowToProduct(map);
    setResults(new Array(built.length).fill(null).map(() => ({ sku: "" })));
    setRanRange({ from: rangeFrom, to: rangeTo });
    setPhase("running");
    setLogs([
      `Starting: ${rangeLabel} · ${built.length} unique products · batch ${batchSize}`,
    ]);

    try {
      // Assign each unique product a stable index up-front (avoid O(n^2) indexOf).
      const productIndex = new Map<string, number>();
      built.forEach((p, i) => productIndex.set(productKey(p), i));

      const batches = chunk(built, batchSize);
      const acc: CategoryValidateResult[] = new Array(built.length).fill(null).map(() => ({ sku: "" }));
      let done = 0;
      for (let b = 0; b < batches.length; b++) {
        const batch = batches[b];
        try {
          const resp = await runCategoryValidation(batch, apiUrl);
          const res = resp.results || [];
          batch.forEach((p, i) => {
            const r = res[i] || { sku: p.sku, status: "error" };
            const idx = productIndex.get(productKey(p));
            if (idx != null) acc[idx] = r;
          });
          const incorrect = res.filter((r) => r.status === "incorrect").length;
          appendLog(
            `Batch ${b + 1}/${batches.length} · ${batch.length} products · ${incorrect} incorrect` +
              (resp.taxonomyVersion ? ` · taxonomy v${resp.taxonomyVersion}` : ""),
          );
        } catch (e) {
          appendLog(`Batch ${b + 1}/${batches.length} failed: ${e instanceof Error ? e.message : String(e)}`);
          batch.forEach((p) => {
            const idx = productIndex.get(productKey(p));
            if (idx != null) acc[idx] = { sku: p.sku, status: "error", error: String(e) };
          });
        }
        done += batch.length;
        setProgress(Math.round((done / built.length) * 100));
        setResults([...acc]);
      }
      appendLog("Complete.");
    } catch (e) {
      appendLog(`Run failed: ${e instanceof Error ? e.message : String(e)}`);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      saveRange(fileName, rangeTo);
      setLastEnd(rangeTo);
      setPhase("review");
    }
  }

  async function startJob() {
    if (missingRequired.length > 0) {
      setError(`Missing columns: ${missingRequired.join(", ")}`);
      return;
    }
    if (rangeInvalid || rangeRows.length === 0) {
      setError(`Invalid row range — sheet has ${rows.length} rows.`);
      return;
    }
    setError(null);
    setPrelistingApiBase(apiUrl);

    const built: CategoryValidateProduct[] = [];
    const map: number[] = [];
    const index = new Map<string, number>();
    for (const row of rangeRows) {
      const p: CategoryValidateProduct = {
        sku: cell(row, cols.sku),
        title: cell(row, cols.name),
        url: cell(row, cols.url),
        l1: cell(row, cols.l1),
        l2: cell(row, cols.l2),
        l3: cell(row, cols.l3),
        l4: cell(row, cols.l4),
      };
      const key = productKey(p);
      let idx = index.get(key);
      if (idx == null) {
        idx = built.length;
        built.push(p);
        index.set(key, idx);
      }
      map.push(idx);
    }

    setProducts(built);
    setRowToProduct(map);
    setResults(new Array(built.length).fill(null).map(() => ({ sku: "" })));
    setRanRange({ from: rangeFrom, to: rangeTo });
    setPhase("running");
    setProgress(0);
    setLogs([`Background job: ${rangeLabel} · ${built.length} unique products`]);

    try {
      const { jobId } = await startCategoryJob(built, apiUrl);
      appendLog(`Job ${jobId} started`);
      // Poll progress + results until done.
      for (;;) {
        await new Promise((r) => setTimeout(r, 3000));
        const prog = (await getCategoryJob(jobId, "progress", apiUrl)) as CategoryJobProgress;
        const pct = prog.total > 0 ? Math.round((prog.done / prog.total) * 100) : 0;
        setProgress(pct);
        const resultsResp = (await getCategoryJob(jobId, "results", apiUrl)) as CategoryValidateResponse;
        if (resultsResp.results) {
          const acc: CategoryValidateResult[] = new Array(built.length).fill(null).map(() => ({ sku: "" }));
          resultsResp.results.forEach((r, i) => {
            if (i < acc.length) acc[i] = r;
          });
          setResults([...acc]);
        }
        appendLog(`${prog.phase} ${prog.done}/${prog.total}${prog.message ? " · " + prog.message : ""}`);
        if (prog.status === "completed" || prog.status === "failed") {
          appendLog(prog.status === "completed" ? "Job complete." : `Job failed: ${prog.message}`);
          break;
        }
      }
    } catch (e) {
      appendLog(`Job failed: ${e instanceof Error ? e.message : String(e)}`);
      setError(e instanceof Error ? e.message : String(e));
    }
    saveRange(fileName, rangeTo);
    setLastEnd(rangeTo);
    setProgress(100);
    setPhase("review");
  }

  function appendLog(line: string) {
    const ts = new Date().toLocaleTimeString();
    setLogs((prev) => {
      const next = [...prev, `[${ts}] ${line}`];
      setTimeout(() => {
        if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
      }, 0);
      return next;
    });
  }

  function exportSheet() {
    const extra = ["Category Verdict", "Category Confidence", "Recommended Category", "Qwen Verified", "Assigned In DB", "Differing Level"];
    const outHeaders = [...headers, ...extra.filter((c) => !headers.includes(c))];
    const aoa: unknown[][] = [outHeaders];

    // Only the validated range — unvalidated rows would export with empty
    // verdicts. Row order matches the sheet so chunks can be re-merged.
    ranRows().forEach((row, ri) => {
      const res = results[rowToProduct[ri]];
      const rec = res?.recommended ? res.recommended.split(">").map((s) => s.trim()) : null;
      const vals = headers.map((h) => {
        if (res && res.status !== "correct" && res.status !== "no-images" && rec) {
          if (h === cols.l1) return rec[0] ?? "";
          if (h === cols.l2) return rec[1] ?? "";
          if (h === cols.l3) return rec[2] ?? "";
          if (h === cols.l4) return rec[3] ?? "";
          if (cols.flow && h === cols.flow) return res.recommended;
        }
        return row[h] ?? "";
      });
      vals.push(
        res?.status ?? "",
        res?.confidence != null ? `${Math.round(res.confidence * 100)}%` : "",
        res?.recommended ?? "",
        res?.qwenVerified ? "Yes" : "No",
        res?.assignedInDb ? "Yes" : "No",
        res?.differingLevel ?? "",
      );
      aoa.push(vals);
    });

    const ws = XLSX.utils.aoa_to_sheet(aoa);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Category Validation");
    const stamp = new Date().toISOString().slice(0, 10);
    const rangeTag = ranRange ? `-rows${ranRange.from}-${ranRange.to}` : "";
    XLSX.writeFile(wb, `category-validation${rangeTag}-${stamp}.xlsx`);
  }

  // Export ONLY the rows verdicted "incorrect", with every original column and
  // value untouched (no L1–L4 replacement) so the file can be re-uploaded and
  // re-run as-is to re-check those verdicts. Verdict columns are appended after
  // the originals for context and are ignored on re-parse.
  function exportIncorrect() {
    const extra = ["Category Verdict", "Category Confidence", "Recommended Category", "Assigned In DB", "Differing Level"];
    const extraCols = extra.filter((c) => !headers.includes(c));
    const outHeaders = [...headers, ...extraCols];
    const aoa: unknown[][] = [outHeaders];

    ranRows().forEach((row, ri) => {
      const res = results[rowToProduct[ri]];
      if (res?.status !== "incorrect") return;
      const verdictVals: Record<string, unknown> = {
        "Category Verdict": res.status,
        "Category Confidence": res.confidence != null ? `${Math.round(res.confidence * 100)}%` : "",
        "Recommended Category": res.recommended ?? "",
        "Assigned In DB": res.assignedInDb ? "Yes" : "No",
        "Differing Level": res.differingLevel ?? "",
      };
      aoa.push([...headers.map((h) => row[h] ?? ""), ...extraCols.map((c) => verdictVals[c] ?? "")]);
    });

    if (aoa.length <= 1) {
      setError("No incorrect rows in the validated range.");
      return;
    }
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Incorrect");
    const stamp = new Date().toISOString().slice(0, 10);
    const rangeTag = ranRange ? `-rows${ranRange.from}-${ranRange.to}` : "";
    XLSX.writeFile(wb, `category-incorrect${rangeTag}-${stamp}.xlsx`);
  }

  // Export the assigned categories that don't exist in the DB, in the exact
  // "All Categories.xlsx" column format (sheet "Categories"). Deduped by path.
  function exportMissingCategories() {
    const seen = new Set<string>();
    const aoa: unknown[][] = [ALLCAT_HEADERS];

    products.forEach((p, i) => {
      const r = results[i];
      if (!r || r.assignedInDb !== false) return;
      const parts = [r.assigned?.l1 ?? p.l1, r.assigned?.l2 ?? p.l2, r.assigned?.l3 ?? p.l3, r.assigned?.l4 ?? p.l4].map(
        (x) => (x || "").trim(),
      );
      const nonEmpty = parts.filter(Boolean);
      if (nonEmpty.length === 0) return;
      const full = (r.assigned?.full || "").trim() || nonEmpty.join(" > ");
      const dedupeKey = full.toLowerCase();
      if (seen.has(dedupeKey)) return;
      seen.add(dedupeKey);

      const level = nonEmpty.length; // 1..4
      const l1 = parts[0];
      const deepest = nonEmpty[nonEmpty.length - 1];
      const [commission, bargain] = L1_DEFAULTS[l1] || L1_DEFAULT_FALLBACK;

      aoa.push([
        "", // Category ID — assigned when the category is created
        parts[0],
        parts[1],
        parts[2],
        parts[3],
        full,
        level,
        "", // Sort Order — assigned when the category is created
        "Active",
        commission,
        bargain,
        slugify(deepest),
      ]);
    });

    if (aoa.length <= 1) {
      setError("No categories missing from the DB in the validated range.");
      return;
    }
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    const wb2 = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb2, ws, "Categories");
    XLSX.writeFile(wb2, "All_category_sheet.xlsx");
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-7xl mx-auto px-6 py-10">
        <div className="flex items-center gap-3 mb-8">
          <Link href="/">
            <Button variant="outline" size="sm" className="gap-1">
              <ArrowLeft className="h-4 w-4" /> Home
            </Button>
          </Link>
          <div className="p-2 bg-gradient-to-br from-violet-500 to-purple-600 rounded-lg">
            <Layers className="h-6 w-6 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
              Category Validation
              <Badge variant="outline" className="bg-amber-100 text-amber-700 border-amber-300 text-xs">
                R&D Prototype
              </Badge>
            </h1>
            <p className="text-slate-500 text-sm">
              Validate assigned L1–L4 categories with Marqo title+image embeddings against the L1–L4 taxonomy.
            </p>
          </div>
        </div>

        <Card className="mb-6">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">1. Upload sheet</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center gap-4">
              <Button asChild variant="outline" className="gap-2">
                <label className="cursor-pointer">
                  <Upload className="h-4 w-4" />
                  Choose .xlsx / .csv
                  <input
                    type="file"
                    accept=".xlsx,.xls,.csv"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void handleFile(f);
                    }}
                  />
                </label>
              </Button>
              {fileName && (
                <span className="flex items-center gap-2 text-sm text-slate-600">
                  <FileSpreadsheet className="h-4 w-4 text-green-600" />
                  {fileName} · {rows.length} rows · {headers.length} columns
                </span>
              )}
            </div>

            <div className="mt-4 flex flex-wrap items-end gap-4">
              <div>
                <div className="text-xs text-slate-500 mb-1">Compute API URL</div>
                <input
                  value={apiUrl}
                  onChange={(e) => setApiUrl(e.target.value)}
                  onBlur={() => setPrelistingApiBase(apiUrl)}
                  className="w-[420px] max-w-full text-xs border rounded px-2 py-1.5 font-mono"
                />
              </div>
              <div>
                <div className="text-xs text-slate-500 mb-1">Batch size</div>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={batchSize}
                  onChange={(e) => setBatchSize(Math.max(1, Number(e.target.value) || 1))}
                  className="w-20 text-xs border rounded px-2 py-1.5"
                />
              </div>
            </div>

            {rows.length > 0 && (
              <div className="mt-4 p-3 rounded border bg-slate-50">
                <div className="flex flex-wrap items-end gap-3">
                  <div>
                    <div className="text-xs text-slate-500 mb-1">Row range (inclusive)</div>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min={1}
                        max={rows.length}
                        value={rangeFrom}
                        onChange={(e) => commitRange(Number(e.target.value), rangeTo)}
                        className="w-24 text-xs border rounded px-2 py-1.5"
                        title="First sheet row (1-based)"
                      />
                      <span className="text-slate-400">–</span>
                      <input
                        type="number"
                        min={1}
                        max={rows.length}
                        value={rangeTo}
                        onChange={(e) => commitRange(rangeFrom, Number(e.target.value))}
                        className="w-24 text-xs border rounded px-2 py-1.5"
                        title="Last sheet row (inclusive)"
                      />
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500 mb-1">Rows / chunk</div>
                    <input
                      type="number"
                      min={1}
                      max={rows.length}
                      value={chunkRows}
                      onChange={(e) => setChunkRows(Math.max(1, Number(e.target.value) || 1))}
                      className="w-24 text-xs border rounded px-2 py-1.5"
                    />
                  </div>
                  <Button variant="outline" size="sm" onClick={applyFirstRange} className="h-8">
                    First {chunkRows}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={applyNextRange}
                    disabled={rangeTo >= rows.length || phase === "running"}
                    className="h-8"
                  >
                    Next {chunkRows}
                  </Button>
                  <Button variant="outline" size="sm" onClick={applyAllRange} className="h-8">
                    All rows
                  </Button>
                  <span className={`text-xs pb-1.5 ${rangeInvalid ? "text-red-600" : "text-slate-600"}`}>
                    {rangeLabel}
                  </span>
                </div>
                <div className="mt-2 text-[11px] text-slate-500">
                  Runs process only this range — validate a 30k sheet incrementally with First → run → Next →
                  run…{lastEnd > 0 ? ` Last completed: row ${lastEnd}${rangeFrom !== lastEnd + 1 ? ` (re-upload resumes at row ${lastEnd + 1})` : ""}.` : ""}
                </div>
              </div>
            )}

            {headers.length > 0 && (
              <div className="mt-4 text-xs text-slate-600 flex flex-wrap gap-x-4 gap-y-1">
                <span>Name: <b>{cols.name || "—"}</b></span>
                <span>SKU: <b>{cols.sku || "—"}</b></span>
                <span>URL: <b>{cols.url || "—"}</b></span>
                <span>L1: <b>{cols.l1 || "—"}</b></span>
                <span>L2: <b>{cols.l2 || "—"}</b></span>
                <span>L3: <b>{cols.l3 || "—"}</b></span>
                <span>L4: <b>{cols.l4 || "—"}</b></span>
              </div>
            )}

            {error && (
              <div className="mt-4 text-sm text-red-600 flex items-center gap-2">
                <AlertTriangle className="h-4 w-4" /> {error}
              </div>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button
                onClick={start}
                disabled={rows.length === 0 || phase === "running" || missingRequired.length > 0 || rangeInvalid}
                className="gap-2 bg-violet-600 hover:bg-violet-700 text-white"
                title={`Runs rows ${rangeFrom}–${rangeTo}`}
              >
                {phase === "running" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {phase === "running" ? "Validating…" : `Run (batch)${rows.length > 0 && !rangeInvalid ? ` · rows ${rangeFrom}–${rangeTo}` : ""}`}
              </Button>
              <Button
                onClick={startJob}
                disabled={rows.length === 0 || phase === "running" || missingRequired.length > 0 || rangeInvalid}
                variant="outline"
                className="gap-2"
                title="Runs the selected row range in the background with progress you can poll. Best for large sheets."
              >
                {phase === "running" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                Run background job
              </Button>
            </div>
            <div className="mt-2 text-[11px] text-slate-400">
              Both runs process only the selected row range (~1.3s/product warm). Use First → run → Next → run to
              walk through a 30k sheet incrementally; progress and results are per-range.
            </div>
            {rows.length === 0 && (
              <div className="mt-2 text-[11px] text-amber-600">Upload a sheet to enable the run buttons.</div>
            )}
            {rows.length > 0 && missingRequired.length > 0 && (
              <div className="mt-2 text-[11px] text-red-600">
                Cannot run — missing columns: {missingRequired.join(", ")}. Detected:{" "}
                {headers.filter(Boolean).slice(0, 12).join(", ")}
                {headers.length > 12 ? "…" : ""}
              </div>
            )}
            {rows.length > 0 && phase === "running" && (
              <div className="mt-2 text-[11px] text-slate-500">
                Running… buttons re-enable when the run finishes.
              </div>
            )}
          </CardContent>
        </Card>

        {logs.length > 0 && (
          <Card className="mb-6">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Progress</CardTitle>
            </CardHeader>
            <CardContent>
              <Progress value={progress} className="h-2 mb-3" />
              <div ref={logRef} className="h-32 overflow-y-auto rounded bg-slate-900 text-slate-100 text-xs p-3 font-mono space-y-0.5">
                {logs.map((l, i) => (
                  <div key={i}>{l}</div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {phase === "review" && (
          <Card>
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
              <CardTitle className="text-base">
                2. Results
                <span className="ml-3 text-sm font-normal text-slate-500">
                  {stats.correct} correct · {stats.incorrect} incorrect · {stats.review} review · {stats.noImages} no-images · {stats.other} other
                </span>
              </CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  onClick={exportMissingCategories}
                  className="gap-2"
                  variant="outline"
                  disabled={missingCategories === 0}
                  title="Export assigned categories that are not in the DB, in the All Categories.xlsx format"
                >
                  <Download className="h-4 w-4" /> Export Categories
                  {missingCategories > 0 ? ` (${missingCategories})` : ""}
                </Button>
                <Button
                  onClick={exportIncorrect}
                  className="gap-2"
                  variant="outline"
                  disabled={incorrectRowCount === 0}
                  title="Export only the incorrect rows with all original data intact — re-upload to re-run and verify them"
                >
                  <Download className="h-4 w-4" /> Export incorrect
                  {incorrectRowCount > 0 ? ` (${incorrectRowCount})` : ""}
                </Button>
                <Button onClick={exportSheet} className="gap-2" variant="outline">
                  <Download className="h-4 w-4" /> Export corrected sheet
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="text-xs text-slate-500 mb-3">
                On export, incorrect rows have their L1–L4 replaced with the predicted taxonomy path.
                {incorrectRowCount > 0 && (
                  <>
                    {" "}
                    <b>Export incorrect</b> ({incorrectRowCount}) does the opposite — it writes only the rows
                    verdicted <b>incorrect</b>, with every original column and value intact (the recommended
                    path in an extra column), so you can re-upload it and re-run to check whether the
                    verdicts are actually incorrect.
                  </>
                )}
                {missingCategories > 0 && (
                  <>
                    {" "}
                    <b>Export Categories</b> ({missingCategories}) writes the {missingCategories} assigned
                    categor{missingCategories === 1 ? "y" : "ies"} not present in the DB to{" "}
                    <span className="font-mono">All_category_sheet.xlsx</span> (All Categories format).
                  </>
                )}
              </div>
              <div className="overflow-x-auto rounded border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Product</TableHead>
                      <TableHead>SKU</TableHead>
                      <TableHead>Assigned</TableHead>
                      <TableHead>Recommended</TableHead>
                      <TableHead className="text-right">Conf.</TableHead>
                      <TableHead>Diff</TableHead>
                      <TableHead>Verdict</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {products.map((p, i) => {
                      const r = results[i];
                      return (
                        <TableRow key={i}>
                          <TableCell className="max-w-[240px] truncate" title={r?.title || p.title}>
                            {r?.title || p.title || "—"}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{r?.sku || p.sku}</TableCell>
                          <TableCell className="max-w-[260px] text-xs text-slate-600" title={r?.assigned?.full}>
                            {r?.assigned?.full || [p.l1, p.l2, p.l3, p.l4].filter(Boolean).join(" > ") || "—"}
                            {r && r.assignedInDb === false && (
                              <span className="ml-1 text-[10px] text-amber-600">(not in DB)</span>
                            )}
                          </TableCell>
                          <TableCell className="max-w-[260px] text-xs font-medium text-violet-700" title={r?.recommended}>
                            {r?.recommended || (r?.status === "correct" ? "—" : "—")}
                          </TableCell>
                          <TableCell className="text-right text-xs">
                            {r?.confidence != null ? `${Math.round(r.confidence * 100)}%` : "—"}
                          </TableCell>
                          <TableCell className="text-xs">{r?.differingLevel || "—"}</TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1.5">
                              <StatusBadge status={r?.status} />
                              {r?.qwenVerified && (
                                <Badge
                                  variant="outline"
                                  className="bg-purple-100 text-purple-700 border-purple-300 text-[10px]"
                                  title="Confirmed/corrected by Qwen-VL from the candidate shortlist"
                                >
                                  Qwen
                                </Badge>
                              )}
                              {r?.keywordAnchor && (
                                <Badge
                                  variant="outline"
                                  className="bg-slate-100 text-slate-600 border-slate-300 text-[10px]"
                                  title={`Title keyword anchor: ${r.keywordAnchor.filter(Boolean).join(" > ")}`}
                                >
                                  keyword
                                </Badge>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
              <div className="mt-3 text-[11px] text-slate-400">
                {ranLabel || rangeLabel} · {products.length} unique products · images fetched from the Gajab product URL ·
                export covers only this validated range.
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
