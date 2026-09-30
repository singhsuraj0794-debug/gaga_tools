import { useEffect, useState } from "react";
import { Layers, AlertTriangle, CheckCircle2 } from "lucide-react";
import { API_BASE } from "@/lib/api";
import { withApiHeaders } from "@/lib/listingValidator";

/** One category line returned by `/api/scraper/<platform>/category-summary`. */
export interface CategorySummaryData {
  total_products: number;
  total_categories: number;
  categories: {
    category: string;
    sheet_name: string;
    products: number;
    attribute_columns: number;
  }[];
}

/**
 * Asks the API how many categories a scraped file contains, before the export
 * runs, so the user can see how many sheets the workbook will get.
 * Returns null while loading or when the request fails (summary is advisory —
 * it must never block the export itself).
 */
export function useCategorySummary(platform: string, products: unknown[]): CategorySummaryData | null {
  const [summary, setSummary] = useState<CategorySummaryData | null>(null);
  const count = products.length;

  useEffect(() => {
    if (count === 0) {
      setSummary(null);
      return;
    }
    let cancelled = false;
    const t = window.setTimeout(async () => {
      try {
        const resp = await fetch(`${API_BASE}/api/scraper/${platform}/category-summary`, {
          ...withApiHeaders({
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ products, platform }),
          }),
        });
        if (!resp.ok) return;
        const data = (await resp.json()) as CategorySummaryData;
        if (!cancelled) setSummary(data);
      } catch {
        /* advisory only */
      }
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [platform, count, products]);

  return summary;
}

/** "This file has N categories → N sheets" panel shown above the export button. */
export function CategorySummary({ summary }: { summary: CategorySummaryData | null }) {
  if (!summary || summary.total_products === 0) return null;

  const sheets = summary.categories.length;
  const unmapped = summary.categories.filter(
    (c) => c.category === "(unmapped)" || !c.category,
  );

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm dark:border-blue-900 dark:bg-blue-950/40">
      <div className="flex items-center gap-2 font-semibold text-blue-800 dark:text-blue-200">
        <Layers className="h-4 w-4 shrink-0" />
        {sheets} {sheets === 1 ? "category" : "categories"} found in {summary.total_products}{" "}
        products → {sheets} {sheets === 1 ? "sheet" : "sheets"} on export
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {summary.categories.map((c) => (
          <span
            key={c.category + c.sheet_name}
            className={
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs " +
              (c.category === "(unmapped)"
                ? "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200"
                : "bg-white text-slate-700 ring-1 ring-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700")
            }
            title={c.category === "(unmapped)" ? "No category mapped — these rows stay flagged" : c.category}
          >
            {c.category === "(unmapped)" ? (
              <AlertTriangle className="h-3 w-3" />
            ) : (
              <CheckCircle2 className="h-3 w-3" />
            )}
            {c.sheet_name} · {c.products}
            <span className="opacity-60">({c.attribute_columns} attrs)</span>
          </span>
        ))}
      </div>

      {unmapped.length > 0 && (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
          {unmapped.reduce((n, c) => n + c.products, 0)} product(s) have no mapped category —
          they go to the "Unmapped" sheet with a blank Category Name and an audit note.
        </p>
      )}
    </div>
  );
}
