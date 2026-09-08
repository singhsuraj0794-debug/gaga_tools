import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Upload,
  FileSpreadsheet,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  ArrowLeft,
  Play,
  Wrench,
  RefreshCw,
  Image,
  Eye,
  Copy,
  Server,
} from "lucide-react";
import {
  parseFile,
  validateProduct,
  validateImages,
  runClipVerificationBatched,
  mergeClipResults,
  runHsnSuggestionBatched,
  runTextCorrectionBatched,
  exportCorrectedSheet,
  exportDeduplicatedSheet,
  exportSkuSheet,
  applyUnitVariationContext,
  applyTitleAccuracyContext,
  applyCategoryContext,
  applyValidationHints,
  buildImageCorrections,
  buildFeedback,
  stripHtml,
  descriptionPassesAttributes,
  getPrelistingApiBase,
  setPrelistingApiBase,
  type ValidationResult,
  type ListingRow,
  type CheckResult,
  type Decision,
  type ClipVerificationProduct,
  type CorrectTextResult,
  type HsnSuggestion,
} from "@/lib/listingValidator";

type Phase = "upload" | "validating" | "review";

interface LogEntry {
  time: string;
  row: number;
  sku: string;
  productName: string;
  result: ValidationResult;
}

interface StepStatus {
  label: string;
  running: boolean;
  done: boolean;
  summary: string;
}

export default function PreListingValidator() {
  // ── Core state ──────────────────────────────────────────────────────────
  const [phase, setPhase] = useState<Phase>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [rows, setRows] = useState<ListingRow[]>([]);
  const [headers, setHeaders] = useState<string[]>([]);
  const [fileName, setFileName] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const [results, setResults] = useState<ValidationResult[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [stepLogs, setStepLogs] = useState<StepStatus[]>([]);
  const [progress, setProgress] = useState(0);
  const [stageLabel, setStageLabel] = useState("");
  const [stageElapsed, setStageElapsed] = useState("");
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

  // ── HSN state ───────────────────────────────────────────────────────────
  const [hsnSuggestions, setHsnSuggestions] = useState<Map<string, HsnSuggestion[]>>(new Map());
  const [hsnVisualContext, setHsnVisualContext] = useState<Map<string, string>>(new Map());
  const [hsnAlgorithmVersion, setHsnAlgorithmVersion] = useState<string>("");
  const [hsnSelections, setHsnSelections] = useState<Map<string, { hsn: string; tax: string }>>(new Map());

  // ── Corrections ─────────────────────────────────────────────────────────
  const [selectedSkus, setSelectedSkus] = useState<Set<string>>(new Set());
  const [textCorrections, setTextCorrections] = useState<Map<string, { title?: string; description?: string }>>(new Map());
  const [hoverMenu, setHoverMenu] = useState<string | null>(null);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [imageGenStatus, setImageGenStatus] = useState<string | null>(null);
  const [overlayUrls, setOverlayUrls] = useState<Map<string, string>>(new Map());
  const [visualVerifyStatus, setVisualVerifyStatus] = useState<string | null>(null);
  const [visualVerifyResults, setVisualVerifyResults] = useState<Map<string, any>>(new Map());
  const [duplicateGroups, setDuplicateGroups] = useState<any[]>([]);
  const [duplicateRemoveSkus, setDuplicateRemoveSkus] = useState<Set<string>>(new Set());
  const [duplicateStatus, setDuplicateStatus] = useState<string | null>(null);
  const [selectedSellers, setSelectedSellers] = useState<Set<string>>(new Set());
  const [sellerDropdownOpen, setSellerDropdownOpen] = useState(false);
  const _allRemovedSkus = useRef<Set<string>>(new Set());
  const [useQwen, setUseQwen] = useState(true);
  const [revalidating, setRevalidating] = useState(false);

  const [dismissedChecks, setDismissedChecks] = useState<Map<string, Set<number>>>(new Map());
  const [correctionFeedback, setCorrectionFeedback] = useState<Map<string, boolean>>(new Map());

  // ── Compute API URL (runtime-configurable; survives tunnel restarts) ───────
  const [apiUrl, setApiUrl] = useState<string>(() =>
    (typeof window !== "undefined" && window.localStorage.getItem("plv_api_url")) || getPrelistingApiBase(),
  );
  const [apiTest, setApiTest] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [apiTestDetail, setApiTestDetail] = useState<string>("");

  const updateApiUrl = (value: string) => {
    // Clean concatenation garbage like "http://localhost:80https://host.trycloudflare.com80":
    // extract every well-formed http(s) URL in the text and keep the one that is NOT
    // the localhost placeholder. Then trim and strip a trailing slash.
    let v = value.trim();
    const candidates = v.match(/https?:\/\/[a-zA-Z0-9.-]+(?::\d+)?(?:[\/][^\s]*)?/g);
    if (candidates && candidates.length > 0) {
      v = candidates.find((u) => !/localhost|127\.0\.0\.1/.test(u)) || candidates[candidates.length - 1];
    }
    v = v.replace(/\/+$/, "");
    // Drop stray digits glued onto the TLD (e.g. "...trycloudflare.com80" from a bad paste).
    v = v.replace(/(\.[a-z]{2,})\d+$/i, "$1");
    setApiUrl(v);
  };

  // Auto-discover the current tunnel URL from Supabase Storage. The
  // start-prelisting-tunnel.sh script publishes the live URL to
  // monitoring/prelisting-api-url.txt, so any device opens the app and gets the
  // correct compute URL without manually pasting.
  const discoverTunnelUrl = async () => {
    try {
      const supabaseUrl = (import.meta.env?.VITE_SUPABASE_URL as string | undefined) ||
        "https://okxyskmjsmtykblrtmyi.supabase.co";
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 8000);
      const resp = await fetch(
        `${supabaseUrl}/storage/v1/object/public/monitoring/prelisting-api-url.txt`,
        { signal: ctrl.signal, cache: "no-store" },
      );
      clearTimeout(timer);
      if (!resp.ok) return;
      const raw = (await resp.text()).trim();
      let discovered = raw;
      try {
        const parsed = JSON.parse(raw);
        if (parsed?.url) discovered = String(parsed.url);
      } catch { /* raw text fallback */ }
      discovered = discovered.trim().replace(/\/+$/, "");
      if (!/^https?:\/\//i.test(discovered)) return;
      // Prefer the discovered URL when the saved one is empty, localhost, or stale.
      const current = getPrelistingApiBase();
      const isDefault = /localhost|127\.0\.0\.1/.test(current) || !current;
      if (isDefault || current !== discovered) {
        setApiUrl(discovered);
      }
    } catch { /* ignore — user can paste manually */ }
  };

  const testApiConnection = async () => {
    const base = getPrelistingApiBase();
    setApiTest("testing");
    setApiTestDetail("Testing connection...");
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 10000);
      const resp = await fetch(`${base}/api/products/status`, { signal: ctrl.signal });
      clearTimeout(timer);
      if (resp.ok) {
        setApiTest("ok");
        setApiTestDetail(`Connected (HTTP ${resp.status}) — compute is reachable`);
      } else {
        setApiTest("fail");
        setApiTestDetail(`API reachable but returned HTTP ${resp.status}`);
      }
    } catch (e: any) {
      setApiTest("fail");
      const reason = e?.name === "AbortError" ? "timed out (10s)" : (e?.message || String(e));
      setApiTestDetail(`Cannot reach API — ${reason}. Start the tunnel and paste its URL.`);
    }
  };

  useEffect(() => {
    setPrelistingApiBase(apiUrl);
    if (typeof window !== "undefined") window.localStorage.setItem("plv_api_url", apiUrl);
  }, [apiUrl]);

  // On mount: auto-discover the current tunnel URL (published to Supabase by the
  // tunnel script), then test connectivity so the badge reflects the live URL.
  useEffect(() => {
    (async () => {
      await discoverTunnelUrl();
      setTimeout(() => testApiConnection(), 300);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const logRef = useRef<HTMLDivElement>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Format-agnostic helpers ────────────────────────────────────────────────
  const _normCol = (s: string) => s.replace(/[*＊]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  const _findCol = (row: ListingRow, ...candidates: string[]) => {
    const keys = Object.keys(row);
    for (const c of candidates) {
      const found = keys.find((k) => _normCol(k) === _normCol(c));
      if (found) return found;
    }
    return undefined;
  };
  const getSku = (row: ListingRow): string => {
    const k = _findCol(row, "Sku *", "Sku", "Sku Number", "SKU Number");
    return k ? String(row[k] ?? "").trim() : "";
  };
  const getSkuKey = (row: ListingRow): string | undefined => {
    return _findCol(row, "Sku *", "Sku", "Sku Number", "SKU Number");
  };
  const getTitle = (row: ListingRow): string => {
    const k = _findCol(row, "Product Name *", "Product Name");
    return k ? String(row[k] ?? "").trim() : "";
  };
  const getDesc = (row: ListingRow): string => {
    const k = _findCol(row, "Description *", "Description");
    return k ? String(row[k] ?? "").trim() : "";
  };
  const getBrand = (row: ListingRow): string => {
    const k = _findCol(row, "Brand Name *", "Brand Name", "Business Name");
    return k ? String(row[k] ?? "").trim() : "";
  };
  const getCategory = (row: ListingRow): string => {
    const k = _findCol(row, "Category Name *", "Category Name");
    return k ? String(row[k] ?? "").trim() : "";
  };
  const getSeller = (row: ListingRow): string => {
    const k = _findCol(row, "Seller Name", "Seller", "Seller Id", "Seller ID", "seller_name", "Brand Name *", "Brand Name", "Business Name");
    return k ? String(row[k] ?? "").trim() : "";
  };
  const getImages = (row: ListingRow): string[] => {
    return Array.from({ length: 10 }, (_, i) => {
      const k = _findCol(row, `Product Image ${i + 1} *`, `Product Image ${i + 1}`);
      return k ? String(row[k] ?? "").trim() : "";
    }).filter((url) => /^https?:\/\//i.test(url));
  };

  const scrollLogToBottom = useCallback(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, []);

  // ── Seller list with product counts ─────────────────────────────────────
  const sellerList = useMemo(() => {
    if (rows.length === 0) return [];
    const counts = new Map<string, number>();
    for (const row of rows) {
      const s = getSeller(row) || "(no seller)";
      counts.set(s, (counts.get(s) || 0) + 1);
    }
    return [...counts.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count);
  }, [rows]);

  // ── Dismiss individual flags ────────────────────────────────────────────
  const dismissCheck = (sku: string, checkIdx: number) => {
    setDismissedChecks((prev) => {
      const next = new Map(prev);
      const existing = prev.get(sku) || new Set<number>();
      const nextSet = new Set(existing);
      nextSet.add(checkIdx);
      next.set(sku, nextSet);
      return next;
    });
    // Mark the check as passed and recalculate decision
    setResults((prev) => {
      const updated = prev.map((r) => {
        if (r.sku !== sku) return r;
        const modified = r.checks.map((c, ci) =>
          ci === checkIdx ? { ...c, passed: true, decision: "PASS" as Decision, message: c.message + " (dismissed by user)" } : c
        );
        const total = modified.length;
        const passedC = modified.filter((c) => c.passed).length;
        const hasReject = modified.some((c) => c.decision === "REJECT" && !c.passed);
        const hasFlag = modified.some((c) => c.decision === "FLAG" && !c.passed);
        let newDecision: Decision;
        let newScore: number;
        if (hasReject) { newDecision = "REJECT"; newScore = Math.max(0, Math.round((passedC / total) * 6)); }
        else if (hasFlag) { newDecision = "FLAG"; newScore = Math.min(10, 7 + Math.round((passedC / total) * 3)); }
        else { newDecision = "PASS"; newScore = 10; }
        return { ...r, checks: modified, decision: newDecision, score: newScore };
      });
      _currentResults.current = updated;
      return updated;
    });
  };

  // ── Upload handlers ─────────────────────────────────────────────────────
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };
  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };
  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const droppedFile = e.dataTransfer.files?.[0];
    if (droppedFile) await handleFileSelect(droppedFile);
  };

  const handleFileSelect = async (selectedFile: File) => {
    const ext = selectedFile.name.split(".").pop()?.toLowerCase();
    if (!ext || !["xlsx", "xls", "csv"].includes(ext)) {
      setParseError("Unsupported file format. Please upload .xlsx, .xls, or .csv");
      return;
    }
    setFile(selectedFile);
    setParseError(null);
    try {
      const result = await parseFile(selectedFile);
      if (result.rowCount === 0) {
        setParseError("No data rows found in file. The file may be empty or formatted incorrectly.");
        return;
      }
      setRows(result.rows);
      setHeaders(result.headers);
      setFileName(result.fileName);
    } catch (err) {
      setParseError(err instanceof Error ? err.message : "Failed to parse file");
    }
  };

  // ── Start validation (all 3 steps auto-sequential) ──────────────────────
  const startValidation = async () => {
    try {
      const t0 = performance.now();
    setPhase("validating");
    setResults([]);
    setLogs([]);
    setCurrentRow(0);
    setProgress(0);
    setStageLabel("Parsing file...");
    setStageElapsed("");
    setHsnSuggestions(new Map());
    setHsnVisualContext(new Map());
    setHsnAlgorithmVersion("");
    setHsnSelections(new Map());
    setSelectedSkus(new Set());
    setDismissedChecks(new Map());
    setStepLogs([
      { label: "Image Analysis", running: false, done: false, summary: "" },
      { label: "Validation Checks", running: false, done: false, summary: "" },
      { label: "HSN Suggestion", running: false, done: false, summary: "" },
      { label: "Text Corrections", running: false, done: false, summary: "" },
    ]);
    setExpandedRows(new Set());

    const now = () => new Date().toLocaleTimeString("en-US", { hour12: false });
    const elapsed = () => {
      const s = Math.floor((performance.now() - t0) / 1000);
      return `${Math.floor(s/60)}m ${s%60}s`;
    };
    const pushLog = (sku: string, row: number, msg: string) => {
      setLogs((prev) => [...prev, {
        time: now(), row, sku, productName: msg,
        result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
      }]);
    };

    pushLog("SYS", 0, `[START] Validation began at ${now()} for ${rows.length} products`);

    // ── Step 1: Image Analysis (Marqo/CLIP) ────────────────────────────
    setProgress(5);
    setStageLabel("Analyzing product images...");
    setStepLogs((prev) => prev.map((s, i) => (i === 0 ? { ...s, running: true } : s)));
    
    const productsWithImages = rows
      .map((row) => {
        const images = getImages(row);
        return images.length > 0 ? { sku: getSku(row), images } : null;
      })
      .filter(Boolean);

    pushLog("SYS", 0, `[IMG] ${productsWithImages.length}/${rows.length} products have images`);
    if (productsWithImages.length > 0) {
      try {
        const clipInput = productsWithImages.map((p) => ({
          sku: p!.sku,
          firstImageUrl: p!.images[0],
          allImageUrls: p!.images,
        }));
        const clipResult = await runClipVerificationBatched(clipInput, "", (bi, tb) => {
          pushLog("SYS", 0, `[IMG] CLIP batch ${bi}/${tb}...`);
        });
        const clipMap = new Map(clipResult.results?.map((cr: any) => [cr.sku, cr]) || []);
        _clipMapRef.current = clipMap as Map<string, any>;
        const flagged = clipResult.results?.filter(
          (cr: any) => cr.rule5?.flagged || cr.rule6?.anyFlagged || cr.rule7?.anyFlagged
        );
        const rule1Issues = clipResult.results?.filter((cr: any) => cr.rule1 && !cr.rule1.passed).length || 0;
        const rule2Issues = clipResult.results?.filter((cr: any) => cr.rule2 && !cr.rule2.passed).length || 0;
        const rule3Issues = clipResult.results?.filter((cr: any) => cr.rule3 && !cr.rule3.passed).length || 0;
        const rule4Issues = clipResult.results?.filter((cr: any) => cr.rule4 && !cr.rule4.passed).length || 0;
          pushLog("SYS", 0, `[IMG] Image analysis complete in ${elapsed()} — R1:${rule1Issues} R2:${rule2Issues} R3:${rule3Issues} R4:${rule4Issues} R5:${flagged?.filter((cr: any) => cr.rule5?.flagged).length || 0} flagged`);
      } catch (e) {
        pushLog("SYS", 0, `[IMG] CLIP error: ${e}`);
      }
    }
    setStepLogs((prev) => prev.map((s, i) => (i === 0 ? { ...s, running: false, done: true, summary: `${productsWithImages.length} analyzed` } : s)));
    setProgress(20);
    scrollLogToBottom();

    // ── Step 2: Validation Checks ──────────────────────────────────────
    setStageLabel("Running validation checks...");
    setStepLogs((prev) => prev.map((s, i) => (i === 1 ? { ...s, running: true } : s)));

    const localRows = rows;
    const results: ValidationResult[] = [];
    const batchLogs: LogEntry[] = [];
    const batchNow = now;

    for (let i = 0; i < localRows.length; i++) {
      const row = localRows[i];
      setCurrentRow(i + 1);
      const pct = 20 + Math.round(((i + 1) / localRows.length) * 30);
      setProgress(pct);
      setStageLabel(`Validating product ${i + 1}/${localRows.length}`);

      let result: ValidationResult;
      try {
        result = validateProduct(row, localRows);
      } catch (err) {
        console.error("validateProduct crashed on row", i + 1, localRows[i], err);
        result = {
          sku: getSku(row) || `row-${i}`,
          productName: getTitle(row),
          category: getCategory(row),
          decision: "FLAG",
          score: 1,
          checks: [{ field: "Internal", check: "Validation error", passed: false, decision: "FLAG", message: String(err) }],
        };
      }

      // Populate imageChecks (URLs for thumbnails + export feedback) WITHOUT
      // adding the client-side rule checks — the backend clip-verify is the
      // authoritative source for image rules (it loads images server-side,
      // avoiding browser CORS failures on Meesho/etc.).
      try {
        const imgResult = await validateImages(row);
        result = { ...result, imageChecks: imgResult.imageChecks };
      } catch (err) {
        console.error("validateImages crashed on row", i + 1, err);
      }

      results.push(result);

      batchLogs.push({
        time: batchNow(), row: i + 1, sku: result.sku,
        productName: result.productName, result,
      });
    }

    _currentResults.current = results;
    _currentRows.current = localRows;
    
    // Apply CLIP verification results (rule5/6/7)
    const clipMap = _clipMapRef.current;
    if (clipMap && clipMap.size > 0) {
      pushLog("SYS", 0, `[IMG-RULES] Applying CLIP verification to ${clipMap.size} products`);
      for (let i = 0; i < results.length; i++) {
        const clipItem = clipMap.get(results[i].sku);
        if (clipItem) {
          results[i] = mergeClipResults(results[i], clipItem);
        }
      }
      _currentResults.current = results;
    }
    setResults(results);

    if (batchLogs.length > 0) setLogs((prev) => [...prev, ...batchLogs]);
    const failed = results.filter((r) => r.decision !== "PASS").length;
    pushLog("SYS", 0, `[VALIDATE] ${results.length} products checked — ${failed} flagged (${elapsed()})`);
    setStepLogs((prev) => prev.map((s, i) => (i === 1 ? { ...s, running: false, done: true, summary: `${results.length} checked · ${failed} flagged` } : s)));
    setProgress(55);
    scrollLogToBottom();

    // ── Step 3: HSN Suggestion ─────────────────────────────────────────
    setStageLabel("Suggesting HSN codes...");
    setStepLogs((prev) => prev.map((s, i) => (i === 2 ? { ...s, running: true } : s)));
    await runStep2_inline();
    setStepLogs((prev) => prev.map((s, i) => (i === 2 ? { ...s, running: false, done: true, summary: `${_hsnData.current.size} suggested` } : s)));
    setProgress(75);
    scrollLogToBottom();

    // ── Step 4: Text Corrections ───────────────────────────────────────
    setStageLabel("Generating text corrections...");
    setStepLogs((prev) => prev.map((s, i) => (i === 3 ? { ...s, running: true } : s)));
    await runTextCorrections();
    const txtCount = _currentResults.current.filter((r: any) => r.descSuggestion || r.titleSuggestion).length;
    setStepLogs((prev) => prev.map((s, i) => (i === 3 ? { ...s, running: false, done: true, summary: `${txtCount} suggestions` } : s)));
    setProgress(95);

    // Final normalize
    const finalResults = _currentResults.current.map((result) =>
      normalizeDecision(applyDismissals(result)),
    );
    _currentResults.current = finalResults;
    setResults(finalResults);
    const withIssues = new Set(finalResults.filter((r) => r.decision !== "PASS").map((r) => r.sku));
    setSelectedSkus(withIssues);

    pushLog("SYS", 0, `[DONE] Validation complete in ${elapsed()} — ${withIssues.size} products need attention`);
    setProgress(100);
    setStageLabel("Complete");
    setStageElapsed(elapsed());
    setTimeout(() => setPhase("review"), 500);
    } catch (e: any) {
      setLogs((prev) => [...prev, {
        time: new Date().toLocaleTimeString("en-US", { hour12: false }),
        row: 0, sku: "ERROR",
        productName: `[FATAL] Validation crashed: ${e?.message || e}`,
        result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
      }]);
      setPhase("review");
    }
  };  // Keep a ref for async access to latest results
  const _currentResults = useRef<ValidationResult[]>([]);
  const _currentRows = useRef<ListingRow[]>([]);
  const _hsnData = useRef<Map<string, { productTypeLabel?: string; productType?: string; titleProductType?: string; titleProductTypeLabel?: string; titleAccuracyStatus?: string; suggestions?: HsnSuggestion[]; categoryV2?: string; categoryV2Label?: string; categoryConfidence?: number; categoryStatus?: string; originalCategoryV2?: string; originalCategoryV2Label?: string; marqoCategory?: string; marqoCategoryLabel?: string; marqoConfidence?: number }>>(new Map());
  // Persistent corrections that survive revalidation — keyed by SKU
  const _appliedCorrections = useRef<Map<string, { title?: string; description?: string }>>(new Map());
  const _clipMapRef = useRef<Map<string, any>>(new Map());

  const normalizeDecision = (result: ValidationResult): ValidationResult => {
    const hasReject = result.checks.some((check) => !check.passed && check.decision === "REJECT");
    const hasFlag = result.checks.some((check) => !check.passed && check.decision === "FLAG");
    const passed = result.checks.filter((check) => check.passed).length;
    const total = result.checks.length;
    if (hasReject) {
      return { ...result, decision: "REJECT", score: total ? Math.max(0, Math.round((passed / total) * 6)) : 0 };
    }
    if (hasFlag) {
      return { ...result, decision: "FLAG", score: total ? Math.min(10, 7 + Math.round((passed / total) * 3)) : 7 };
    }
    return { ...result, decision: "PASS", score: 10 };
  };

  const applyDismissals = (result: ValidationResult): ValidationResult => {
    const dismissed = dismissedChecks.get(result.sku);
    if (!dismissed || dismissed.size === 0) return result;
    return {
      ...result,
      checks: result.checks.map((check, index) => dismissed.has(index)
        ? {
            ...check,
            passed: true,
            decision: "PASS" as Decision,
            message: check.message.includes("dismissed by user")
              ? check.message
              : `${check.message} (dismissed by user)`,
          }
        : check),
    };
  };

  useEffect(() => {
    _currentResults.current = results;
  }, [results]);
  useEffect(() => {
    _currentRows.current = rows;
  }, [rows]);

  // ── Step 1: Title & Description validation ─────────────────────────────
  const [currentRow, setCurrentRow] = useState(0);

  const runStep1 = async () => {
    setStepLogs((prev) => prev.map((s, i) => (i === 0 ? { ...s, running: true } : s)));

    const allResults: ValidationResult[] = [];
    const localRows = _currentRows.current;
    const applied = _appliedCorrections.current;

    for (let i = 0; i < localRows.length; i++) {
      setCurrentRow(i + 1);

      // Use applied corrections to build a modified row for validation
      const row = localRows[i];
      const sku = getSku(row);
      const corr = applied.get(sku);
      let effectiveRow = row;
      if (corr) {
        effectiveRow = { ...row };
        if (corr.title) { const k = _findCol(row, "Product Name *", "Product Name"); if (k) effectiveRow[k] = corr.title; }
        if (corr.description) { const k = _findCol(row, "Description *", "Description"); if (k) effectiveRow[k] = corr.description; }
      }

      let result: ValidationResult;
      try {
        result = validateProduct(effectiveRow, localRows);
      } catch (err) {
        console.error("validateProduct crashed on row", i + 1, localRows[i], err);
        const sku = localRows[i] ? getSku(localRows[i]) || `row-${i + 1}` : `row-${i + 1}`;
        setLogs((prev) => [
          ...prev,
          {
            time: new Date().toLocaleTimeString("en-US", { hour12: false }),
            row: i + 1,
            sku,
            productName: localRows[i] ? getTitle(localRows[i]) || "?" : "?",
            result: {
              sku,
              productName: localRows[i] ? getTitle(localRows[i]) || "?" : "?",
              category: "",
              decision: "REJECT",
              score: 0,
              checks: [{
                field: "Internal",
                check: "Validation crashed",
                passed: false,
                decision: "REJECT",
                message: err instanceof Error ? err.message : String(err),
              }],
            },
          },
        ]);
        continue;
      }

      allResults.push(result);

      setLogs((prev) => [
        ...prev,
        {
          time: new Date().toLocaleTimeString("en-US", { hour12: false }),
          row: i + 1,
          sku: result.sku,
          productName: corr?.title || result.productName,
          result,
        },
      ]);
      setResults([...allResults]);
      _currentResults.current = allResults;
      scrollLogToBottom();

      await new Promise((r) => setTimeout(r, 50));
    }

    const passCount = allResults.filter((r) => r.decision === "PASS").length;
    const flagCount = allResults.filter((r) => r.decision === "FLAG").length;
    const rejectCount = allResults.filter((r) => r.decision === "REJECT").length;
    setStepLogs((prev) =>
      prev.map((s, i) =>
        i === 0
          ? {
              ...s,
              running: false,
              done: true,
              summary: `${allResults.length} products · ${passCount}P ${flagCount}F ${rejectCount}R`,
            }
          : s,
      ),
    );
    scrollLogToBottom();
  };

  // ── Step 2: HSN suggestion ─────────────────────────────────────────────
  const runStep2 = async () => {
    setStepLogs((prev) => prev.map((s, i) => (i === 1 ? { ...s, running: true } : s)));

    const localRows = _currentRows.current;
    const localResults = _currentResults.current;
    const applied = _appliedCorrections.current;
    if (localRows.length === 0) {
      setStepLogs((prev) => prev.map((s, i) => (i === 1 ? { ...s, running: false, done: true, summary: "No products" } : s)));
      return;
    }

    const products = localRows.map((row, idx) => {
      const sku = getSku(row) || localResults[idx]?.sku || "";
      const corr = applied.get(sku);
      const images = getImages(row);
      return { sku, title: corr?.title || getTitle(row), description: corr?.description || getDesc(row), images };
    });

    try {
      const hsnResult = await runHsnSuggestionBatched(products);
      const hsnDataBySku = new Map(hsnResult.results.map((r) => [r.sku, r]));
      _hsnData.current = hsnDataBySku;
      const map = new Map<string, HsnSuggestion[]>();
      const visualMap = new Map<string, string>();

      // Per-product HSN logging
      const hsnNow = () => new Date().toLocaleTimeString("en-US", { hour12: false });
      const hsnLogs: LogEntry[] = [];
      for (const item of hsnResult.results) {
        if (item.suggestions.length > 0) {
          map.set(item.sku, item.suggestions);
          const top = item.suggestions[0];
          hsnLogs.push({
            time: hsnNow(), row: 0, sku: item.sku,
            productName: `HSN: ${item.sku} — top=${top.hsn} GST=${top.gst_rate}% conf=${(top.confidence * 100).toFixed(0)}% | "${top.description?.substring(0, 50)}"`,
            result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
          });
        }
        if (item.visualContext) visualMap.set(item.sku, item.visualContext);
      }

      setHsnSuggestions(map);
      setHsnVisualContext(visualMap);
      setHsnAlgorithmVersion(hsnResult.algorithmVersion || hsnResult.results[0]?.algorithmVersion || "");

      // Refine checks + commit top-ranked HSN automatically. Compute the
      // array synchronously so the next step cannot read a stale ref.
      if (hsnDataBySku.size > 0) {
        const updated = _currentResults.current.map((res) => {
          const item = hsnDataBySku.get(res.sku);
          if (!item) return res;
          let r = res;
          if (item.unitVariation && item.unitVariation !== "optional") {
            r = applyUnitVariationContext(r, item.unitVariation, item.productTypeLabel);
          }
          r = applyTitleAccuracyContext(r, item.productType, item.productTypeLabel, item.titleProductType, item.titleProductTypeLabel, item.titleAccuracyStatus);
          r = applyCategoryContext(r, item.categoryStatus as any, item.categoryV2Label || item.categoryV2, item.originalCategoryV2Label || item.originalCategoryV2, item.marqoConfidence);
          r = applyValidationHints(r, item.validationHints, item.productType);
          // AI corrections will be populated after HSN, keep undefined for now
          if (item.opClaims && item.opClaims.length > 0) {
            const flags = item.opClaims.slice(0, 3).join("; ");
            r.checks = r.checks.map((c) => {
              if (c.check !== "Misleading claims") return c;
              return { ...c, passed: false, decision: "FLAG" as Decision, message: `Semantic claim: "${flags}"` };
            });
          }
          return r;
        });
        _currentResults.current = updated;
        setResults(updated);
      }

      // Commit top-ranked HSN
      setHsnSelections(() => {
        const next = new Map<string, { hsn: string; tax: string }>();
        for (const [sku, suggestions] of map) {
          const designated = designatedHsn(suggestions[0]);
          if (designated) next.set(sku, designated);
        }
        return next;
      });

      if (hsnLogs.length > 0) {
        setLogs((prev) => [...prev, ...hsnLogs]);
        scrollLogToBottom();
      }

      setStepLogs((prev) =>
        prev.map((s, i) =>
          i === 1 ? { ...s, running: false, done: true, summary: `${map.size} products suggested · ${hsnResult.algorithmVersion || ""}` } : s,
        ),
      );
    } catch (err) {
      console.error("HSN suggestion failed:", err);
      setStepLogs((prev) =>
        prev.map((s, i) =>
          i === 1 ? { ...s, running: false, done: true, summary: `Failed: ${err instanceof Error ? err.message : String(err)}` } : s,
        ),
      );
    }
  };

  // ── Step 2 inline (called from new pipeline, no stepLog management) ───
  const runStep2_inline = async () => {
    const localRows = _currentRows.current;
    const localResults = _currentResults.current;
    const applied = _appliedCorrections.current;
    if (localRows.length === 0) return;

    const now = () => new Date().toLocaleTimeString("en-US", { hour12: false });

    const products = localRows.map((row, idx) => {
      const sku = getSku(row) || localResults[idx]?.sku || "";
      const corr = applied.get(sku);
      const images = getImages(row);
      return { sku, title: corr?.title || getTitle(row), description: corr?.description || getDesc(row), images };
    });

    try {
      const hsnResult = await runHsnSuggestionBatched(products);
      const hsnDataBySku = new Map(hsnResult.results.map((r: any) => [r.sku, r]));
      _hsnData.current = hsnDataBySku;
      const map = new Map<string, HsnSuggestion[]>();
      const visualMap = new Map<string, string>();

      for (const item of hsnResult.results) {
        if (item.suggestions.length > 0) {
          map.set(item.sku, item.suggestions);
          const top = item.suggestions[0];
          setLogs((prev) => [...prev, {
            time: now(), row: 0, sku: item.sku,
            productName: `HSN: ${item.sku} — top=${top.hsn} GST=${top.gst_rate}%`,
            result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
          }]);
        }
        if (item.visualContext) visualMap.set(item.sku, item.visualContext);
      }

      setHsnSuggestions(map);
      setHsnVisualContext(visualMap);
      setHsnAlgorithmVersion(hsnResult.algorithmVersion || hsnResult.results[0]?.algorithmVersion || "");

      if (hsnDataBySku.size > 0) {
        // Normalize: same V2 category → prefer most frequent top HSN
        const catToHsns = new Map<string, Map<string, number>>();
        for (const [sku, item] of hsnDataBySku) {
          const catV2 = (item as any).categoryV2 || "";
          const topHsn = item.suggestions?.[0]?.hsn || "";
          if (!catV2 || !topHsn || catV2 === "generic") continue;
          if (!catToHsns.has(catV2)) catToHsns.set(catV2, new Map());
          const counts = catToHsns.get(catV2)!;
          counts.set(topHsn, (counts.get(topHsn) || 0) + 1);
        }
        const catBestHsn = new Map<string, string>();
        for (const [cat, counts] of catToHsns) {
          let best = "";
          let bestCount = 0;
          for (const [hsn, count] of counts) {
            if (count > bestCount) { bestCount = count; best = hsn; }
          }
          catBestHsn.set(cat, best);
        }

        const updated = _currentResults.current.map((res) => {
          const item = hsnDataBySku.get(res.sku);
          if (!item) return res;
          let r = res;
          if (item.unitVariation && item.unitVariation !== "optional") {
            r = applyUnitVariationContext(r, item.unitVariation, item.productTypeLabel);
          }
          r = applyTitleAccuracyContext(r, item.productType, item.productTypeLabel, item.titleProductType, item.titleProductTypeLabel, item.titleAccuracyStatus);
          r = applyCategoryContext(r, item.categoryStatus as any, item.categoryV2Label || item.categoryV2, item.originalCategoryV2Label || item.originalCategoryV2, item.marqoConfidence);
          r = applyValidationHints(r, item.validationHints, item.productType);
          return r;
        });
        _currentResults.current = updated;
        setResults(updated);
      }
    } catch (err) {
      setLogs((prev) => [...prev, {
        time: now(), row: 0, sku: "BATCH",
        productName: `[HSN] FAILED — ${err instanceof Error ? err.message : String(err)}`,
        result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
      }]);
    }
  };

  // ── Step 3: Image verification ─────────────────────────────────────────
  const runStep3 = async () => {
    setStepLogs((prev) => prev.map((s, i) => (i === 1 ? { ...s, running: true } : s)));

    const localRows = _currentRows.current;
    const currentR = _currentResults.current;
    let updatedResults = [...currentR];
    const now = () => new Date().toLocaleTimeString("en-US", { hour12: false });
    const batchLogs: LogEntry[] = [];
    const pushLog = (sku: string, row: number, msg: string) => {
      batchLogs.push({
        time: now(), row, sku, productName: msg,
        result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
      });
    };

    // 3a: Client-side image checks (Rules 1-4)
    for (let i = 0; i < updatedResults.length; i++) {
      const r = updatedResults[i];
      const row = localRows[i];
      const sku = r.sku;
      const title = getTitle(row).substring(0, 40);
      pushLog(sku, i + 1, `[IMG] Checking ${sku} — "${title}..."`);
      try {
        const imageResult = await validateImages(row);
        updatedResults[i] = {
          ...updatedResults[i],
          checks: [...updatedResults[i].checks, ...imageResult.checks],
          imageChecks: imageResult.imageChecks,
        };
        const imgCount = imageResult.imageChecks?.length ?? 0;
        const dupes = imageResult.imageChecks?.filter((ic) => ic.isDuplicate).length ?? 0;
        const ops = imageResult.imageChecks?.filter((ic) => ic.hasOpsClaim).length ?? 0;
        const failed = imageResult.imageChecks?.filter((ic) => !ic.loaded).length ?? 0;
        const imgIssues = [
          dupes > 0 ? `${dupes} dupe` : "",
          ops > 0 ? `${ops} ops-claim` : "",
          failed > 0 ? `${failed} failed-load` : "",
        ].filter(Boolean).join(", ");
        pushLog(sku, i + 1, `[IMG] ${sku} — ${imgCount} images${imgIssues ? ` · Issues: ${imgIssues}` : " · All OK"}`);

        const hasReject = updatedResults[i].checks.some((c) => c.decision === "REJECT" && !c.passed);
        const hasFlag = updatedResults[i].checks.some((c) => c.decision === "FLAG" && !c.passed);
        const total = updatedResults[i].checks.length;
        const passedC = updatedResults[i].checks.filter((c) => c.passed).length;
        if (hasReject) {
          updatedResults[i].decision = "REJECT";
          updatedResults[i].score = Math.max(0, Math.round((passedC / total) * 6));
        } else if (hasFlag && updatedResults[i].decision === "PASS") {
          updatedResults[i].decision = "FLAG";
          updatedResults[i].score = Math.min(10, 7 + Math.round((passedC / total) * 3));
        } else {
          updatedResults[i].score = Math.min(10, 7 + Math.round((passedC / total) * 3));
        }
      } catch (err) {
        pushLog(sku, i + 1, `[IMG] ${sku} — ERROR: ${err instanceof Error ? err.message : String(err)}`);
      }
      await new Promise((r) => setTimeout(r, 30));
    }

    setResults(updatedResults);
    _currentResults.current = updatedResults;

    // 3b: Server-side Rules 5-7 (CLIP + OCR)
    const clipProducts: ClipVerificationProduct[] = updatedResults
      .map((r, idx) => {
        const row = localRows[idx];
        const imageUrls = getImages(row);
        return { sku: r.sku, firstImageUrl: imageUrls[0] || "", allImageUrls: imageUrls };
      })
      .filter((p) => p.firstImageUrl);

    if (clipProducts.length > 0) {
      pushLog("BATCH", 0, `[CLIP] Verifying ${clipProducts.length} products with images...`);
      try {
        const clipResult = await runClipVerificationBatched(clipProducts, "", (bi, tb) => {
          pushLog("BATCH", 0, `[CLIP] Batch ${bi}/${tb}...`);
        });
        const clipMap = new Map(clipResult.results.map((cr) => [cr.sku, cr]));

        for (const cr of clipResult.results) {
          const parts: string[] = [];
          if (cr.rule5?.hasMarkings) parts.push(`RULE5: markings on img1`);
          if (cr.rule6?.anyFlagged) parts.push(`RULE6: content issue`);
          if (cr.rule7?.anyFlagged) {
            const ocr = cr.rule7.images?.map((image) => image.ocrText).filter(Boolean).join("; ");
            parts.push(`RULE7: ops claim${ocr ? ` OCR=\"${ocr.substring(0, 40)}\"` : ""}`);
          }
          if (!cr.rule5?.hasMarkings && !cr.rule6?.anyFlagged && !cr.rule7?.anyFlagged) parts.push("All OK");
          pushLog(cr.sku, 0, `[CLIP] ${cr.sku} — ${parts.join(" | ")}`);
        }
        updatedResults = updatedResults.map((r) => {
          const clipItem = clipMap.get(r.sku);
          return clipItem ? mergeClipResults(r, clipItem) : r;
        });
        setResults(updatedResults);
        _currentResults.current = updatedResults;
      } catch (err) {
        pushLog("BATCH", 0, `[CLIP] FAILED — ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    // Push ALL step-3 logs in ONE batch
    if (batchLogs.length > 0) {
      setLogs((prev) => [...prev, ...batchLogs]);
      scrollLogToBottom();
    }

    const dupCount = updatedResults.filter((r) => r.imageChecks?.some((ic) => ic.isDuplicate)).length;
    setStepLogs((prev) =>
      prev.map((s, i) =>
        i === 2
          ? { ...s, running: false, done: true, summary: `${updatedResults.length} checked${dupCount ? ` · ${dupCount} w/ dupes` : ""}` }
          : s,
      ),
    );
    scrollLogToBottom();
  };

  // ── Revalidation ────────────────────────────────────────────────────────
  const revalidate = async () => {
    const t0 = performance.now();
    setRevalidating(true);
    setPhase("validating");
    setProgress(0);
    setStageLabel("Re-validating...");
    setStageElapsed("");
    setStepLogs([
      { label: "Validation Checks", running: false, done: false, summary: "" },
      { label: "HSN Suggestion", running: false, done: false, summary: "" },
      { label: "Text Corrections", running: false, done: false, summary: "" },
    ]);
    setExpandedRows(new Set());

    const now = () => new Date().toLocaleTimeString("en-US", { hour12: false });
    const elapsed = () => {
      const s = Math.floor((performance.now() - t0) / 1000);
      return `${Math.floor(s/60)}m ${s%60}s`;
    };

    // Step 1: Re-run validation
    setProgress(10);
    setStageLabel("Re-running validation checks...");
    setStepLogs((prev) => prev.map((s, i) => (i === 0 ? { ...s, running: true } : s)));

    const localResults = _currentResults.current;
    const localRows = _currentRows.current;
    const applied = _appliedCorrections.current;
    const results: ValidationResult[] = [];

    for (let i = 0; i < localRows.length; i++) {
      const row = localRows[i];
      setCurrentRow(i + 1);
      setProgress(10 + Math.round(((i + 1) / localRows.length) * 30));

      const effectiveRow = { ...row };
      const sku = getSku(row);
      if (applied.has(sku)) {
        const corr = applied.get(sku)!;
        if (corr.title) { const k = _findCol(row, "Product Name *", "Product Name"); if (k) effectiveRow[k] = corr.title; }
        if (corr.description) { const k = _findCol(row, "Description *", "Description"); if (k) effectiveRow[k] = corr.description; }
      }
      let result: ValidationResult;
      try {
        result = validateProduct(effectiveRow, localRows);
      } catch (err) {
        result = {
          sku, productName: getTitle(row),
          category: getCategory(row),
          decision: "FLAG", score: 1,
          checks: [{ field: "Internal", check: "Error", passed: false, decision: "FLAG", message: String(err) }],
        };
      }
      const imgChk = await validateImages(effectiveRow);
      result = { ...result, imageChecks: imgChk.imageChecks };

      // Preserve CLIP rule5/6/7 from previous run
      const prevClip = localResults.find((r) => r.sku === sku);
      if (prevClip) {
        const clipChecks = prevClip.checks.filter((c) =>
          c.check.startsWith("RULE 5") || c.check.startsWith("RULE 6") || c.check.startsWith("RULE 7")
        );
        if (clipChecks.length > 0) {
          result = { ...result, checks: [...result.checks, ...clipChecks] };
        }
      }

      results.push(result);
    }

    // Re-apply CLIP rule5/6/7 from _clipMapRef
    const clipMap = _clipMapRef.current;
    if (clipMap && clipMap.size > 0) {
      for (let i = 0; i < results.length; i++) {
        const clipItem = clipMap.get(results[i].sku);
        if (clipItem) {
          results[i] = mergeClipResults(results[i], clipItem);
        }
      }
    }

    _currentResults.current = results;
    _currentRows.current = [...localRows];
    setResults(results);
    setStepLogs((prev) => prev.map((s, i) => (i === 0 ? { ...s, running: false, done: true, summary: `${results.length} re-checked` } : s)));
    setProgress(45);
    scrollLogToBottom();

    // Step 2: HSN — skip if already fetched, just re-apply context
    const hsnAlreadyFetched = _hsnData.current.size > 0;
    setStepLogs((prev) => prev.map((s, i) => (i === 1 ? { ...s, running: true } : s)));
    if (hsnAlreadyFetched) {
      setStageLabel("Re-applying HSN context...");
      // Re-apply HSN context without re-fetching
      const hsnDataBySku = _hsnData.current;
      const updated = _currentResults.current.map((res) => {
        const item = hsnDataBySku.get(res.sku);
        if (!item) return res;
        let r = res;
        if (item.unitVariation && item.unitVariation !== "optional") {
          r = applyUnitVariationContext(r, item.unitVariation, item.productTypeLabel);
        }
        r = applyTitleAccuracyContext(r, item.productType, item.productTypeLabel, item.titleProductType, item.titleProductTypeLabel, item.titleAccuracyStatus);
          r = applyCategoryContext(r, item.categoryStatus as any, item.categoryV2Label || item.categoryV2, item.originalCategoryV2Label || item.originalCategoryV2, item.marqoConfidence);
        r = applyValidationHints(r, item.validationHints, item.productType);
        return r;
      });
      _currentResults.current = updated;
      setResults(updated);
      setStepLogs((prev) => prev.map((s, i) => (i === 1 ? { ...s, running: false, done: true, summary: "HSN context re-applied (cached)" } : s)));
    } else {
      setStageLabel("Suggesting HSN codes...");
      await runStep2_inline();
      setStepLogs((prev) => prev.map((s, i) => (i === 1 ? { ...s, running: false, done: true, summary: "done" } : s)));
    }
    setProgress(70);

    // Step 3: Text corrections
    setStepLogs((prev) => prev.map((s, i) => (i === 2 ? { ...s, running: true } : s)));
    setStageLabel("Generating text corrections...");
    await runTextCorrections();
    setStepLogs((prev) => prev.map((s, i) => (i === 2 ? { ...s, running: false, done: true, summary: "done" } : s)));
    setProgress(95);

    const finalResults = _currentResults.current.map((result) =>
      normalizeDecision(applyDismissals(result)),
    );
    _currentResults.current = finalResults;
    setResults(finalResults);
    const withIssues = new Set(finalResults.filter((r) => r.decision !== "PASS").map((r) => r.sku));
    setSelectedSkus(withIssues);
    setProgress(100);
    setStageLabel("Complete");
    setPhase("review");
    setRevalidating(false);
    setTimeout(() => scrollLogToBottom(), 100);
  };  // ── AI Text Corrections (MiniLM API) ────────────────────────────────────

  const runTextCorrections = async () => {
    const localRows = _currentRows.current;
    const localResults = _currentResults.current;
    const applied = _appliedCorrections.current;
    if (localRows.length === 0) return;

    const now = () => new Date().toLocaleTimeString("en-US", { hour12: false });
    const batchLogs: LogEntry[] = [];
    const pushLog = (sku: string, row: number, msg: string) => {
      batchLogs.push({
        time: now(), row, sku, productName: msg,
        result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
      });
    };

    pushLog("BATCH", 0, `[TXT] Starting text correction for ${localRows.length} products${useQwen ? " (Qwen VLM ON)" : ""}...`);

    const correctProducts = localRows.map((row, idx) => {
      const r = localResults[idx] || { sku: "" };
      const hsnItem = _hsnData.current?.get(r.sku);
      const corr = applied.get(r.sku);
      const rowImages = getImages(row);
      const title = corr?.title || getTitle(row);
      const description = corr?.description || getDesc(row);
      pushLog(r.sku, idx + 1, `[TXT] ${r.sku} — title="${title.substring(0, 50)}" imgs=${rowImages.length}`);
      return {
        sku: r.sku,
        title,
        description,
        brand: getBrand(row),
        category: getCategory(row),
        productTypeLabel: hsnItem?.productTypeLabel || "",
        v2Category: hsnItem?.categoryV2 || "",
        v2Confidence: hsnItem?.categoryConfidence || 0,
        images: rowImages,
      };
    }).filter((p) => p.sku && (p.title || p.description));

    if (correctProducts.length === 0) {
      pushLog("BATCH", 0, "[TXT] No products to correct");
      setLogs((prev) => [...prev, ...batchLogs]);
      return;
    }

    try {
      pushLog("BATCH", 0, `[TXT] Calling API for ${correctProducts.length} products (batched)...`);
      const result = await runTextCorrectionBatched(correctProducts, "", useQwen, (bi, tb) => {
        pushLog("BATCH", 0, `[TXT] Batch ${bi}/${tb}...`);
      });
      pushLog("BATCH", 0, `[TXT] API returned ${result.results.length} results`);

      const corrMap = new Map(result.results.map((cr) => [cr.sku, { title: cr.title, description: cr.description, log: cr.log }]));

      const updated = _currentResults.current.map((r) => {
          const corr = corrMap.get(r.sku);
          if (!corr) return r;
          const next = {
            ...r,
            titleSuggestion: corr.title ?? r.titleSuggestion,
            descSuggestion: corr.description ?? r.descSuggestion,
          };
          if (corr.description && descriptionPassesAttributes(corr.description)) {
            next.checks = next.checks.map((c) => {
              if (c.check === "Product attributes" && !c.passed) {
                return { ...c, passed: true, decision: "PASS" as Decision, message: "Attributes resolved: specs auto-generated from title+description" };
              }
              return c;
            });
          }
          return next;
      });
      _currentResults.current = updated;
      setResults(updated);

      // Detailed per-product logging
      for (const cr of result.results) {
        const parts: string[] = [];
        if (cr.log && cr.log.length > 0) {
          for (const l of cr.log) parts.push(l);
        }
        if (cr.title) {
          parts.push(`TITLE SUGGESTION: "${cr.title}"`);
        } else {
          parts.push("TITLE: no change needed");
        }
        if (cr.description) {
          parts.push(`DESC SUGGESTION: "${cr.description.substring(0, 80)}..."`);
        } else {
          parts.push("DESC: no change needed");
        }
        pushLog(cr.sku, 0, `[TXT] ${cr.sku} — ${parts.join(" | ")}`);
      }
      const titleCount = result.results.filter((r) => r.title).length;
      const descCount = result.results.filter((r) => r.description).length;
      pushLog("BATCH", 0, `[TXT] Done — ${titleCount} title suggestions, ${descCount} desc suggestions`);
    } catch (err) {
      pushLog("BATCH", 0, `[TXT] FAILED — ${err instanceof Error ? err.message : String(err)}`);
    }

    // Push ALL step-4 logs in ONE batch
    if (batchLogs.length > 0) {
      setLogs((prev) => [...prev, ...batchLogs]);
      scrollLogToBottom();
    }
  }; 

  // ── Toolbar hover helpers ───────────────────────────────────────────────
  const hoverIn = (menu: string) => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    setHoverMenu(menu);
  };
  const hoverOut = () => {
    hoverTimer.current = setTimeout(() => setHoverMenu(null), 200);
  };

  // ── Correction actions ──────────────────────────────────────────────────
  const designatedHsn = (suggestion: HsnSuggestion | undefined) => {
    const hsn = suggestion?.hsn?.split(",")[0]?.trim() ?? "";
    return /^\d{4}$|^\d{6}$|^\d{8}$/.test(hsn) && suggestion ? { hsn, tax: String(suggestion.gst_rate) } : undefined;
  };

  const applyHsnToSelected = () => {
    setHsnSelections((prev) => {
      const next = new Map(prev);
      for (const sku of selectedSkus) {
        const suggestions = hsnSuggestions.get(sku);
        if (suggestions && suggestions.length > 0) {
          const designated = designatedHsn(suggestions[0]);
          if (designated) next.set(sku, designated);
        }
      }
      return next;
    });
    setHoverMenu(null);
  };

  const applyHsnToAll = () => {
    setHsnSelections((prev) => {
      const next = new Map(prev);
      for (const [sku, suggestions] of hsnSuggestions) {
        if (suggestions.length > 0) {
          const designated = designatedHsn(suggestions[0]);
          if (designated) next.set(sku, designated);
        }
      }
      return next;
    });
    setHoverMenu(null);
  };

  const applyTitleSuggestions = () => {
    const now = new Date().toLocaleTimeString("en-US", { hour12: false });
    const newLogs: LogEntry[] = [];
    const updates: { sku: string; title: string }[] = [];
    for (const sku of selectedSkus) {
      const res = results.find((r) => r.sku === sku);
      if (res?.titleSuggestion) {
        updates.push({ sku, title: res.titleSuggestion });
        _appliedCorrections.current.set(sku, { ..._appliedCorrections.current.get(sku), title: res.titleSuggestion });
        newLogs.push({
          time: now, row: 0, sku,
          productName: `✓ TITLE APPLIED: "${res.titleSuggestion}"`,
          result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },        });
      }
    }
    if (updates.length > 0) {
      setTextCorrections((prev) => {
        const next = new Map(prev);
        for (const u of updates) next.set(u.sku, { ...next.get(u.sku), title: u.title });
        return next;
      });
      setRows((prevRows) => prevRows.map((row) => {
        const sku = getSku(row);
        const u = updates.find((x) => x.sku === sku);
        return u ? { ...row, [_findCol(row, "Product Name *", "Product Name") || "Product Name *"]: u.title } : row;
      }));
      const skuSet = new Set(updates.map((u) => u.sku));
      setResults((prev) => prev.map((r) => skuSet.has(r.sku) ? { ...r, titleSuggestion: undefined } : r));
    }
    if (newLogs.length > 0) setLogs((prev) => [...prev, ...newLogs]);
    setHoverMenu(null);
  };

  const applyDescSuggestions = () => {
    const now = new Date().toLocaleTimeString("en-US", { hour12: false });
    const newLogs: LogEntry[] = [];
    const updates: { sku: string; description: string }[] = [];
    for (const sku of selectedSkus) {
      const res = results.find((r) => r.sku === sku);
      if (res?.descSuggestion) {
        updates.push({ sku, description: res.descSuggestion });
        _appliedCorrections.current.set(sku, { ..._appliedCorrections.current.get(sku), description: res.descSuggestion });
        newLogs.push({
          time: now, row: 0, sku,
          productName: `✓ DESC APPLIED`,
          result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
        });
      }
    }
    if (updates.length > 0) {
      setTextCorrections((prev) => {
        const next = new Map(prev);
        for (const u of updates) next.set(u.sku, { ...next.get(u.sku), description: u.description });
        return next;
      });
      setRows((prevRows) => prevRows.map((row) => {
        const sku = getSku(row);
        const u = updates.find((x) => x.sku === sku);
        return u ? { ...row, [_findCol(row, "Description *", "Description") || "Description *"]: u.description } : row;
      }));
      const skuSet = new Set(updates.map((u) => u.sku));
      setResults((prev) => prev.map((r) => skuSet.has(r.sku) ? { ...r, descSuggestion: undefined } : r));
    }
    if (newLogs.length > 0) setLogs((prev) => [...prev, ...newLogs]);
    setHoverMenu(null);
  };

  const applyTitleSuggestionsToAll = () => {
    const now = new Date().toLocaleTimeString("en-US", { hour12: false });
    const newLogs: LogEntry[] = [];
    const updates: { sku: string; title: string }[] = [];
    for (const res of results) {
      if (res.titleSuggestion) {
        updates.push({ sku: res.sku, title: res.titleSuggestion });
        _appliedCorrections.current.set(res.sku, { ..._appliedCorrections.current.get(res.sku), title: res.titleSuggestion });
        newLogs.push({
          time: now, row: 0, sku: res.sku,
          productName: `✓ TITLE APPLIED: "${res.titleSuggestion}"`,
          result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
        });
      }
    }
    if (updates.length > 0) {
      setTextCorrections((prev) => {
        const next = new Map(prev);
        for (const u of updates) next.set(u.sku, { ...next.get(u.sku), title: u.title });
        return next;
      });
      setRows((prevRows) => prevRows.map((row) => {
        const sku = getSku(row);
        const u = updates.find((x) => x.sku === sku);
        return u ? { ...row, [_findCol(row, "Product Name *", "Product Name") || "Product Name *"]: u.title } : row;
      }));
      const skuSet = new Set(updates.map((u) => u.sku));
      setResults((prev) => prev.map((r) => skuSet.has(r.sku) ? { ...r, titleSuggestion: undefined } : r));
    }
    if (newLogs.length > 0) setLogs((prev) => [...prev, ...newLogs]);
    setHoverMenu(null);
  };

  const applyDescSuggestionsToAll = () => {
    const now = new Date().toLocaleTimeString("en-US", { hour12: false });
    const newLogs: LogEntry[] = [];
    const updates: { sku: string; description: string }[] = [];
    for (const res of results) {
      if (res.descSuggestion) {
        updates.push({ sku: res.sku, description: res.descSuggestion });
        _appliedCorrections.current.set(res.sku, { ..._appliedCorrections.current.get(res.sku), description: res.descSuggestion });
        newLogs.push({
          time: now, row: 0, sku: res.sku,
          productName: `✓ DESC APPLIED`,
          result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
        });
      }
    }
    if (updates.length > 0) {
      setTextCorrections((prev) => {
        const next = new Map(prev);
        for (const u of updates) next.set(u.sku, { ...next.get(u.sku), description: u.description });
        return next;
      });
      setRows((prevRows) => prevRows.map((row) => {
        const sku = getSku(row);
        const u = updates.find((x) => x.sku === sku);
        return u ? { ...row, [_findCol(row, "Description *", "Description") || "Description *"]: u.description } : row;
      }));
      const skuSet = new Set(updates.map((u) => u.sku));
      setResults((prev) => prev.map((r) => skuSet.has(r.sku) ? { ...r, descSuggestion: undefined } : r));
    }
    if (newLogs.length > 0) setLogs((prev) => [...prev, ...newLogs]);
    setHoverMenu(null);
  };

  const imageGenSheet = async () => {
    if (!file) return;
    setImageGenStatus("Generating overlay images...");
    const now = new Date().toLocaleTimeString("en-US", { hour12: false });

    const extractSpecsFromText = (title: string, desc: string): Record<string, string> => {
      const specs: Record<string, string> = {};
      const text = `${title} ${desc}`;
      const textLower = text.toLowerCase();

      // Products where visual specs (Material, Type) are unreliable from images/text alone
      const isNonMaterialProduct = /\b(tablet|cleaner|descaler|cleaning|liquid|powder|gel|cream|paste|soap|detergent|shampoo|oil|solution|concentrate|strip|sachet|capsule|spray|foam|scrub|freshener|deodorant|sanitizer|disinfectant)\b/i.test(text);

      // Material — expanded list with Indian/common materials
      if (!isNonMaterialProduct) {
        const matMatch = text.match(/\b(cotton|polyester|nylon|stainless\s*steel|leather|rubber|plastic|wood|metal|brass|aluminium|aluminum|silk|wool|canvas|fiberglass|silicone|bamboo|ceramic|glass|porcelain|cast\s*iron|granite|marble|jute|denim|satin|velvet|terry\s*cotton|pp|abs|tpu|zinc\s*alloy|alloy|iron|steel|acrylic|resin|mDF|mdf|terracotta|clay|copper|bronze|nickel|chrome|tin|lead|pvc|ev|eva|microfiber|net|mesh|lace|chiffon|georgette|crepe|rayon|linen|khadi|chikankari|kundan|meenakari|kundan|jaduai|thewa|bidri|dhokra|pattachitra|madhubani|warli|ikat|bandhani|leheriya|ajrakh|kalamkari|block\s*print|screen\s*print|digital\s*print|embroidered|handloom|powerloom|handmade|handcrafted|artisan)\b/i);
        if (matMatch) specs["Material"] = matMatch[1].replace(/\b\w/g, (c: string) => c.toUpperCase());
      }

      // Color — handle multi-word colors
      const colorPatterns = [
        /(?:color|colour)[:\s]*([a-z\s]+?)(?:\s*[,.\n]|$)/i,
        /\b(rose\s*gold|midnight\s*blue|dark\s*blue|light\s*blue|sky\s*blue|navy\s*blue|forest\s*green|olive\s*green|sea\s*green|hot\s*pink|baby\s*pink|dusty\s*pink|burnt\s*orange|rust\s*red|wine\s*red|maroon|burgundy|teal|turquoise|lavender|mauve|peach|cream|ivory|charcoal|slate|beige|tan|camel|mahogany|walnut|cherry|maple|ebony|bronze|copper|golden|silver|platinum|metallic|matte|glossy|shimmer|iridescent|holographic|neon|fluorescent|pastel|earth\s*tone|multicolor|rainbow|mix(?:ed)?\s*color|assorted)\b/i,
        /\b(black|white|blue|red|green|yellow|pink|purple|orange|grey|gray|brown|beige|navy|teal|maroon|gold|silver|transparent|multicolor|rainbow)\b/i
      ];
      for (const pat of colorPatterns) {
        const m = text.match(pat);
        if (m) {
          specs["Color"] = m[1].trim().replace(/\b\w/g, (c: string) => c.toUpperCase());
          break;
        }
      }

      // Capacity/Volume — expanded units
      const capMatch = text.match(/\b(\d+(?:\.\d+)?)\s*(ml|l|ltr|litre|liter|gallon|gal|oz|gm|g|kg|ltrs|litres|liters|cl|dl|qt|pt|fl\s*oz|cup|tbsp|tsp)\b/i);
      if (capMatch) specs["Capacity"] = `${capMatch[1]} ${capMatch[2].toUpperCase()}`;

      // Weight
      const wtMatch = text.match(/(?:weight|wt|net\s*wt|gross\s*wt)[:\s]*(\d+(?:\.\d+)?)\s*(g|kg|lb|lbs|oz|gm|grams?|kilograms?)\b/i);
      if (wtMatch) specs["Weight"] = `${wtMatch[1]} ${wtMatch[2]}`;

      // Dimensions — 2D and 3D
      const dim3Match = text.match(/(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|inch|inches|in|m|ft|feet)\b/i);
      if (dim3Match) specs["Dimensions"] = `${dim3Match[1]} x ${dim3Match[2]} x ${dim3Match[3]} ${dim3Match[4]}`;
      else {
        const dim2Match = text.match(/(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|inch|inches|in|m|ft|feet)\b/i);
        if (dim2Match) specs["Dimensions"] = `${dim2Match[1]} x ${dim2Match[2]} ${dim2Match[3]}`;
      }

      // Size — clothing and general (skip standalone numbers like "3" from "3 in 1")
      const sizeMatch = text.match(/\bsize[:\s]*(xs|s|m|l|xl|xxl|xxxl|xxxxl|small|medium|large|extra\s*large|free\s*size)\b/i);
      if (sizeMatch) specs["Size"] = sizeMatch[1].toUpperCase();

      // Pattern
      const patMatch = text.match(/\b(solid|printed|striped|floral|plain|checked|plaid|geometric|abstract|polka\s*dot|cartoon|cute|embroidered|woven|knitted|crocheted|patchwork|tie\s*dye|batik|block\s*print|screen\s*print|digital\s*print|sublimation|engraved|etched|carved|molded|textured|smooth|rough|matte|glossy|shimmer|glitter|sequin|beaded|tasseled|fringed|ruffled|pleated|gathered|smocked|quilted|padded|lined|unlined)\b/i);
      if (patMatch) specs["Pattern"] = patMatch[1].replace(/\b\w/g, (c: string) => c.toUpperCase());

      // Type / Product type — expanded (skip for non-material products like cleaners, tablets)
      if (!isNonMaterialProduct) {
        const typeMatch = text.match(/\b(bottle|bag|wallet|belt|watch|earring|necklace|bracelet|ring|cap|hat|shoe|sandal|slipper|towel|blanket|curtain|pillow|cover|case|stand|holder|dispenser|cooker|grinder|mixer|chopper|peeler|ladle|spoon|fork|knife|scissors|clip|keychain|keyring|toy|figurine|lamp|light|fan|mirror|hook|hanger|organizer|basket|tray|plate|bowl|cup|mug|glass|jar|container|box|purse|clutch|saree|kurti|kurta|lehenga|sherwani|suit|blazer|shirt|t[\-\s]?shirt|top|blouse|dress|skirt|pant|trouser|jeans|shorts|jumpsuit|romper|jersey|polo|hoodie|sweater|jacket|coat|vest|shawl|stole|dupatta|scarf|glove|sock|inner|thermal|nightwear|lounge|pyjama|pajama|tracksuit|sports|gym|yoga|swim|bikini|underwear|bra|brief|boxers|vest|camisole|slip|bodysuit|corset|bustier|chemise|nightie|robe|gown|anarkali|palazzo|patiala|dhoti|lungi|nagra|mojari|jutti|kolhapuri|chappal|floaters|sneakers|boots|heels|flats|loafers|oxfords|moccasins|sliders|clogs|wedges|platforms|stilettos|pumps|mules|espadrilles|brogues|derby|monk)\b/i);
        if (typeMatch) specs["Type"] = typeMatch[1].replace(/\b\w/g, (c: string) => c.toUpperCase());
      }

      // Theme / Character
      const themeMatch = text.match(/\b(cartoon|cute|kawaii|disney|pokemon|dragon\s*ball|doraemon|tom\s*&?\s*jerry|avengers|marvel|batman|spider[\s-]?man|princess|unicorn|dinosaur|animal|monster|superhero|fantasy|vintage|retro|minimalist|modern|classic|luxury|elegant|bohemian|boho|rustic|industrial|scandinavian|japanese|japanese|chinese|indian|pakistani|arabic|turkish|moroccan|french|italian|spanish|american|british|australian|african|mexican|thai|vietnamese|korean|filipino|indonesian|malaysian|singaporean|chinese|taiwanese|hong\s*kong)\b/i);
      if (themeMatch) specs["Theme"] = themeMatch[1].replace(/\b\w/g, (c: string) => c.toUpperCase());

      // Target / Audience
      const targetMatch = text.match(/\b(for\s+kids|for\s+girls|for\s+boys|for\s+women|for\s+men|for\s+baby|for\s+couples|for\s+teens|for\s+toddlers|kids|girls|boys|women|men|baby|toddler|infant|adult|teen|unisex|family|couples|women|ladies|gentlemen|girls|boys|kids|children|senior|elderly|professional|office|home|travel|outdoor|indoor|gym|sports|yoga|meditation|yoga|pilates|running|walking|hiking|camping|trekking|cycling|swimming|surfing|skating|skiing|snowboarding|climbing|rafting|kayaking|canoeing|fishing|hunting|camping|picnic|beach|pool|garden|patio|balcony|rooftop|terrace|lawn|yard|garage|attic|basement|kitchen|bathroom|bedroom|living\s*room|dining\s*room|study\s*room|office|workshop|studio|gym|salon|spa|hotel|restaurant|cafe|bar|pub|club|lounge|mall|shop|store|market|bazaar|fair|festival|event|party|wedding|birthday|anniversary|christmas|diwali|eid|holi|navratri|dussehra|ganesh\s*chaturthi|onam|pongal|baisakhi|lohri|makar\s*sankranti|republic\s*day|independence\s*day|gandhi\s*jayanti|ambedkar\s*jayanti|teachers\s*day|childrens\s*day|mothers\s*day|fathers\s*day|valentines\s*day|womens\s*day)\b/i);
      if (targetMatch) {
        const t = targetMatch[1].replace(/^for\s+/i, '');
        specs["Target"] = t.replace(/\b\w/g, (c: string) => c.toUpperCase());
      }

      // Usage / Occasion
      const usageMatch = text.match(/\b(party|gift|birthday|wedding|anniversary|festival|diwali|christmas|new\s*year|valentine|return\s*gift|return gift|home\s*decor|kitchen|bathroom|bedroom|office|travel|gym|outdoor|indoor|daily\s*use|casual|formal|ethnic|western|traditional|contemporary|fusion|bohemian|sporty|trendy|fashion|fancy|designer|branded|premium|luxury|budget|affordable|economical|value|combo|set|pair|single|piece)\b/i);
      if (usageMatch) specs["Use"] = usageMatch[1].replace(/\b\w/g, (c: string) => c.toUpperCase());

      // Quantity / Set / Pack
      const qtyMatch = text.match(/\b(set\s+of\s+\d+|pack\s+of\s+\d+|combo\s+of\s+\d+|\d+\s*pcs?|\d+\s*pieces?|\d+\s*items?|\d+\s*pair|\d+\s*pairs|pair|single|assorted|mixed)\b/i);
      if (qtyMatch) specs["Pack"] = qtyMatch[1];

      // Occasion / Festival
      const occMatch = text.match(/\b(wedding|birthday|anniversary|diwali|christmas|new\s*year|valentine|navratri|dussehra|ganesh\s*chaturthi|onam|pongal|baisakhi|eid|republic\s*day|independence\s*day|mothers\s*day|fathers\s*day|teachers\s*day|return\s*gift|party|ceremony|celebration|festive|festival| pooja| puja| temple| religious| spiritual| astrological| vastu| feng\s*shui)\b/i);
      if (occMatch) specs["Occasion"] = occMatch[1].replace(/\b\w/g, (c: string) => c.toUpperCase());

      // Country of Origin
      const originMatch = text.match(/(?:country\s+of\s+origin|made\s+in|origin|manufactured\s+in|produced\s+in)[:\s]*(india|china|usa|united\s*states|uk|united\s*kingdom|japan|korea|germany|france|italy|spain|turkey|uae|dubai|bangladesh|sri\s*lanka|nepal|pakistan|thailand|vietnam|indonesia|malaysia|philippines|taiwan|hong\s*kong|singapore|australia|canada|brazil|mexico|russia|egypt|south\s*africa|nigeria|kenya|ethiopia|ghana|morocco|tunisia|algeria|libya|sudan|chad|niger|mali|senegal|guinea|sierra\s*leone|liberia|ivory\s*coast|burkina\s*faso|togo|benin|gabon|congo|cameroon|central\s*african\s*republic|chad|equatorial\s*guinea|sao\s*tome|principe|angola|zambia|zimbabwe|mozambique|madagascar|malawi|tanzania|uganda|rwanda|burundi|somalia|djibouti|eritrea|ethiopia|sudan|south\s*sudan|uganda|kenya|tanzania|zanzibar|comoros|mauritius|seychelles|madagascar|reunion|mayotte|french\s*southern|antarctic|australia|new\s*zealand|fiji|papua|new\s*guinea|solomon\s*islands|vanuatu|samoa|tonga|tuvalu|nauru|kiribati|marshall\s*islands|micronesia|palau|northern\s*mariana|guam|american\s*samoa|cook\s*islands|niue|tokelau|wallis|futuna|french\s*polynesia|new\s*caledonia)\b/i);
      if (originMatch) specs["Origin"] = originMatch[1].replace(/\b\w/g, (c: string) => c.toUpperCase());

      // Power / Battery / Voltage
      const powerMatch = text.match(/\b(\d+\s*(?:w|watts?|kw|hp|v|volts?|mah|ah|hours?|hrs?)|rechargeable|cordless|wireless|usb\s*powered|battery\s*operated|ac\s*powered|dc\s*powered|solar\s*powered|manual|electric|non[\s-]electric)\b/i);
      if (powerMatch) specs["Power"] = powerMatch[1];

      // Warranty
      const warrantyMatch = text.match(/\b(\d+\s*(?:days?|months?|years?|yrs?)\s*(?:warranty|guarantee|replacement|return))\b/i);
      if (warrantyMatch) specs["Warranty"] = warrantyMatch[1];

      // Brand from title (if not already set)
      if (!specs["Brand"]) {
        const brandMatch = text.match(/^([A-Z][A-Za-z0-9\s&]+?)(?:\s+(?:for|new|latest|original|authentic|premium|best|top|high|quality|combo|set|pack|piece|pair|assorted))/i);
        if (brandMatch && brandMatch[1].length > 2 && brandMatch[1].length < 30) {
          specs["Brand"] = brandMatch[1].trim();
        }
      }

      return specs;
    };

    const genProducts = results.map((r) => {
      const specs: Record<string, string> = {};
      const row = rows.find((rw) => getSku(rw) === r.sku) || {};

      // Source 1: Parse HTML specs from descSuggestion
      const desc = r.descSuggestion || "";
      const isNonMaterialForDesc = /\b(tablet|cleaner|descaler|cleaning|liquid|powder|gel|cream|paste|soap|detergent|shampoo|oil|solution|concentrate|strip|sachet|capsule|spray|foam|scrub)\b/i.test(`${r.productName} ${desc}`);
      if (desc) {
        const specMatches = desc.matchAll(/<li><b>([^<]+)<\/b>\s*([^<]*)<\/li>/g);
        for (const m of specMatches) {
          if (m[1] && m[2]) {
            const label = m[1].replace(":", "").trim();
            const value = m[2].trim();
            // Skip bad Material/Type from descSuggestion for non-material products
            if (isNonMaterialForDesc && (label.toLowerCase() === "material" || label.toLowerCase() === "type")) continue;
            specs[label] = value;
          }
        }
      }

      // Source 2: Brand from row data
      const brand = getBrand(row);
      if (brand && !specs["Brand"]) specs["Brand"] = brand;

      // Source 3: Category from row data
      const category = getCategory(row);
      if (category && !specs["Category"]) specs["Category"] = category;

      // Source 4: HSN — use GST rate + schedule, but NOT productTypeLabel as TYPE spec
      // (HSN productTypeLabel is a category-level label like "plastic kitchenware" — not a product type)
      const hsnItem = _hsnData.current?.get(r.sku);

      // Products where Material/Type specs from regex are unreliable
      const isNonMaterial = /\b(tablet|cleaner|descaler|cleaning|liquid|powder|gel|cream|paste|soap|detergent|shampoo|oil|solution|concentrate|strip|sachet|capsule|spray|foam|scrub)\b/i.test(`${r.productName} ${desc}`);

      // Source 5: Extract from title + description text (local regex) — skip Material/Type for non-material products
      const textSpecs = extractSpecsFromText(r.productName, desc);
      for (const [k, v] of Object.entries(textSpecs)) {
        if (isNonMaterial && (k === "Material" || k === "Type")) continue;
        if (!specs[k]) specs[k] = v;
      }

      // Source 6: API-based spec extraction (category-aware + Marqo)
      // This will be called later in batch for better performance

      // Priority order for specs (most important first)
      const specPriority = ["Material", "Color", "Type", "Theme", "Target", "Use", "Size", "Capacity", "Weight", "Dimensions", "Pattern", "Pack", "Occasion", "Origin", "Power", "Warranty", "Brand", "Category"];
      const orderedSpecs: Record<string, string> = {};
      for (const key of specPriority) {
        if (specs[key]) orderedSpecs[key] = specs[key];
      }
      // Add any remaining specs not in priority list
      for (const [k, v] of Object.entries(specs)) {
        if (!orderedSpecs[k]) orderedSpecs[k] = v;
      }

      // Image URLs
      const images = getImages(row);
      return {
        sku: r.sku,
        title: r.productName,
        brand,
        firstImageUrl: images[0] || "",
        images,
        specs: orderedSpecs,
      };
    }).filter((p) => p.firstImageUrl);

    if (genProducts.length === 0) {
      setImageGenStatus("No products with images to generate.");
      return;
    }

    // Enhance specs using API-based extraction (category-aware + Marqo)
    try {
      setImageGenStatus("Enhancing specs with AI extraction...");
      const specResp = await fetch(`${getPrelistingApiBase()}/api/products/extract-specs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          products: genProducts.map(p => ({
            sku: p.sku,
            title: p.title,
            description: p.descSuggestion || "",
            category: p.category || "",
            images: p.images.slice(0, 3), // Limit to 3 images for performance
          }))
        }),
      });

      if (specResp.ok) {
        const specData = await specResp.json();
        // Merge AI-extracted specs with existing specs (AI specs take precedence for missing values)
        for (const result of specData.results || []) {
          const product = genProducts.find(p => p.sku === result.sku);
          if (product && result.specs) {
            for (const [k, v] of Object.entries(result.specs)) {
              if (!product.specs[k] && v) {
                product.specs[k] = v as string;
              }
            }
          }
        }
        setLogs((prev) => [...prev, {
          time: now, row: 0, sku: "SPECS",
          productName: `[SPECS] Enhanced specs for ${specData.results?.length || 0} products using AI extraction`,
          result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
        }]);
      }
    } catch (e: any) {
      console.warn("Spec extraction API failed, using local extraction:", e.message);
    }

    // Qwen VLM spec extraction (when enabled)
    if (useQwen) {
      try {
        setImageGenStatus("Extracting specs with Qwen VLM...");
        const qwenProducts = genProducts.map(p => ({
          sku: p.sku,
          title: p.title,
          description: "",
          images: p.images.slice(0, 1),
        }));

        // Batch in groups of 5 (Qwen is slower)
        const qwenResults = new Map<string, Record<string, string>>();
        for (let i = 0; i < qwenProducts.length; i += 5) {
          const batch = qwenProducts.slice(i, i + 5);
          const resp = await fetch(`${getPrelistingApiBase()}/api/products/qwen-specs`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ products: batch }),
          });
          if (resp.ok) {
            const data = await resp.json();
            for (const r of data.results || []) {
              if (r.specs) qwenResults.set(r.sku, r.specs);
            }
          }
          setImageGenStatus(`Qwen VLM: ${Math.min(i + 5, qwenProducts.length)}/${qwenProducts.length} products...`);
        }

        // Merge Qwen specs (Qwen takes precedence — it actually understands the image)
        for (const product of genProducts) {
          const qwenSpecs = qwenResults.get(product.sku);
          if (qwenSpecs) {
            for (const [k, v] of Object.entries(qwenSpecs)) {
              // Always use Qwen's specs — it's a VLM that sees the actual product
              product.specs[k] = v as string;
            }
          }
        }

        setLogs((prev) => [...prev, {
          time: now, row: 0, sku: "QWEN",
          productName: `[QWEN] Extracted specs for ${qwenResults.size} products using Qwen VLM`,
          result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
        }]);
      } catch (e: any) {
        console.warn("Qwen VLM spec extraction failed:", e.message);
      }
    }

    try {
      const resp = await fetch(`${getPrelistingApiBase()}/api/products/image-gen`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ products: genProducts }),
      });
      if (!resp.ok) {
        setImageGenStatus(`ImageGen failed: ${resp.status}`);
        return;
      }
      const data = await resp.json();
      const successCount = data.results?.filter((r: any) => r.image_url).length || 0;
      
      // Store overlay image URLs for export
      const newUrls = new Map<string, string>();
      for (const r of data.results || []) {
        if (r.image_url) {
          newUrls.set(r.sku, r.image_url);
        }
      }
      setOverlayUrls(newUrls);
      
      setLogs((prev) => [...prev, {
        time: now, row: 0, sku: "IMGGEN",
        productName: `[IMGGEN] Generated ${successCount} overlay images on ImgBB`,
        result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] },
      }]);
      setImageGenStatus(`Uploaded ${successCount}/${genProducts.length} overlay images to ImgBB. Export the sheet to include them.`);
    } catch (e: any) {
      setImageGenStatus(`ImageGen error: ${e.message}`);
    }
  };

  const findSheetDuplicates = async () => {
    if (rows.length < 2) return;
    setDuplicateStatus("Scanning images for duplicates...");
    try {
      const products = rows.map((row) => {
        const images = getImages(row);
        return {
          sku: getSku(row),
          title: getTitle(row),
          seller: getSeller(row),
          images,
        };
      }).filter((p) => p.sku && p.images.length > 0);

      if (products.length < 2) {
        setDuplicateStatus(`Duplicate check needs at least 2 products with images. Found ${rows.length} rows, ${products.length} with SKU+images.`);
        return;
      }

      // Group by seller name
      const sellerMap = new Map<string, typeof products>();
      for (const p of products) {
        const key = p.seller || "__no_seller__";
        if (!sellerMap.has(key)) sellerMap.set(key, []);
        sellerMap.get(key)!.push(p);
      }

      // Filter to selected sellers only
      const selectedSellerArr = [...selectedSellers];
      const hasSelection = selectedSellerArr.length > 0;
      const filteredMap = new Map<string, typeof products>();
      for (const [key, prods] of sellerMap) {
        if (!hasSelection || selectedSellerArr.includes(key)) {
          filteredMap.set(key, prods);
        }
      }

      const sellerNames = [...filteredMap.keys()].filter((k) => k !== "__no_seller__");
      const hasSellers = sellerNames.length >= 1;

      if (hasSellers) {
        setDuplicateStatus(`Checking ${filteredMap.size} seller batch(es), ${products.length} products...`);
      }

      const allGroups: any[] = [];
      const allRemoveSkus: string[] = [];
      let batchIdx = 0;

      for (const [sellerKey, sellerProducts] of filteredMap) {
        if (sellerProducts.length < 2) continue;
        batchIdx++;

        const label = sellerKey === "__no_seller__" ? `Batch ${batchIdx}` : sellerKey;
        setDuplicateStatus(`[${batchIdx}/${filteredMap.size}] Checking ${label} (${sellerProducts.length} products)...`);

        const startedAt = Date.now();
        const progressInterval = setInterval(() => {
          const elapsed = Math.floor((Date.now() - startedAt) / 1000);
          const m = Math.floor(elapsed / 60);
          const s = elapsed % 60;
          setDuplicateStatus(`[${batchIdx}/${filteredMap.size}] ${label}: scanning images... (${m}m ${s}s elapsed)`);
        }, 5000);

        const resp = await fetch(`${getPrelistingApiBase()}/api/products/sheet-duplicates`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ products: sellerProducts }),
          signal: AbortSignal.timeout(2700000),
        });
        clearInterval(progressInterval);

        if (!resp.ok) {
          const errBody = await resp.json().catch(() => ({}));
          throw new Error(`${label}: ${errBody.error || `HTTP ${resp.status}`}`);
        }
        const result = await resp.json();

        for (const g of (result.groups || [])) {
          g.seller = sellerKey !== "__no_seller__" ? sellerKey : undefined;
        }
        allGroups.push(...(result.groups || []));
        allRemoveSkus.push(...(result.remove_skus || []));
      }

      setDuplicateGroups(allGroups);
      const removeSet = new Set(allRemoveSkus);
      setDuplicateRemoveSkus(removeSet);

      if (allGroups.length > 0) {
        const autoRemoveSkus = new Set<string>();
        allGroups.forEach((g: any) => {
          g.remove.forEach((r: any) => autoRemoveSkus.add(r.sku));
        });
        autoRemoveSkus.forEach((sku) => _allRemovedSkus.current.add(sku));
        setDuplicateStatus(`Found ${allRemoveSkus.length} duplicate(s) in ${allGroups.length} group(s). ${autoRemoveSkus.size} product(s) marked for removal.`);
        setRows((prev) => prev.filter((row) => !autoRemoveSkus.has(getSku(row))));
      } else {
        setDuplicateStatus("No duplicate products found.");
      }
    } catch (e) {
      setDuplicateStatus(`Duplicate check failed: ${e}. Compute API: ${getPrelistingApiBase()} — ensure the tunnel is running and this URL is correct (use the Test button).`);
    }
  };

  const visualVerifySheet = async () => {
    if (!file) return;
    setVisualVerifyStatus("Building product list...");

    try {
      // Build products for visual-verify
      const products = rows.map((row) => {
        const images: string[] = [];
        for (let c = 1; c <= 10; c++) {
          const k = c === 1 ? "Product Image 1 *" : `Product Image ${c}`;
          const val = String(row[k as keyof ListingRow] ?? "").trim();
          if (/^https?:\/\//i.test(val)) images.push(val);
        }
        return {
          sku: getSku(row),
          title: getTitle(row),
          description: getDesc(row).replace(/<[^>]+>/g, " ").substring(0, 500),
          images: images.slice(0, 2),
        };
      }).filter((p) => p.images.length > 0);

      if (products.length === 0) {
        setVisualVerifyStatus("No products with images found.");
        return;
      }

      // Limit to first 30 products for CLIP, then Qwen only on flagged (max 5)
      const limitedProducts = products.slice(0, 30);

      console.log(`[VISUAL] Running CLIP flagging on ${limitedProducts.length} products...`);
      setVisualVerifyStatus(`Running CLIP flagging on ${limitedProducts.length} products...`);

      // Step 1: CLIP only (fast)
      const resp = await fetch(`${getPrelistingApiBase()}/api/products/visual-verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ products: limitedProducts, skipQwen: true }),
        signal: AbortSignal.timeout(120000),
      });
      if (!resp.ok) {
        setVisualVerifyStatus(`Visual verify failed: ${resp.status}`);
        return;
      }
      const clipData = await resp.json();
      const resultMap = new Map<string, any>();
      for (const r of clipData.results || []) {
        resultMap.set(r.sku, r);
      }

      const flaggedCount = clipData.summary?.flagged || 0;
      setVisualVerifyResults(resultMap);

      if (flaggedCount === 0) {
        setVisualVerifyStatus(`No mismatches found out of ${limitedProducts.length} products`);
        return;
      }

      // Step 2: Run Qwen only on flagged items (max 5)
      const flaggedSkus = clipData.results
        .filter((r: any) => r.flagged?.length > 0)
        .slice(0, 5)
        .map((r: any) => r.sku);

      console.log(`[VISUAL] CLIP found ${flaggedCount} flagged. Running Qwen on ${flaggedSkus.length} products...`);
      setVisualVerifyStatus(`CLIP found ${flaggedCount} flagged. Running Qwen corrections on ${flaggedSkus.length} products...`);

      const flaggedProducts = limitedProducts
        .filter((p) => flaggedSkus.includes(p.sku))
        .map((p) => {
          const clipResult = clipData.results.find((r: any) => r.sku === p.sku);
          return { ...p, flagged: clipResult?.flagged || [] };
        });

      const qwenResp = await fetch(`${getPrelistingApiBase()}/api/products/visual-verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ products: flaggedProducts, skipQwen: false }),
        signal: AbortSignal.timeout(180000),
      });

      if (qwenResp.ok) {
        const qwenData = await qwenResp.json();
        // Merge Qwen corrections into results
        for (const qr of qwenData.results || []) {
          const existing = resultMap.get(qr.sku);
          if (existing) {
            existing.corrections = qr.corrections || [];
            existing.corrected_title = qr.corrected_title;
            existing.corrected_description = qr.corrected_description;
          }
        }
        setVisualVerifyResults(new Map(resultMap));
      }

      const correctedCount = Array.from(resultMap.values()).filter((r) => r.corrections?.length > 0).length;
      setVisualVerifyStatus(`Done: ${flaggedCount} flagged, ${correctedCount} corrected out of ${limitedProducts.length} products`);
      console.log(`[VISUAL] Done — ${flaggedCount} flagged, ${correctedCount} corrected`);
    } catch (e: any) {
      setVisualVerifyStatus(`Visual verify error: ${e.message}`);
    }
  };

  const exportSheet = async () => {
    if (!file) return;
    try {
      setExportStatus("Exporting...");
      const corrections = new Map(hsnSelections);
      const imgCorrections = buildImageCorrections(results);
      const feedback = buildFeedback(results);
      const all = await exportCorrectedSheet(file, corrections, textCorrections, imgCorrections, overlayUrls, _allRemovedSkus.current, feedback);
      const removedCount = _allRemovedSkus.current.size;
      const fbCount = feedback.size;
      setExportStatus(`Exported ${all} product update${all === 1 ? "" : "s"} to corrected sheet.${removedCount > 0 ? ` ${removedCount} duplicate${removedCount > 1 ? "s" : ""} excluded.` : ""}${fbCount > 0 ? ` ${fbCount} product${fbCount > 1 ? "s" : ""} flagged in Feedback column.` : ""}`);
      setHoverMenu(null);
    } catch (e: any) {
      setExportStatus(`Export failed: ${e?.message ?? e}`);
      console.error("exportSheet error", e);
    }
  };

  const exportSheetNoDuplicates = async () => {
    if (!file) return;
    try {
      const skipSkus = new Set<string>([..._allRemovedSkus.current]);
      duplicateRemoveSkus.forEach((sku) => skipSkus.add(sku));
      const { keptRows, removedRows } = await exportDeduplicatedSheet(file, skipSkus);
      setDuplicateStatus(`Exported ${keptRows} product${keptRows === 1 ? "" : "s"} to Excel. ${removedRows} duplicate${removedRows !== 1 ? "s" : ""} removed.`);
      setHoverMenu(null);
    } catch (e: any) {
      setDuplicateStatus(`Export failed: ${e?.message ?? e}`);
    }
  };

  const exportRemovedSkuSheet = () => {
    // SKU sheet lists ONLY the SKUs being deleted (removed duplicates),
    // never the SKUs being kept.
    const skus = new Set<string>();
    _allRemovedSkus.current.forEach((s) => skus.add(s));
    duplicateRemoveSkus.forEach((s) => skus.add(s));
    if (skus.size === 0) {
      setDuplicateStatus("No duplicate SKUs to export yet.");
      return;
    }
    exportSkuSheet(skus);
    setDuplicateStatus(`Exported SKU sheet with ${skus.size} SKU${skus.size === 1 ? "" : "s"} to delete.`);
  };

  const toggleAll = () => {
    if (selectedSkus.size === results.length) {
      setSelectedSkus(new Set());
    } else {
      setSelectedSkus(new Set(results.map((r) => r.sku)));
    }
  };

  const toggleSku = (sku: string) => {
    setSelectedSkus((prev) => {
      const next = new Set(prev);
      if (next.has(sku)) next.delete(sku);
      else next.add(sku);
      return next;
    });
  };

  const toggleExpand = (idx: number) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  // ── Decision display helpers ────────────────────────────────────────────
  const decisionBadge = (d: Decision) => {
    switch (d) {
      case "PASS":
        return <Badge className="bg-green-100 text-green-800 border-green-300"><CheckCircle2 className="w-3 h-3 mr-1" /> PASS</Badge>;
      case "FLAG":
        return <Badge className="bg-amber-100 text-amber-800 border-amber-300"><AlertTriangle className="w-3 h-3 mr-1" /> FLAG</Badge>;
      case "REJECT":
        return <Badge className="bg-red-100 text-red-800 border-red-300"><XCircle className="w-3 h-3 mr-1" /> REJECT</Badge>;
    }
  };

  const stats = {
    pass: results.filter((r) => r.decision === "PASS").length,
    flag: results.filter((r) => r.decision === "FLAG").length,
    reject: results.filter((r) => r.decision === "REJECT").length,
    total: results.length,
  };

  const allPass = stats.total > 0 && stats.pass === stats.total;
  const hasTextSuggestions = results.some((r) => r.titleSuggestion || r.descSuggestion);

  // Image review counts (manual review — not auto-correctable)
  const imgDupes = results.filter((r) => r.imageChecks?.some((ic) => ic.isDuplicate)).length;
  const imgOpsClaims = results.filter((r) => r.imageChecks?.some((ic) => ic.hasOpsClaim)).length;
  const imgMarkings = results.filter((r) =>
    r.checks.some((c) => c.check === "RULE 5: Markings (1st image)" && !c.passed && c.message !== "Markings check pending — run DINOv2+CLIP to verify"),
  ).length;
  const imgContent = results.filter((r) =>
    r.checks.some((c) => c.check === "RULE 6: Content restrictions" && !c.passed && c.message !== "Content check pending — run DINOv2+CLIP to verify"),
  ).length;
  const imgReviewCount = imgDupes + imgOpsClaims + imgMarkings + imgContent;
  const hasImageReviewItems = imgReviewCount > 0;

  // ── Toolbar component ───────────────────────────────────────────────────
  const Toolbar = () => (
    <div className="flex items-center gap-1 bg-white border rounded-lg shadow-sm px-3 py-2 mb-6 sticky top-0 z-10">
      <Wrench className="w-4 h-4 text-slate-400 mr-2" />
      {/* HSN dropdown */}
      <div className="relative" onMouseEnter={() => hoverIn("hsn")} onMouseLeave={hoverOut}>
        <Button variant="ghost" size="sm" className="h-8 text-xs">
          HSN <ChevronDown className="w-3 h-3 ml-1" />
        </Button>
        {hoverMenu === "hsn" && (
          <div className="absolute top-full left-0 mt-1 bg-white border rounded-lg shadow-lg py-1 z-20 min-w-[180px]">
            <button onClick={applyHsnToAll} className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100">
              Apply to All ({hsnSuggestions.size})
            </button>
            <button onClick={applyHsnToSelected} className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100" disabled={selectedSkus.size === 0}>
              Apply to Selected ({selectedSkus.size})
            </button>
          </div>
        )}
      </div>
      {/* Images — manual review */}
      <div className="relative" onMouseEnter={() => hoverIn("img")} onMouseLeave={hoverOut}>
        <Button variant="ghost" size="sm" className="h-8 text-xs" disabled={!hasImageReviewItems}>
          <Wrench className="w-3 h-3 mr-1" /> Images{imgReviewCount > 0 ? ` (${imgReviewCount})` : ""} <ChevronDown className="w-3 h-3 ml-1" />
        </Button>
        {hoverMenu === "img" && hasImageReviewItems && (
          <div className="absolute top-full left-0 mt-1 bg-white border rounded-lg shadow-lg py-1 z-20 min-w-[240px]">
            <div className="px-3 py-1 text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Manual Review Required</div>
            {imgDupes > 0 && <div className="px-3 py-1 text-xs text-amber-700">Duplicate images: {imgDupes} product{imgDupes !== 1 ? "s" : ""}</div>}
            {imgMarkings > 0 && <div className="px-3 py-1 text-xs text-amber-700">Markings/watermarks: {imgMarkings} product{imgMarkings !== 1 ? "s" : ""}</div>}
            {imgOpsClaims > 0 && <div className="px-3 py-1 text-xs text-red-700">Ops claims on images: {imgOpsClaims} product{imgOpsClaims !== 1 ? "s" : ""}</div>}
            {imgContent > 0 && <div className="px-3 py-1 text-xs text-red-700">Content restrictions: {imgContent} product{imgContent !== 1 ? "s" : ""}</div>}
            <div className="border-t mt-1 pt-1 px-3 py-1 text-[11px] text-slate-400 italic">Replace flagged images manually in the sheet</div>
          </div>
        )}
      </div>
      {/* Text dropdown */}
      <div className="relative" onMouseEnter={() => hoverIn("text")} onMouseLeave={hoverOut}>
        <Button variant="ghost" size="sm" className="h-8 text-xs" disabled={!hasTextSuggestions}>
          Corrections <ChevronDown className="w-3 h-3 ml-1" />
        </Button>
        {hoverMenu === "text" && hasTextSuggestions && (
          <div className="absolute top-full left-0 mt-1 bg-white border rounded-lg shadow-lg py-1 z-20 min-w-[260px]">
            <button onClick={applyTitleSuggestionsToAll} className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100">
              Apply All Title Suggestions ({results.filter((r) => r.titleSuggestion).length})
            </button>
            <button onClick={applyTitleSuggestions} className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100" disabled={selectedSkus.size === 0}>
              Apply Title to Selected ({results.filter((r) => r.titleSuggestion && selectedSkus.has(r.sku)).length})
            </button>
            <div className="border-t my-1" />
            <button onClick={applyDescSuggestionsToAll} className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100">
              Apply All Desc Suggestions ({results.filter((r) => r.descSuggestion).length})
            </button>
            <button onClick={applyDescSuggestions} className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100" disabled={selectedSkus.size === 0}>
              Apply Desc to Selected ({results.filter((r) => r.descSuggestion && selectedSkus.has(r.sku)).length})
            </button>
          </div>
        )}
      </div>
      {/* + More placeholder */}
      <div className="relative" onMouseEnter={() => hoverIn("more")} onMouseLeave={hoverOut}>
        <Button variant="ghost" size="sm" className="h-8 text-xs text-slate-400 cursor-default">
          + More
        </Button>
        {hoverMenu === "more" && (
          <div className="absolute top-full left-0 mt-1 bg-white border rounded-lg shadow-lg py-1 z-20 min-w-[140px]">
            <div className="px-3 py-1.5 text-xs text-slate-400 italic">Coming soon</div>
          </div>
        )}
      </div>
      {/* Spacer */}
      <div className="flex-1" />
      {/* Qwen VLM toggle */}
      <label className="flex items-center gap-1.5 text-xs text-slate-500 mr-2 cursor-pointer select-none" title="Use Qwen VLM to correct wrong materials/colors from images (slower but more accurate)">
        <input
          type="checkbox"
          checked={useQwen}
          onChange={(e) => setUseQwen(e.target.checked)}
          className="w-3.5 h-3.5 rounded border-slate-300 text-purple-600 focus:ring-purple-500"
        />
        Use Qwen VLM
      </label>
      {/* Revalidate */}
      <Button onClick={revalidate} disabled={revalidating} size="sm" className="h-8 text-xs" variant="outline">
        {revalidating ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
        Revalidate
      </Button>
      {/* Dismiss All Selected */}
      <Button onClick={() => {
        setDismissedChecks((prev) => {
          const next = new Map(prev);
          for (const sku of selectedSkus) {
            const existing = next.get(sku) || new Set<number>();
            const result = results.find((r) => r.sku === sku);
            if (result) {
              const nextSet = new Set(existing);
              result.checks.forEach((_, ci) => nextSet.add(ci));
              next.set(sku, nextSet);
            }
          }
          return next;
        });
        setResults((prev) => {
          const updated = prev.map((r) => {
            if (!selectedSkus.has(r.sku)) return r;
            const modified = r.checks.map((c) => ({
              ...c, passed: true, decision: "PASS" as Decision, message: c.message + " (dismissed by user)",
            }));
            return { ...r, checks: modified, decision: "PASS" as Decision, score: 10 };
          });
          _currentResults.current = updated;
          return updated;
        });
      }} size="sm" className="h-8 text-xs bg-slate-600 hover:bg-slate-700 text-white" disabled={selectedSkus.size === 0}>
        <CheckCircle2 className="w-3.5 h-3.5 mr-1" /> Dismiss All ({selectedSkus.size})
      </Button>
      {/* Export (only when all pass) */}
      <Button onClick={exportSheet} size="sm" className="h-8 text-xs bg-teal-600 hover:bg-teal-700" disabled={!allPass}>
        <Download className="w-3.5 h-3.5 mr-1" /> Export Sheet
      </Button>
      {/* ImageGen — generate overlay images with specs */}
      <Button onClick={imageGenSheet} size="sm" className="h-8 text-xs bg-orange-600 hover:bg-orange-700 text-white" disabled={results.length === 0}>
        <Image className="w-3.5 h-3.5 mr-1" /> ImageGen
      </Button>
      {/* Visual Verify — CLIP flagging + Qwen correction */}
      <Button onClick={visualVerifySheet} size="sm" className="h-8 text-xs bg-purple-600 hover:bg-purple-700 text-white" disabled={results.length === 0}>
        <Eye className="w-3.5 h-3.5 mr-1" /> Visual Verify
      </Button>
    </div>
  );

  // ── Render: Upload phase ────────────────────────────────────────────────
  if (phase === "upload") {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-8">
          <div className="flex items-center gap-4 mb-8">
            <Link href="/"><Button variant="ghost" size="icon" className="rounded-full"><ArrowLeft className="w-5 h-5" /></Button></Link>
            <div>
              <h1 className="text-3xl font-bold text-slate-900">Pre-Listing Validator</h1>
              <p className="text-slate-500 mt-1">Upload a Gajab Hub export and validate product listings before upload</p>
            </div>
          </div>
          <div className="mb-6 flex flex-wrap items-center gap-2 bg-teal-50 border border-teal-200 rounded-lg px-3 py-2">
            <Server className="w-4 h-4 text-teal-600 shrink-0" />
            <span className="text-xs font-medium text-teal-800 shrink-0">Compute API</span>
            <input
              value={apiUrl}
              onChange={(e) => updateApiUrl(e.target.value)}
              onBlur={() => setPrelistingApiBase(apiUrl)}
              placeholder="https://your-tunnel.trycloudflare.com"
              className="flex-1 min-w-0 bg-white border border-teal-200 rounded px-2 py-1 text-xs text-slate-700 focus:outline-none focus:ring-1 focus:ring-teal-400"
            />
            <button
              onClick={testApiConnection}
              disabled={apiTest === "testing"}
              className="shrink-0 inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded border border-teal-300 text-teal-700 bg-white hover:bg-teal-100 disabled:opacity-60"
            >
              {apiTest === "testing" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {apiTest === "testing" ? "Testing…" : "Test"}
            </button>
            {apiTest === "ok" && (
              <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-medium text-green-700 bg-green-50 border border-green-200 rounded px-1.5 py-0.5">
                <CheckCircle2 className="w-3 h-3" /> {apiTestDetail || "Connected"}
              </span>
            )}
            {apiTest === "fail" && (
              <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-medium text-red-700 bg-red-50 border border-red-200 rounded px-1.5 py-0.5" title={apiTestDetail}>
                <AlertTriangle className="w-3 h-3" /> Not reachable
              </span>
            )}
            <span className="text-[10px] text-teal-600 shrink-0 hidden lg:inline">paste your tunnel URL → Test (saved in browser)</span>
          </div>
          <Card className="border-2 border-dashed border-slate-300 bg-white/50">
            <CardContent className="p-12">
              <div
                className={`border-2 border-dashed rounded-lg p-12 text-center transition-colors cursor-pointer ${
                  isDragOver ? "border-teal-500 bg-teal-50" : "border-slate-300 hover:border-teal-400"
                }`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
              >
                <input type="file" accept=".xlsx,.xls,.csv" onChange={(e) => { if (e.target.files?.[0]) handleFileSelect(e.target.files[0]); }} className="hidden" id="plv-file-upload" />
                <label htmlFor="plv-file-upload" className="cursor-pointer flex flex-col items-center gap-3">
                  <Upload className="w-14 h-14 text-teal-500" />
                  <span className="text-lg font-semibold text-slate-700">Drop your Gajab Hub export here or click to browse</span>
                  <span className="text-sm text-slate-500">Supports .xlsx, .xls, and .csv files</span>
                </label>
              </div>
              {file && !parseError && fileName && (
                <div className="mt-6 flex flex-col items-center gap-3">
                  <div className="flex items-center gap-3 text-slate-700">
                    <FileSpreadsheet className="w-8 h-8 text-teal-600" />
                    <div>
                      <p className="font-semibold">{fileName}</p>
                      <p className="text-sm text-slate-500">{rows.length} product{rows.length !== 1 ? "s" : ""} found · {headers.length} columns</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 mt-2">
                    <Button onClick={findSheetDuplicates} size="lg" variant="outline" className="border-rose-300 text-rose-700 hover:bg-rose-50" disabled={rows.length < 2}>
                      <Copy className="w-4 h-4 mr-2" /> Find Duplicates
                    </Button>
                    <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none" title="Use Qwen VLM to extract accurate specs from product images (slower but more accurate)">
                      <input
                        type="checkbox"
                        checked={useQwen}
                        onChange={(e) => setUseQwen(e.target.checked)}
                        className="w-3.5 h-3.5 rounded border-slate-300 text-purple-600 focus:ring-purple-500"
                      />
                      Use Qwen VLM
                    </label>
                    <Button onClick={startValidation} className="bg-teal-600 hover:bg-teal-700" size="lg">
                      <Play className="w-4 h-4 mr-2" /> Validate {rows.length} Products
                    </Button>
                  </div>
                  {sellerList.length > 0 && (
                    <div className="mt-2 relative w-full max-w-lg">
                      <button
                        type="button"
                        onClick={() => setSellerDropdownOpen(!sellerDropdownOpen)}
                        className="w-full flex items-center justify-between px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm text-slate-700 hover:border-slate-300"
                      >
                        <span>
                          {selectedSellers.size === 0
                            ? `All sellers (${sellerList.length})`
                            : `Selected ${selectedSellers.size}/${sellerList.length} sellers`}
                        </span>
                        <span className="text-xs text-slate-400">{sellerDropdownOpen ? "\u25B2" : "\u25BC"}</span>
                      </button>
                      {sellerDropdownOpen && (
                        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-60 overflow-y-auto">
                          <div className="flex items-center justify-between px-3 py-1.5 border-b border-slate-100 sticky top-0 bg-white">
                            <button
                              onClick={() => {
                                if (selectedSellers.size === sellerList.length) {
                                  setSelectedSellers(new Set());
                                } else {
                                  setSelectedSellers(new Set(sellerList.map((s) => s.name)));
                                }
                              }}
                              className="text-xs text-teal-600 hover:text-teal-700 font-medium"
                            >
                              {selectedSellers.size === sellerList.length ? "Deselect All" : "Select All"}
                            </button>
                          </div>
                          {sellerList.map((s) => (
                            <label
                              key={s.name}
                              className="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-sm"
                            >
                              <input
                                type="checkbox"
                                checked={selectedSellers.has(s.name)}
                                onChange={() => {
                                  setSelectedSellers((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(s.name)) next.delete(s.name);
                                    else next.add(s.name);
                                    return next;
                                  });
                                }}
                                className="w-3.5 h-3.5 rounded border-slate-300 text-rose-600 focus:ring-rose-500"
                              />
                              <span className="flex-1 text-slate-700 truncate">{s.name}</span>
                              <span className="text-xs text-slate-400">{s.count}</span>
                            </label>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {duplicateStatus && (
                    <div className="mt-2 p-2 bg-rose-50 border border-rose-200 rounded text-sm text-rose-700 max-w-lg text-center">
                      {duplicateStatus}
                      <button onClick={() => setDuplicateStatus(null)} className="ml-2 text-rose-500 hover:text-rose-700 font-bold">&times;</button>
                    </div>
                  )}
                  {duplicateGroups.length > 0 && (
                    <div className="mt-2 w-full max-w-lg">
                      <div className="flex items-center justify-between mb-1">
                        <h4 className="font-semibold text-sm text-slate-700">Duplicate Groups</h4>
                        <div className="flex items-center gap-2">
                          {duplicateRemoveSkus.size > 0 && (
                            <Button size="sm" variant="outline" className="h-7 text-xs border-rose-300 text-rose-700 hover:bg-rose-50"
                              onClick={() => {
                                duplicateRemoveSkus.forEach((sku) => _allRemovedSkus.current.add(sku));
                                setRows((prev) => prev.filter((row) => {
                                  const sku = getSku(row);
                                  return !duplicateRemoveSkus.has(sku);
                                }));
                                setDuplicateStatus(`Removed ${duplicateRemoveSkus.size} duplicate${duplicateRemoveSkus.size !== 1 ? "s" : ""}. Export the sheet to save changes.`);
                                setDuplicateGroups([]);
                              }}>
                              Remove {duplicateRemoveSkus.size} Duplicate{duplicateRemoveSkus.size !== 1 ? "s" : ""}
                            </Button>
                          )}
                          <Button size="sm" variant="outline" className="h-7 text-xs border-rose-300 text-rose-700 hover:bg-rose-50"
                            onClick={exportRemovedSkuSheet}>
                            <Download className="w-3 h-3 mr-1" /> Export SKU Sheet (Delete)
                          </Button>
                          <Button size="sm" variant="outline" className="h-7 text-xs border-teal-300 text-teal-700 hover:bg-teal-50"
                            onClick={exportSheetNoDuplicates} disabled={!file}>
                            <Download className="w-3 h-3 mr-1" /> Export Excel (No Duplicates)
                          </Button>
                        </div>
                      </div>
                      <div className="space-y-1 max-h-40 overflow-y-auto text-xs">
                        {duplicateGroups.map((g, gi) => (
                          <div key={gi} className="p-2 bg-slate-50 border border-slate-200 rounded">
                            <div className="font-medium text-slate-600">Group {gi + 1} — {g.match_type === "all_images_match" ? "ALL images match" : `${g.matched_images}/${g.total_images} images match`}</div>
                            <div className="mt-1 space-y-0.5">
                              <div className="text-green-700">KEEP: {g.keep?.sku} — {g.keep?.title?.substring(0, 40)}</div>
                              {g.remove?.map((p: any) => (
                                <div key={p.sku} className="text-rose-600">REMOVE: {p.sku} — {p.title?.substring(0, 40)}</div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {duplicateGroups.length === 0 && _allRemovedSkus.current.size > 0 && (
                    <div className="mt-2 w-full max-w-lg">
                      <div className="flex items-center justify-between mb-1">
                        <h4 className="font-semibold text-sm text-slate-700">Duplicates Removed</h4>
                        <div className="flex items-center gap-2">
                          <Button size="sm" variant="outline" className="h-7 text-xs border-rose-300 text-rose-700 hover:bg-rose-50"
                            onClick={exportRemovedSkuSheet}>
                            <Download className="w-3 h-3 mr-1" /> Export SKU Sheet (Delete)
                          </Button>
                          <Button size="sm" variant="outline" className="h-7 text-xs border-teal-300 text-teal-700 hover:bg-teal-50"
                            onClick={exportSheetNoDuplicates} disabled={!file}>
                            <Download className="w-3 h-3 mr-1" /> Export Excel (No Duplicates)
                          </Button>
                        </div>
                      </div>
                      <p className="text-xs text-slate-500">{_allRemovedSkus.current.size} duplicate{_allRemovedSkus.current.size !== 1 ? "s" : ""} excluded from export. Click export to download the deduplicated sheet.</p>
                    </div>
                  )}
                </div>
              )}
              {file && parseError && (
                <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">{parseError}</div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  // ── Render: Validating phase ────────────────────────────────────────────
  if (phase === "validating") {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
        <div className="max-w-4xl mx-auto px-6 py-8">
          <div className="flex items-center gap-4 mb-6">
            <div>
              <h1 className="text-3xl font-bold text-slate-900">Validating Products</h1>
              <p className="text-slate-500 mt-1">{fileName} · {rows.length} products</p>
            </div>
            {stageElapsed && (
              <div className="ml-auto text-sm text-slate-500">
                Completed in <span className="font-mono text-slate-700">{stageElapsed}</span>
              </div>
            )}
          </div>

          {/* Progress bar */}
          <Card className="mb-4">
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2 text-sm text-slate-600">
                  {progress < 100 ? <Loader2 className="w-4 h-4 text-teal-600 animate-spin" /> :
                   <CheckCircle2 className="w-4 h-4 text-green-600" />}
                  <span className="font-medium">{stageLabel}</span>
                </div>
                <span className="text-sm font-mono text-slate-500">{progress}%</span>
              </div>
              <div className="w-full bg-slate-200 rounded-full h-3">
                <div
                  className="bg-gradient-to-r from-teal-500 to-emerald-500 h-3 rounded-full transition-all duration-500 ease-out"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </CardContent>
          </Card>

          {/* Step indicators */}
          <div className="grid grid-cols-4 gap-2 mb-4">
            {stepLogs.map((step, i) => (
              <div key={i} className={`px-3 py-2 rounded-lg border text-xs transition-colors ${
                step.running ? "border-teal-400 bg-teal-50 text-teal-700" :
                step.done ? "border-green-300 bg-green-50 text-green-700" :
                "border-slate-200 bg-white text-slate-400"
              }`}>
                <div className="flex items-center gap-1.5 font-semibold">
                  {step.running ? <Loader2 className="w-3 h-3 animate-spin" /> :
                   step.done ? <CheckCircle2 className="w-3 h-3" /> :
                   <div className="w-3 h-3 rounded-full border-2 border-slate-300" />}
                  {i + 1}. {step.label}
                </div>
                {step.summary && <div className="mt-0.5 opacity-75">{step.summary}</div>}
              </div>
            ))}
          </div>

          {/* Live log */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Live Log</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div ref={logRef} className="bg-slate-900 text-green-400 font-mono text-xs rounded-b-lg p-4 max-h-[50vh] overflow-y-auto space-y-1">
                {logs.length === 0 && progress < 5 && (
                  <div className="text-slate-500">Waiting for validation to start...</div>
                )}
                {logs.map((log, i) => {
                  const isInfo = log.sku === "SYS";
                  const dot = isInfo ? "[SYS]" :
                    log.result.decision === "PASS" ? "[OK]" :
                    log.result.decision === "FLAG" ? "[FLAG]" : "[FAIL]";
                  const dotColor = isInfo ? "text-sky-300" :
                    log.result.decision === "PASS" ? "text-green-400" :
                    log.result.decision === "FLAG" ? "text-amber-400" : "text-red-400";
                  return (
                    <div key={i} className="leading-relaxed">
                      <span className="text-slate-500">[{log.time}]</span>{" "}
                      {!isInfo && <span className="text-cyan-400">Row {log.row}</span>}
                      {!isInfo && " — "}
                      <span className={isInfo ? "text-sky-200" : "text-white"}>
                        "{log.productName.length > 140 ? log.productName.substring(0, 140) + "…" : log.productName}"
                      </span>{" "}
                      {!isInfo && <span className="text-slate-500">({log.sku})</span>}
                      {" "}<span className={dotColor}>{dot}</span>
                    </div>
                  );
                })}
                {progress < 100 && (
                  <div className="flex items-center gap-2 text-slate-500 pt-1 border-t border-slate-700">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    {stageLabel}...
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }  // ── Render: Review phase ────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center gap-4 mb-4">
          <Link href="/"><Button variant="ghost" size="icon" className="rounded-full"><ArrowLeft className="w-5 h-5" /></Button></Link>
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Validation Review</h1>
            <p className="text-slate-500 text-sm">{fileName} · {results.length} products</p>
          </div>
          <div className="flex-1" />
          <Button onClick={() => { setPhase("upload"); setFile(null); setRows([]); setResults([]); setLogs([]); setHsnSuggestions(new Map()); setHsnSelections(new Map()); setSelectedSkus(new Set()); setTextCorrections(new Map()); setDismissedChecks(new Map()); _appliedCorrections.current = new Map(); }} variant="outline" size="sm">
            New Validation
          </Button>
        </div>

        {/* Step results summary */}
        <div className="grid grid-cols-3 gap-3 mb-6">
          {stepLogs.map((step, i) => (
            <div key={i} className={`px-4 py-3 rounded-lg border text-sm ${step.done ? "bg-green-50 border-green-200" : "bg-slate-50 border-slate-200"}`}>
              <div className="flex items-center gap-2 font-semibold text-slate-700">
                {step.done ? <CheckCircle2 className="w-4 h-4 text-green-600" /> : <div className="w-4 h-4" />}
                Step {i + 1}: {step.label}
              </div>
              {step.summary && <div className="text-xs text-slate-500 mt-0.5">{step.summary}</div>}
            </div>
          ))}
        </div>

        {/* Stats */}
        <div className="grid grid-cols-4 gap-4 mb-4">
          {[
            { label: "Pass", value: stats.pass, color: "green", Icon: CheckCircle2 },
            { label: "Flag", value: stats.flag, color: "amber", Icon: AlertTriangle },
            { label: "Reject", value: stats.reject, color: "red", Icon: XCircle },
            { label: "Total", value: stats.total, color: "slate", Icon: FileSpreadsheet },
          ].map(({ label, value, color, Icon }) => (
            <Card key={label} className={`border-${color}-200 bg-${color}-50`}>
              <CardContent className="p-4 flex items-center gap-3">
                <Icon className={`w-8 h-8 text-${color}-600`} />
                <div>
                  <div className={`text-2xl font-bold text-${color}-700`}>{value}</div>
                  <div className={`text-xs text-${color}-600 uppercase font-semibold`}>{label}</div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Toolbar */}
        <Toolbar />

        {/* Export status */}
        {exportStatus && (
          <div className={`mb-4 p-3 rounded-lg text-sm flex items-center gap-2 ${exportStatus.startsWith("Export failed") || exportStatus.startsWith("Export error")
            ? "bg-red-50 border border-red-200 text-red-700"
            : "bg-teal-50 border border-teal-200 text-teal-700"}`}>
            {exportStatus.startsWith("Export failed") || exportStatus.startsWith("Export error")
              ? <AlertTriangle className="w-4 h-4 shrink-0" />
              : <CheckCircle2 className="w-4 h-4 shrink-0" />}
            {exportStatus}
            <button onClick={() => setExportStatus(null)} className="ml-auto text-current opacity-60 hover:opacity-100">×</button>
          </div>
        )}

        {imageGenStatus && (
          <div className="mb-4 p-3 bg-orange-50 border border-orange-200 rounded-lg text-orange-700 text-sm flex items-center gap-2">
            <Image className="w-4 h-4 shrink-0" />
            {imageGenStatus}
            <button onClick={() => setImageGenStatus(null)} className="ml-auto text-orange-500 hover:text-orange-700">×</button>
          </div>
        )}

        {visualVerifyStatus && (
          <div className="mb-4 p-3 bg-purple-50 border border-purple-200 rounded-lg text-purple-700 text-sm flex items-center gap-2">
            <Eye className="w-4 h-4 shrink-0" />
            {visualVerifyStatus}
            <button onClick={() => setVisualVerifyStatus(null)} className="ml-auto text-purple-500 hover:text-purple-700">×</button>
          </div>
        )}

        {visualVerifyResults.size > 0 && (
          <div className="mb-4 p-3 bg-white border rounded-lg">
            <h4 className="font-semibold text-sm mb-2">Visual Verification Results</h4>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {Array.from(visualVerifyResults.entries()).map(([sku, r]) => (
                <div key={sku} className="text-xs border-b pb-2">
                  <div className="font-medium">{sku}</div>
                  {r.flagged?.length > 0 && (
                    <div className="text-amber-600 mt-1">
                      Flagged: {r.flagged.map((f: any) => `${f.type}="${f.stated}" (CLIP: ${f.clip_top2?.join(", ")})`).join("; ")}
                    </div>
                  )}
                  {r.corrections?.length > 0 && (
                    <div className="text-green-600 mt-1">
                      Corrected: {r.corrections.map((c: any) => `${c.original}→${c.observed} (${c.confidence})`).join("; ")}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* All-pass banner */}
        {allPass && (
          <div className="mb-4 p-4 bg-green-50 border border-green-300 rounded-lg flex items-center gap-3">
            <CheckCircle2 className="w-6 h-6 text-green-600" />
            <div>
              <p className="font-semibold text-green-800">All {stats.total} products passed validation!</p>
              <p className="text-sm text-green-600">Review the corrections applied, then export the corrected sheet.</p>
            </div>
            <div className="flex-1" />
            <Button onClick={exportSheet} className="bg-green-600 hover:bg-green-700" size="lg">
              <Download className="w-4 h-4 mr-2" /> Export Now
            </Button>
          </div>
        )}

        {/* Product table */}
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-slate-50">
                    <th className="text-left p-3 font-semibold text-slate-600 w-8">
                      <button onClick={toggleAll} className="hover:opacity-70" title="Toggle all">
                        {selectedSkus.size === results.length ? "☑" : selectedSkus.size > 0 ? "☒" : "☐"}
                      </button>
                    </th>
                    <th className="text-left p-3 font-semibold text-slate-600 w-8" />
                    <th className="text-left p-3 font-semibold text-slate-600">Sku</th>
                    <th className="text-left p-3 font-semibold text-slate-600">Product Name</th>
                    <th className="text-left p-3 font-semibold text-slate-600">Category</th>
                    <th className="text-left p-3 font-semibold text-slate-600">Decision</th>
                    <th className="text-center p-3 font-semibold text-slate-600 w-16">Score</th>
                    <th className="text-center p-3 font-semibold text-slate-600 w-20">Issues</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((result, idx) => {
                    const issueCount = result.checks.filter((c) => !c.passed).length;
                    const isExpanded = expandedRows.has(idx);
                    return (
                      <>
                        <tr key={idx} className="border-b hover:bg-slate-50 cursor-pointer transition-colors" onClick={() => toggleExpand(idx)}>
                          <td className="p-3">
                            <button onClick={(e) => { e.stopPropagation(); toggleSku(result.sku); }} className="hover:opacity-70">
                              {selectedSkus.has(result.sku) ? "☑" : "☐"}
                            </button>
                          </td>
                          <td className="p-3">{isExpanded ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}</td>
                          <td className="p-3 font-mono text-xs">{result.sku}</td>
                          <td className="p-3 max-w-xs">
                          <div className="flex items-center gap-1">
                            <div className="truncate">{stripHtml(result.productName)}</div>
                            {(result.titleSuggestion || result.descSuggestion) && (
                              <span className="shrink-0 text-xs text-amber-600 bg-amber-100 px-1.5 py-0.5 rounded-full font-medium" title="Agent-suggested corrections available">
                                ✎
                              </span>
                            )}
                          </div>
                        </td>
                          <td className="p-3 text-xs text-slate-500 max-w-[180px] truncate">{result.category}</td>
                          <td className="p-3">{decisionBadge(result.decision)}</td>
                          <td className="p-3 text-center">
                            <span className={`inline-flex items-center justify-center w-8 h-8 rounded-full text-xs font-bold ${
                              result.score >= 8 ? "bg-green-100 text-green-700" :
                              result.score >= 5 ? "bg-amber-100 text-amber-700" :
                              "bg-red-100 text-red-700"
                            }`}>{result.score}</span>
                          </td>
                          <td className="p-3 text-center">
                            <span className={`text-sm font-medium ${
                              issueCount === 0 ? "text-green-600" : issueCount <= 2 ? "text-amber-600" : "text-red-600"
                            }`}>{issueCount}</span>
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr key={`${idx}-detail`}>
                            <td colSpan={8} className="bg-slate-50 p-4">
                              <div className="space-y-2">
                                {result.checks.map((check, ci) => (
                                  <div key={ci} className={`flex items-start gap-2 text-xs p-2 rounded ${
                                    check.passed ? "bg-green-50 text-green-700" :
                                    check.decision === "REJECT" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700"
                                  }`}>
                                    {check.passed ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" /> :
                                     check.decision === "REJECT" ? <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> :
                                     <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
                                    <div className="flex-1"><span className="font-semibold">{check.check}</span>{" "}<span className="opacity-75">{check.message}</span></div>
                                    {!check.passed && (
                                      <button
                                        onClick={(e) => { e.stopPropagation(); dismissCheck(result.sku, ci); }}
                                        className="ml-2 p-0.5 rounded hover:bg-red-100 shrink-0"
                                        title="Dismiss this flag"
                                      >
                                        <XCircle className="w-4 h-4 text-red-500 hover:text-red-700" />
                                      </button>
                                    )}
                                  </div>
                                ))}
                                {/* HSN suggestions */}
                                {hsnSuggestions.has(result.sku) && (
                                  <div className="mt-3">
                                    <p className="text-xs font-semibold text-slate-500 mb-2">HSN Suggestions{hsnAlgorithmVersion ? ` · ${hsnAlgorithmVersion}` : ""}</p>
                                    {hsnVisualContext.get(result.sku) && <p className="text-[11px] text-slate-400 mb-2">{hsnVisualContext.get(result.sku)}</p>}
                                    <div className="space-y-1.5">
                                      {hsnSuggestions.get(result.sku)!.slice(0, 4).map((s, si) => {
                                        const applied = hsnSelections.get(result.sku)?.hsn === s.hsn.split(",")[0].trim();
                                        return (
                                          <button
                                            key={si}
                                            onClick={() => {
                                              const hsn = s.hsn.split(",")[0].trim();
                                              if (/^\d{4}$|^\d{6}$|^\d{8}$/.test(hsn)) setHsnSelections((prev) => new Map(prev).set(result.sku, { hsn, tax: String(s.gst_rate) }));
                                            }}
                                            className={`w-full text-left flex items-start gap-2 text-xs p-2 rounded border transition-colors ${
                                              applied ? "bg-green-100 border-green-400 text-green-800" : "bg-white border-slate-200 text-slate-700 hover:border-teal-400 hover:bg-teal-50"
                                            }`}
                                          >
                                            {applied ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" /> : <span className="w-3.5 h-3.5 shrink-0 inline-block" />}
                                            <div className="flex-1">
                                              <span className="font-mono font-semibold">{s.hsn}</span>{" "}
                                              <span className="text-slate-500">GST {s.gst_rate}%</span>{" "}
                                              <span className="opacity-70">({(s.confidence * 100).toFixed(0)}%)</span>
                                              <div className="opacity-75">{s.description}</div>
                                            </div>
                                          </button>
                                        );
                                      })}
                                    </div>
                                  </div>
                                )}
                                {/* Visual Verify corrections — y/n validation */}
                                {visualVerifyResults.has(result.sku) && (() => {
                                  const vr = visualVerifyResults.get(result.sku)!;
                                  const corrections = vr.corrections || [];
                                  const flagged = vr.flagged || [];
                                  if (corrections.length === 0 && flagged.length === 0) return null;
                                  return (
                                    <div className="mt-3">
                                      <p className="text-xs font-semibold text-purple-600 mb-2">
                                        Visual Verification{corrections.length > 0 ? " Corrections" : " Flags"}
                                      </p>
                                      {/* Show corrections with y/n */}
                                      {corrections.map((c: any, ci: number) => {
                                        const key = `${result.sku}:${c.attribute}:${ci}`;
                                        const feedback = correctionFeedback.get(key);
                                        return (
                                          <div key={ci} className={`flex items-center gap-2 text-xs p-2 rounded mb-1 ${
                                            feedback === true ? "bg-green-50 text-green-700" :
                                            feedback === false ? "bg-red-50 text-red-700" :
                                            "bg-purple-50 text-purple-700"
                                          }`}>
                                            <span className="font-semibold capitalize">{c.attribute}:</span>
                                            <span className="line-through opacity-60">{c.original}</span>
                                            <span>→</span>
                                            <span className="font-medium">{c.observed}</span>
                                            <span className="text-[10px] opacity-50">({c.confidence})</span>
                                            <div className="ml-auto flex gap-1">
                                              <button
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  setCorrectionFeedback((prev) => new Map(prev).set(key, true));
                                                  fetch("/api/products/validate-correction", {
                                                    method: "POST",
                                                    headers: { "Content-Type": "application/json" },
                                                    body: JSON.stringify({ sku: result.sku, attribute: c.attribute, original: c.original, observed: c.observed, isCorrect: true }),
                                                  }).catch(() => {});
                                                }}
                                                className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                                  feedback === true ? "bg-green-500 text-white" : "bg-white border border-green-300 text-green-600 hover:bg-green-100"
                                                }`}
                                              >Y</button>
                                              <button
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  setCorrectionFeedback((prev) => new Map(prev).set(key, false));
                                                  const actual = prompt(`Qwen said "${c.observed}" but it's wrong. What is the actual ${c.attribute}?`);
                                                  fetch("/api/products/validate-correction", {
                                                    method: "POST",
                                                    headers: { "Content-Type": "application/json" },
                                                    body: JSON.stringify({ sku: result.sku, attribute: c.attribute, original: c.original, observed: c.observed, isCorrect: false, actualValue: actual || undefined }),
                                                  }).catch(() => {});
                                                }}
                                                className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                                  feedback === false ? "bg-red-500 text-white" : "bg-white border border-red-300 text-red-600 hover:bg-red-100"
                                                }`}
                                              >N</button>
                                            </div>
                                          </div>
                                        );
                                      })}
                                      {/* Show flagged without corrections */}
                                      {flagged.filter((f: any) => !corrections.find((c: any) => c.attribute === f.type)).map((f: any, fi: number) => (
                                        <div key={`f${fi}`} className="text-xs p-2 rounded mb-1 bg-amber-50 text-amber-700">
                                          <span className="font-semibold capitalize">{f.type}:</span> "{f.stated}" not confirmed by CLIP (top: {f.clip_top2?.join(", ")})
                                        </div>
                                      ))}
                                    </div>
                                  );
                                })()}
                                {/* Title/Desc suggestions */}
                                {(result.titleSuggestion || result.descSuggestion) && (
                                  <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                                    <p className="text-xs font-semibold text-amber-700 mb-2">Suggested Corrections</p>
                                    <div className="space-y-2">
                                        {result.titleSuggestion && (
                                        <div className="flex items-start justify-between gap-2">
                                          <div className="flex-1"><p className="text-xs font-mono text-slate-600 bg-white p-1.5 rounded border">{result.titleSuggestion}</p></div>
                                          <Button size="sm" variant="outline" className="h-7 text-xs border-amber-500 text-amber-700 hover:bg-amber-100 shrink-0"
                                            onClick={() => {
                                              const t = result.titleSuggestion!;
                                              setTextCorrections((prev) => { const next = new Map(prev); next.set(result.sku, { ...next.get(result.sku), title: t }); return next; });
                                              _appliedCorrections.current.set(result.sku, { ..._appliedCorrections.current.get(result.sku), title: t });
                                              setRows((prevRows) => prevRows.map((row) => getSku(row) === result.sku ? { ...row, [_findCol(row, "Product Name *", "Product Name") || "Product Name *"]: t } : row));
                                              // Clear suggestion from results and show feedback
                                              setResults((prev) => prev.map((r) => r.sku === result.sku ? { ...r, titleSuggestion: undefined } : r));
                                              setLogs((prev) => [...prev, { time: new Date().toLocaleTimeString("en-US", { hour12: false }), row: 0, sku: result.sku, productName: `✓ TITLE APPLIED: "${t}"`, result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] } }]);
                                            }}>
                                            Apply
                                          </Button>
                                        </div>
                                        )}
                                      {result.descSuggestion && (
                                        <div className="flex items-start justify-between gap-2">
                                          <div className="flex-1"><p className="text-xs text-slate-500 bg-white p-1.5 rounded border whitespace-pre-wrap">{result.descSuggestion}</p></div>
                                          <Button size="sm" variant="outline" className="h-7 text-xs border-amber-500 text-amber-700 hover:bg-amber-100 shrink-0"
                                            onClick={() => {
                                              const d = result.descSuggestion!;
                                              setTextCorrections((prev) => { const next = new Map(prev); next.set(result.sku, { ...next.get(result.sku), description: d }); return next; });
                                              _appliedCorrections.current.set(result.sku, { ..._appliedCorrections.current.get(result.sku), description: d });
                                              setRows((prevRows) => prevRows.map((row) => getSku(row) === result.sku ? { ...row, [_findCol(row, "Description *", "Description") || "Description *"]: d } : row));
                                              // Clear suggestion from results and show feedback
                                              setResults((prev) => prev.map((r) => r.sku === result.sku ? { ...r, descSuggestion: undefined } : r));
                                              setLogs((prev) => [...prev, { time: new Date().toLocaleTimeString("en-US", { hour12: false }), row: 0, sku: result.sku, productName: `✓ DESC APPLIED`, result: { sku: "", productName: "", category: "", decision: "PASS", score: 0, checks: [] } }]);
                                            }}>
                                            Apply
                                          </Button>
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                )}
                                {/* Image thumbnails */}
                                {result.imageChecks && result.imageChecks.length > 0 && (
                                  <div className="mt-3">
                                    <div className="flex items-center justify-between mb-2">
                                      <p className="text-xs font-semibold text-slate-500">Image Details</p>
                                      {result.imageChecks.some((ic) => ic.isDuplicate) && (
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            // Dismiss every duplicate-image RULE 2 check for this product
                                            // so the product passes and export is unlocked. buildImageCorrections
                                            // already removes isDuplicate images when writing the sheet.
                                            result.checks.forEach((c, ci) => {
                                              if (
                                                (c.check === "RULE 2: Duplicate images" ||
                                                 c.check === "RULE 2: Exact URL duplicates" ||
                                                 c.check === "RULE 2: Perceptual duplicates" ||
                                                 c.check === "RULE 2: Distinct images") &&
                                                !c.passed
                                              ) {
                                                dismissCheck(result.sku, ci);
                                              }
                                            });
                                            setExportStatus(`Duplicate images for ${result.sku} will be removed on export.`);
                                            setTimeout(() => setExportStatus(null), 4000);
                                          }}
                                          className="text-[11px] px-2 py-1 rounded border border-amber-300 text-amber-700 hover:bg-amber-50 font-medium"
                                        >
                                          Remove duplicate images
                                        </button>
                                      )}
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                      {result.imageChecks.map((ic, ii) => (
                                        <div key={ii} className={`border rounded p-1 ${
                                          ic.hasOpsClaim ? "border-red-400 bg-red-50" :
                                          ic.isDuplicate ? "border-amber-300 bg-amber-50" :
                                          "border-green-300 bg-green-50"
                                        }`}>
                                          <img src={ic.url} alt="" className="w-20 h-20 object-cover rounded" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                                          <div className="text-[10px] text-center mt-1">
                                            {ic.width > 0 ? `${ic.width}x${ic.height}` : ""}
                                            {ic.isDuplicate && <span className="text-amber-600 block">Duplicate</span>}
                                            {ic.hasOpsClaim && <span className="text-red-600 block">Ops claim: {ic.ocrText}</span>}
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* Validation log in review */}
        {logs.length > 0 && (
          <Card className="mt-6">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Validation Log</CardTitle>
            </CardHeader>
            <CardContent>
              <div ref={logRef} className="bg-slate-900 text-green-400 font-mono text-xs rounded-lg p-4 max-h-96 overflow-y-auto space-y-1">
                {logs.map((log, i) => {
                  const issues = log.result.checks?.filter((c) => !c.passed);
                  const brief = issues?.slice(0, 2).map((c) => c.message).join("; ");
                  const message = log.productName || log.result.productName || "Validation event";
                  const isEvent = !log.result.productName && !log.result.checks?.length;
                  const dot = isEvent ? "[INFO]" : log.result.decision === "PASS" ? "[PASS]" : log.result.decision === "FLAG" ? "[FLAG]" : "[REJCT]";
                  return (
                    <div key={i} className="leading-relaxed">
                      <span className="text-slate-500">[{log.time}]</span>{" "}
                      <span className="text-cyan-400">{log.sku === "BATCH" ? "BATCH" : `Row ${log.row}`}</span> —{" "}
                      <span className="text-white">
                        "{message.length > 120 ? message.substring(0, 120) + "…" : message}"
                      </span>{" "}
                      <span className="text-slate-500">(SKU: {log.sku})</span>{" "}
                      <span className={isEvent ? "text-sky-300" : log.result.decision === "REJECT" ? "text-red-400" : log.result.decision === "FLAG" ? "text-amber-400" : "text-green-400"}>
                        {dot}
                      </span>{" "}
                      {brief && <span className="text-slate-500">— {brief}</span>}
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Bottom revalidate/export bar */}
        {stats.total > 0 && !allPass && (
          <div className="flex items-center gap-3 mt-6">
            <Button onClick={revalidate} disabled={revalidating} className="bg-teal-600 hover:bg-teal-700">
              {revalidating ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-2" />}
              Revalidate Corrections
            </Button>
            <p className="text-sm text-slate-500">
              {stats.flag + stats.reject} product{stats.flag + stats.reject !== 1 ? "s" : ""} still need attention. Apply corrections above, then revalidate.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
