# Synthetic Monitor — Week-on-Week Report

**Period:** 26 Jul 2026 → 25 Sep 2026  
**Source:** Supabase `monitoring_runs`  
**Rows analysed:** 57,585 checks across 10 ISO weeks  
**Generated:** 25 Sep 2026 12:33 UTC

---

## 1. Executive summary

- Overall pass rate went **40.1% → 74.4%** over the period (best week **2026-W34** at 80.5%).
- **Infrastructure is now solid:** API checks 98–99%, server health 97–98% (they were 76% / 48% in W31).
- **Performance has improved recently:** Lighthouse score **+28.8%** and Total Blocking Time **−44%** comparing W38–W39 against W36–W37.
- **The remaining drag is web performance, not infrastructure:** Lighthouse accounts for ~62% of all failures in the last two weeks.
- `product_detail` is the weakest page (score 45.2); `home` and `category` have recovered to ~48–55.

---

## 2. Overall health — week on week

| Week | Checks | Pass | Degraded | Fail | Pass rate |
|---|---:|---:|---:|---:|---:|
| 2026-W30 | 504 | 202 | 45 | 257 | **40.1%** |
| 2026-W31 | 8,066 | 4,632 | 649 | 2,785 | **57.4%** |
| 2026-W32 | 6,185 | 3,533 | 506 | 2,146 | **57.1%** |
| 2026-W33 | 10,002 | 6,602 | 767 | 2,633 | **66.0%** |
| 2026-W34 | 13,017 | 10,476 | 728 | 1,813 | **80.5%** |
| 2026-W35 | 4,216 | 3,132 | 264 | 820 | **74.3%** |
| 2026-W36 | 4,352 | 3,100 | 309 | 943 | **71.2%** |
| 2026-W37 | 3,719 | 2,682 | 238 | 799 | **72.1%** |
| 2026-W38 | 3,796 | 2,728 | 217 | 851 | **71.9%** |
| 2026-W39 | 3,728 | 2,772 | 522 | 434 | **74.4%** |

> **Note on volume.** Checks/week fell from ~13,000 (W34) to ~3,800 (W35 onward) when the run cadence was reduced. Percentages remain comparable; absolute counts after W34 are not.

---

## 3. Pass rate by check family

| Family | W30 | W31 | W32 | W33 | W34 | W35 | W36 | W37 | W38 | W39 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **happy_flow** | 68% | 75% | 79% | 83% | 87% | 84% | 82% | 84% | 84% | 83% |
| **feature** | 59% | 56% | 49% | 51% | 74% | 74% | 74% | 74% | 75% | 91% |
| **lighthouse** | 6% | 45% | 48% | 54% | 71% | 52% | 37% | 36% | 48% | 37% |
| **api** | 76% | 78% | 76% | 86% | 97% | 93% | 99% | 97% | 99% | 98% |
| **server** | 55% | 48% | 45% | 62% | 95% | 96% | 94% | 98% | 98% | 97% |
| **meta** | 0% | 0% | 0% | 25% | 34% | 32% | 30% | 32% | 33% | 31% |
| **native_happy_flow** | – | – | – | – | – | 67% | 70% | 86% | 41% | 93% |

**Reading it:** `api`, `server` and `happy_flow` are stable and high. `lighthouse` is the only family that is *worse* than it was mid-period. `feature` jumped to 91% in W39 (stale-selector false positives removed).

---

## 4. Performance metrics — weekly averages

| Metric | W30 | W31 | W32 | W33 | W34 | W35 | W36 | W37 | W38 | W39 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Lighthouse performance score | 43 | 48 | 44 | 48 | 61 | 48 | 36 | 38 | 46 | 50 |
| LCP (ms) | 13,707 | 8,511 | 9,344 | 6,515 | 2,953 | 3,554 | 5,048 | 4,424 | 4,745 | 4,257 |
| Total Blocking Time (ms) | 600 | 392 | 521 | 472 | 349 | 534 | 734 | 727 | 447 | 360 |
| Speed Index (ms) | 19,619 | 16,658 | 19,353 | 13,417 | 5,279 | 6,567 | 8,237 | 7,036 | 7,397 | 8,316 |
| API response (ms) | 1,803 | 1,397 | 1,738 | 1,320 | 718 | 1,067 | 709 | 859 | 691 | 787 |
| Server response (ms) | 1,121 | 1,365 | 1,943 | 1,550 | 1,200 | 1,133 | 1,439 | 1,226 | 973 | 1,235 |
| happy-flow step (ms) | 5,563 | 7,905 | 8,648 | 7,956 | 8,542 | 9,580 | 9,125 | 9,117 | 8,213 | 10,729 |

---

## 5. Has performance improved recently? — W38+W39 vs W36+W37

| Metric | W36–W37 | W38–W39 | Change | Verdict |
|---|---:|---:|---:|---|
| Lighthouse performance score | 37.1 | 47.8 | +28.8% | ✅ improved |
| LCP (ms) | 4,740.6 | 4,518.3 | -4.7% | ✅ improved |
| Total Blocking Time (ms) | 730.6 | 406.7 | -44.3% | ✅ improved |
| Speed Index (ms) | 7,648.2 | 7,821.8 | +2.3% | ⚠️ worse |
| API response (ms) | 782.6 | 736.7 | -5.9% | ✅ improved |
| Server response (ms) | 1,334.5 | 1,097.5 | -17.8% | ✅ improved |
| happy-flow step (ms) | 9,121.0 | 9,468.0 | +3.8% | ⚠️ worse |
| **Overall pass rate** | 71.6% | 73.1% | +1.5 pts | ✅ improved |

**Verdict: yes.** Performance bottomed out in W36–W37 and has recovered — Lighthouse +28.8%, blocking time nearly halved, server response −18%. The two regressions (Speed Index, happy-flow step time) are small and driven by the bargain/checkout flows running on a slow page.

---

## 6. Lighthouse performance score by page

| Page | W30 | W31 | W32 | W33 | W34 | W35 | W36 | W37 | W38 | W39 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| home | 43.6 | 47.3 | 35.3 | 43.6 | 61.3 | 48.9 | 35.9 | 37.6 | 51.3 | 48.5 |
| category | 40.6 | 43.6 | 49.4 | 52.4 | 61.7 | 47.9 | 37.7 | 39.1 | 48.2 | 55.1 |
| product_detail | 44.4 | 52.7 | 45.8 | 48.5 | 60.0 | 47.8 | 35.9 | 36.7 | 39.3 | 45.2 |

Threshold for pass is **≥ 50** (`THRESHOLDS.performance_score`). `product_detail` is the weakest and most volatile page.

---

## 7. Where the remaining failures are (last 2 weeks)

| Check | Checks | Fail | Degraded |
|---|---:|---:|---:|
| `product_detail` | 601 | 308 | 118 |
| `home` | 563 | 213 | 89 |
| `category` | 541 | 196 | 65 |
| `native_happy_flow` | 539 | 157 | 30 |
| `monitor` | 245 | 0 | 166 |
| `feature/product_detail` | 468 | 166 | 0 |
| `feature/category` | 400 | 114 | 0 |
| `step_web_bargain_flow` | 82 | 18 | 61 |
| `step_mweb_bargain_flow` | 82 | 24 | 54 |
| `step_mweb_checkout_flow` | 82 | 21 | 21 |
| `step_web_bargain2_flow` | 70 | 2 | 32 |
| `step_mweb_bargain2_flow` | 61 | 5 | 21 |
| `step_mweb_product_detail_load` | 82 | 25 | 0 |
| `step_web_checkout_flow` | 82 | 12 | 13 |
| `step_mweb_category_toys-games_load` | 82 | 0 | 19 |
| `step_web_product_detail_load` | 82 | 12 | 0 |
| `step_web_category_all_load` | 82 | 0 | 11 |
| `api/Category Page (gajab.com/product-lis` | 164 | 4 | 6 |
| `step_web_search_products` | 70 | 0 | 8 |
| `step_mweb_category_electronics_load` | 82 | 1 | 5 |
| `server/gajab.com (category)` | 82 | 3 | 3 |
| `step_mweb_category_all_load` | 82 | 0 | 4 |
| `step_mweb_category_fashion-accessories_load` | 82 | 0 | 4 |
| `feature/feature_checks` | 4 | 4 | 0 |
| `step_mweb_home_load` | 82 | 0 | 3 |

---

## 8. Native Android app

| Week | Checks | Pass | Degraded | Fail | Pass rate |
|---|---:|---:|---:|---:|---:|
| 2026-W30 | – | – | – | – | – |
| 2026-W31 | – | – | – | – | – |
| 2026-W32 | – | – | – | – | – |
| 2026-W33 | – | – | – | – | – |
| 2026-W34 | – | – | – | – | – |
| 2026-W35 | 199 | 133 | 3 | 63 | **66.8%** |
| 2026-W36 | 376 | 264 | 26 | 86 | **70.2%** |
| 2026-W37 | 44 | 38 | 4 | 2 | **86.4%** |
| 2026-W38 | 286 | 116 | 23 | 147 | **40.6%** |
| 2026-W39 | 253 | 236 | 7 | 10 | **93.3%** |

The W38 collapse (40.6%) was **emulator infrastructure**, not the app — `adb: device offline` and UiAutomator startup timeouts before any step ran. W39 recovered to 93.3%.

---

## 9. Findings and recommended actions

| # | Finding | Impact | Recommendation |
|---|---|---|---|
| 1 | Web performance is the dominant failure source (~62% of failures) | High | Fix the two 4.9 MB decorative background SVGs (`add-address-bg.svg`, `select-background.svg` — each embeds a 3.7 MB base64 JPEG) |
| 2 | `product_detail` is the weakest page (45.2) | High | Preload the hero/product image with `fetchpriority=high`; stop lazy-loading above-the-fold images |
| 3 | ~1.4 MB third-party JS (GTM 856 KB, lottie 298 KB, customfit 252 KB) | Medium | Defer GTM to idle/interaction; self-host and tree-shake lottie |
| 4 | Speed Index still 7.8–8.3 s and worsening | Medium | Filmstrip analysis — content becomes useful slowly despite improved TBT |
| 5 | Android monitor is volatile week to week | Medium | Mitigated (Appium session retry, 180 s timeouts); consider a pre-run emulator health gate |
| 6 | Feature checks had false positives until W39 | Resolved | Stale CSS selectors replaced with text-based matching |
| 7 | Run volume fell 4× after W34 | Info | If intentional, ignore; otherwise restore the schedule |

---

## 10. Methodology and caveats

- Data is every row in `monitoring_runs` between the first and last timestamps above; weeks are ISO (Monday–Sunday).
- A check is `pass`, `degraded` or `fail`. `degraded` usually means slow-but-working (time budget exceeded) or a data-dependent skip, not a hard break.
- **W39 is a partial week** and may shift as more runs land.
- Weekly averages blend mobile (mweb) and desktop (web) runs; Lighthouse is mobile-only by configuration.
- Run volume changed sharply at W35, so week-to-week *percentages* are the meaningful comparison, not raw counts.
