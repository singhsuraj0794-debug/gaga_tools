# Project Index

## Synthetic Monitoring (`monitoring/`)

End-user experience monitoring for gajab.com.

### Structure
- `run_monitor.py` — Entrypoint: runs all checks, writes to Supabase, alerts Slack
- `config.py` — All thresholds, URLs, time budgets in one place
- `server_health.py` — Pings API server, gateway, and gajab.com for uptime + latency
- `api_monitor.py` — Tests key API endpoints (healthz, products/status, price-mappings)
- `lighthouse_audit.py` — Lighthouse CLI audits + PageSpeed Insights API
- `happy_flow.py` — Playwright journey (home → category → PDP → bargain flow)
- `feature_checks.py` — Element-level UI checks (buttons, banners, search bar, product grid, filters, PDP elements)
- `supabase_client.py` — Writes to Supabase `monitoring_runs` table
- `slack_alert.py` — Posts failures/degradations to Slack webhook
- `dashboard/index.html` — Standalone dashboard
- `supabase_migration.sql` — SQL for `monitoring_runs` table

### Pages monitored
- Home: `https://gajab.com/`
- Category: `https://gajab.com/product-list/all`
- Product detail: `https://gajab.com/product-detail/prestige-pvc-80-veggie-cutter-with-3-stainless-steel-blades-jumbo-bowl-black/4305598878914`

### Happy-flow steps (session persisted via Supabase Storage)
1. home_load → 2. category_load → 3. random_product → 4. bargain_flow (slider + offer) → 5. checkout_nav + Pay + Razorpay + UPI → 6. my_bargains + alerts_orders + banners → 7. search_products

### Session persistence
- One-time login via `monitoring/setup_login.py` (OTP from Indian mobile)
- Session saved to `monitoring/.gajab_session.json` and uploaded to Supabase Storage
- Each run auto-loads session — no OTP needed
- Session is 573KB (too large for GitHub Secrets), stored in Supabase Storage bucket `monitoring/gajab_session.json`
- Re-login when session expires by re-running setup_login.py

### Credentials (in `monitoring/.env`, gitignored)
- Twilio SID: `AC...` (in .env)
- Twilio Auth: `...` (in .env)
- Monitor phone: `+1...` (in .env)
- Supabase: `https://okxyskmjsmtykblrtmyi.supabase.co` (anon key in .env)
- Slack webhook: `https://hooks.slack.com/services/...` (in .env)

### Frontend
- Monitoring Dashboard at `/monitoring` route in video-finder React app
- Requires `VITE_SUPABASE_URL` and `VITE_SUPABASE_KEY` in `artifacts/video-finder/.env`

### Automation
- GitHub Actions: `.github/workflows/monitor.yml` (hourly cron)
- GitHub secrets required: SUPABASE_URL, SUPABASE_KEY, TWILIO_SID, TWILIO_AUTH_TOKEN, MONITOR_PHONE, SLACK_WEBHOOK_URL

### OTP login (not currently active)
- gajab.com login expects 10-digit Indian mobile number
- Twilio monitoring number is US-based (+1), so login flow is disabled
- Helper functions (`_poll_otp`, `_get_twilio_messages`, `_extract_otp`) preserved for future use

### Run locally
```bash
cd monitoring
python3 run_monitor.py
```

## HSN Suggestion (`HSN/` + api-server)

Embedding-based semantic search that maps a scraped product title/description to the closest entries in the GST 2.0 Rate Notification (Notification No. 09/2025), suggesting HSN codes + GST rate. Zero-shot: no training data — the notification itself is the knowledge base.

### Structure
- `HSN/GST_2.0_Rate_Notification_English.docx` — source notification (7 schedules)
- `HSN/parse_hsn.py` — parses the docx into structured JSON (code, description, schedule, central tax, GST rate). Run: `python3 parse_hsn.py GST_2.0_Rate_Notification_English.docx hsn_entries.json`
- `HSN/hsn_entries.json` — 1,195 parsed entries (also copied into `artifacts/api-server/` for deployment)
- `artifacts/api-server/_hsn_suggest.py` — the ML service: embeds all entries with `sentence-transformers/all-MiniLM-L6-v2` (cached to `.hsn_embeddings.npy`), embeds the product query, returns top-5 by cosine similarity. Uses a `SYNONYMS`/`STOPWORDS` query-expansion lexicon (retail → tariff language).
- Endpoint: `POST /api/products/hsn-suggest` — body `{ "products": [{ "sku", "title", "description" }] }`; returns `{ results: [{ sku, query, suggestions: [{ hsn, description, gst_rate, central_tax, schedule, confidence, rank }], topGstRate, topSchedule }] }`

### Notes
- First call downloads the model (~9s) and embeds the 1,195-entry corpus (cached to `.hsn_embeddings.npy` next to the script; delete to rebuild). Subsequent calls reuse the cache.
- `build.mjs` copies `_hsn_suggest.py` + `hsn_entries.json` into `dist/`.
- Known limitation: exact 6/8-digit HSN can be imprecise for generic apparel (tends to match fabric/caps chapters), but the GST rate is generally correct. Treat suggestions as human-verifiable candidates, not ground truth.

## Category Validation (R&D Lab)

Validates a product's assigned **L1–L4** category (from a Gajab Hub / SKU mapping sheet) using the Marqo ecommerce image model, and recommends the correct taxonomy path when it's wrong. Source of truth is the L1–L4 DB workbook. Page: `/rd/category-validation` (R&D Lab card in `Home.tsx`).

**Goal:** per product, say whether the assigned L1–L4 is `correct`, and if not, the correct full DB path (`recommended`). Marqo is the base classifier; low-confidence rows are escalated to a local **Qwen2.5-VL** re-ranker over a constrained candidate shortlist.

### Structure
- `All Categories.xlsx` (Downloads) — **source of truth** (single `Categories` sheet): Category ID, L1–L4, Full Path, Level, Sort Order, Status, Commission, Bargain %, Slug → **18 L1, 150 L2, 623 L3, 2,402 L4 paths** (with ids + Active/Inactive status)
- `artifacts/api-server/_build_category_taxonomy.py` — builds `category_l1l4.json` from the workbook. Run: `python3 _build_category_taxonomy.py "All Categories.xlsx" category_l1l4.json`
- `artifacts/api-server/category_l1l4.json` — normalized taxonomy (v2.0): `l1`, `l2`/`l3`/`l4` child maps keyed `"L1 || L2"`, `ids`/`status` by full path, and `paths[]` (L4 leaves with full path + id + status)
- `artifacts/api-server/_category_validate.py` — the service: fetches product images from the Gajab gateway (`productImage[]` → `https://resize.gajab.com/{container}{file}`), encodes them once with Marqo, classifies hierarchically, and emits a `verdict` + `recommended` path + a focused `shortlist` for escalation.
- `artifacts/api-server/_category_keywords.py` — curated **keyword → exact taxonomy path** map (60 entries, high-precision product nouns). A safe way to use the title (raw title-vs-path text blending was removed — it scored "PET Fridge Bottle Set" → "Stretch Film" at 84%). Targets are validated against `category_l1l4.json` at load; audit with `python3 _category_keywords.py`.
- `artifacts/api-server/_category_qwen.py` — Qwen2.5-VL (local MLX 4-bit, shares `_qwen_model`) **constrained re-rank**: given the product image + title and a shortlist of real DB paths, it returns the best one (or none). ~0.8s/generation once the model is warm. Fixes Marqo's shape-vs-function errors.
- `artifacts/api-server/_marqo_classifier.py` — Marqo (`hf-hub:Marqo/marqo-ecommerce-embeddings-B`) via OpenCLIP; `encode_images` + `classify_features` + `classify_scores_text`, with **label-embedding caches** (in-process and on-disk `.marqo_label_emb.pt`)
- Endpoints (`src/routes/products/index.ts`, via the analysis server proxy):
  - `POST /api/products/category-validate` — batch (fits the ~100s tunnel limit)
  - `POST /api/products/category-validate/job` → `{ jobId }` — background job for large sheets
  - `GET /api/products/category-validate/job/:jobId/progress|results`
- Frontend: `artifacts/video-finder/src/pages/CategoryValidator.tsx` + `runCategoryValidation()` / `startCategoryJob()` / `getCategoryJob()` in `lib/listingValidator.ts`; route in `App.tsx`

### Algorithm
1. Images from the sheet, else fetched from the product URL (gateway, ~0.3s/product).
2. Marqo encodes images ONCE, and encodes the product **title** once into the same embedding space (`encode_text_query`).
3. **Title+image fusion (v1.2, PRIMARY SIGNAL)**: every scoring call goes through `_fused_top` = `w_img·image + w_path·text(full path) + w_leaf·text(bare L4 leaf)`, weights summing to 1 (defaults **0.4 / 0.2 / 0.4**, env `CATEGORY_TITLE_WEIGHT` = total title weight `0.6`, `CATEGORY_LEAF_WEIGHT` = leaf share `0.4`; image gets the remainder). Title names the function, image only shows shape — the image-only model called a *"Radnal Garbage Dustbin 12L Cream"* **"Diaper Disposal Bins"** at 0.99 (same lidded-bin shape). The **bare-leaf term** exists because full-path labels dilute short titles: `"All-In-One Sewing Kit"` scores **0.0000** against its own path `… > Tailoring & Embroidery > Sewing Kit` but **0.674** against `… > Home Appliances > Sewing Machines` (path noise words hijack the embedding) — yet the bare leaf `Sewing Kit` scores **1.000 vs 0.000**. The image keeps 40% as a guard because a title-only blend once matched `"PET Fridge Bottle Set"` → `"Stretch Film"` at 84%. Bare-leaf scores are computed once per product over ~2,400 unique L4 names (cached embeddings) and passed only to the global top-25 call; L3/L4 sibling re-scores already use bare-name labels. The older `_leaf_tiebreak` helper is gone (dead code).
4. **Top level (L1/L2/L3)**: fused top-25 against the ~2,399 full-path strings (**generic catch-all leaves excluded** — `gemstone`, `Coins`, `Combo`, etc. stay valid assignment targets but are never classification candidates), sum score mass per L1 / per `L1 || L2` / per `L1 || L2 || L3` prefix (a single best path is unstable — hundreds of siblings split the softmax mass). A fresh 2-way softmax among L3 siblings flipped jewelry rows (`Precious Jewellery` 0.95 vs the correct `Artificial & Silver Jewellery` 0.05) because re-normalizing over a tiny set throws away the global evidence — hence prefix-mass down to L3, with fresh fused sibling re-score only as fallback when top-25 has no coverage.
5. **Keyword anchor**: match the title against the curated map. When it hits, its L1/L2/L3 (and L4, if the entry specifies one) become a **strong anchor** that overrides BOTH fusion signals (a sipper bottle image otherwise reads as "Confetti Streamers"; *"Rabbit Ear Headband"* reads as **Garden Stake** — party-prop shapes + "ear"→Ear Muffs text — and *"All-In-One Sewing Kit"* reads as **Sewing Machines** on path noise). The fused scorer still picks L4 among the anchored L3's siblings when the entry has no L4. 77 entries, high-precision product nouns only; longest keyword wins (so `makeup headband` → Face Care's Makeup Headband, plain `headband` → Hair Accessories).
6. **L4**: re-score only among the chosen L3's siblings (catch-alls filtered; if the L3 has no concrete leaf left, back off to the best sibling L3 that does). Fused title+image (was image-only).
7. **Focused shortlist** for escalation: predicted path + its L4 siblings, keyword-anchor path + siblings, assigned path (if valid) + siblings, then the global fused top picks as filler (≤30).
8. **Marqo is the source of truth.** Qwen no longer overrides `recommended`/`predicted`/`status` (it kept picking generic catch-alls like `Precious Jewellery > gemstone`); its escalation block was removed from `_analysis_server.py`. The shortlist is still computed but advisory only.

### Verdict (primary output)
- `correct` — assigned path is valid and matches the model (L1–L4).
- `incorrect` — assigned path is valid but the model disagrees with high confidence at **any level incl. L4** (real error). `recommended` = predicted path.
- `review` — low confidence (<0.50 at L1/L2), OR the assigned leaf/path is invalid. `recommended` = best valid DB path. **No false "incorrect"** for e.g. `... > Bottles` (not a valid L4).
- `no-images` / `unclassified` / `error`.
- `recommended` is ALWAYS a path that exists in the taxonomy.

### Performance (important)
- Encoding the ~2,400 path labels with the text encoder costs **90–200s on CPU**; cached to **`.marqo_label_emb.pt`** (gitignored, rebuildable). A fresh one-shot process loads it in <1s (131s → 0.08s measured).
- Marqo image encoding is the real per-product bottleneck: **~1–2.5s/product** (Marqo/OpenCLIP **crashes on MPS**, so it is CPU-only).
- **The 66–132s single-product stall**: `resize.gajab.com` intermittently 502s and the shared HTTP session (`_clip_verify._get_http_session`) used `Retry(total=6, backoff_factor=1.0)` → ~62s of exponential backoff sleeping per dead image. Category validation now downloads through a **fast session** (`fast=True`: 1 retry, backoff 0.2, 6s timeout) with a **thread pool inside `encode_images`** — worst-case per-product time went 132s → ~2.6s.
- Products are processed in a **thread pool** (`CATEGORY_WORKERS`, default 4, max 8) — threads, not processes, so Marqo stays single-instance (torch CPU ops release the GIL; image fetch is I/O).
- **Result cache**: completed verdicts are cached in-process keyed by (url, title, assignment, images, taxonomy+algorithm version) — a re-run of the same sheet returns in ~0s. Errors are never cached.
- **Warm-up**: `_analysis_server.py` loads Marqo + taxonomy + label cache at startup (`ANALYSIS_NO_WARM=1` to skip), so the first job no longer pays the ~5–15s cold load.
- Measured (64-row jewellery sheet, server warm): full job **~85s**; identical second job **~0s**.
- Qwen re-rank: no longer used for category validation (Marqo authoritative).
- Persistent analysis server (`_analysis_server.py`, `/category-validate`): jobs run in a background thread with `progress.json`/`results.json` under `category_jobs/` (gitignored).
- `build.mjs` copies the Python scripts + `category_l1l4.json` into `dist/`. Note: the API reads `ANALYSIS_PORT` (default 8003) to reach the analysis server.

### Notes / limitations
- **Invalid L4 assignments are common** in real sheets (e.g. `... > Bottles`, `... > Container`); they route to `review`, not a false `incorrect`.
- The image alone reads shape, not function — the title fusion (0.6) now carries function, and the keyword anchor still overrides both for common nouns/errors; genuinely novel products remain human-verifiable candidates.
- Extend accuracy by adding high-precision, unambiguous keywords to `KEYWORD_MAP` (reuse a path that exists in `category_l1l4.json`; the self-check enforces this).

## Meta Mind Map / Hook Analyzer (Trend Finder)

VidCognition-style pre-publish ad analyzer. Upload/paste an Instagram (or
YouTube/TikTok/Facebook) reel and get **Hook (0–3s) / Bridge (3–7s) / Offer
(7–15s)** scores 0–100, drop-off annotations, and a strategist critique with
fixes — backed by Meta's **TRIBE v2** fMRI brain-encoding model (shown as a
secondary "brain response panel"). Powers the `/trend-finder` page.

### Structure
- `artifacts/video-finder/src/pages/TrendFinder.tsx` — the page (input + card view + Mind Map detail)
- `artifacts/api-server/src/routes/trend/index.ts` — API: analyze (upload or URL via yt-dlp), SSE progress, result, media, frames
- `artifacts/api-server/_tribe_analyze.py` — pipeline CLI: trim → TRIBE predict → features → score → render cortical frames → `result.json`/`progress.json`
- `artifacts/api-server/_tribe_regions.py` — Destrieux-atlas region groups + "works" score + flags
- `artifacts/api-server/benchmark_seeds.json` — seed Instagram reel URLs for the benchmark corpus
- `artifacts/api-server/_tribe_features.py` — per-second video features (scene cuts, motion, brightness, contrast, faces via **YuNet DNN**, people via **HOG**, on-screen text via **Tesseract OCR**, audio RMS) → concrete observations used to make the critique specific. Requires `opencv-python-headless==4.10.0.84`, `pytesseract` + `brew install tesseract`, and the YuNet model at `~/tribe-v2/cache/face_detection_yunet.onnx`.

### Scoring
- Per-moment dimension scores (Destrieux ROIs) → absolute engagement blend → **Hook/Bridge/Offer window scores (0–100)** + overall.
- **Drop-offs**: consecutive moments where engagement falls >15%.
- **Tailored breakdown**: each weak/drop moment is cross-referenced with the per-second video features (`_tribe_features.py`) so feedback is concrete and timestamped (e.g. *"at 2s a hard cut lands on a static, faceless, dark shot"*) with a matching fix — not generic ROI advice.
- **Critique**: rule-based heuristic from `_tribe_regions.py`, then enriched by **Groq** (`GROQ_API_KEY`, model `GROQ_MODEL` default `openai/gpt-oss-120b`) into issues/fixes/rewrite ideas grounded in those observations. The feature breakdown is preserved and merged with Groq's; falls back to the heuristic if Groq is unavailable.
- **Brain functions**: `brainFunctions[]` maps the 7 predicted networks to plain-language meaning + hook role, marks each **engaged / switched off**, and gives the concrete reason a function is off (from the video features).
- **Comparison**: `GET /api/trend/benchmark` aggregates every completed job's scores (read from `trend_finder/*/result.json`) into percentiles so each reel is ranked against the local corpus. This is a *relative* ranking — there is no external labelled-retention benchmark.
- **Benchmark corpus**: successful public reels are queued via `POST /api/trend/benchmark/ingest` (or `/seed`) and analysed **sequentially in the background** (jobs tagged `kind: "benchmark"`, capped at `BENCHMARK_MAX_SECONDS` default 5). Each reel's meta (`likeCount`, `commentCount`, `successScore`) is stored in `trend_finder/<jobId>/meta.json` and reloaded on startup. Instagram discovery is **manual** (no public search API) — paste reel URLs.
- **Instagram needs cookies**: yt-dlp uses the logged-in browser via `YT_DLP_COOKIES_FROM_BROWSER` (default `chrome`) or `YT_DLP_COOKIES` (path to cookies.txt). Without them IG returns "login required". Success is ranked by likes + 3×comments (views aren't exposed by IG).

### Endpoints
- `POST /api/trend/analyze` — multipart `file` OR JSON `{ url, title }` → `{ jobId }`
- `GET /api/trend/jobs` — recent jobs (history/card view)
- `GET /api/trend/benchmark` — percentile stats (benchmark corpus + local) + `top[]` performers
- `GET /api/trend/benchmark/status` — ingest queue + corpus items
- `POST /api/trend/benchmark/ingest` — body `{ urls: string[] }` → queue reels for background analysis
- `POST /api/trend/benchmark/seed` — queue the URLs in `benchmark_seeds.json`
- `GET /api/trend/analyze/:jobId` — status + result
- `GET /api/trend/analyze/:jobId/progress` — SSE
- `GET /api/trend/media/:jobId` — the analysed clip (mp4, Range)
- `GET /api/trend/frames/:jobId/:name` — rendered brain PNG

### Local TRIBE venv (required — Python 3.11, kept OUTSIDE the repo)
The API server's system `python3` (3.9) cannot run TRIBE. It invokes a dedicated
venv via `TRIBE_PYTHON` (default `~/tribe-v2/venv/bin/python`). Setup:
```bash
python3.11 -m venv ~/tribe-v2/venv
~/tribe-v2/venv/bin/pip install torch==2.6.0 torchvision==0.21.0
git clone --depth 1 https://github.com/facebookresearch/tribev2 ~/tribe-v2/src
cd ~/tribe-v2/src && ~/tribe-v2/venv/bin/pip install -e ".[plotting]"
~/tribe-v2/venv/bin/pip install "transformers>=4.53,<5"
# weights (public, no token needed) — exclude the huge redundant files:
~/tribe-v2/venv/bin/hf download facebook/vjepa2-vitg-fpc64-256 --exclude "original/*"
~/tribe-v2/venv/bin/hf download facebook/w2v-bert-2.0 --exclude "*.pt"
```

### Notes / limitations
- **Runs on CPU only.** MPS is not supported by `neuralset` and crashed the 16 GB Mac (OOM). `--device mps` is refused.
- **No HF token needed:** the text encoder (gated LLaMA-3.2-3B) auto-drops when there are no word events, so we run **video + audio** only (`audio_only=True`).
- **Slow:** ~2.5–4 min per timestep (1 Hz). Clips are capped at `TRIBE_MAX_SECONDS` (default 8) → jobs run ~20–30 min. The UI polls and shows progress.
- Env overrides: `TRIBE_PYTHON`, `TRIBE_MAX_SECONDS`, `TRIBE_CACHE` (default `~/tribe-v2/cache`).
- Runtime artifacts land in `artifacts/api-server/trend_finder/<jobId>/` (gitignored).
- TRIBE v2 is **CC BY-NC 4.0** — internal R&D only.

## Amazon <-> Gajab Cross-Reference (`scripts/amazon_crossref.py`)

Standalone local script: for every row of an uploaded Amazon sheet, decide whether
the same product already exists in our catalogue (`All_products.xlsx`) and export
an Excel report. Powers the "does this Amazon product exist in our DB + who is the
Amazon seller" workflow. No API server, no Supabase.

### Usage
```bash
python3 scripts/amazon_crossref.py \
  --amazon "~/Downloads/IN_AMAZON_blackBoxProducts_Backpacks.xlsx" \
  --gajab  "~/Downloads/All_products.xlsx" \
  --out    "~/Downloads/amazon_crossref.xlsx"
```
Flags: `--threshold` (Exists cutoff, default 85), `--workers` (image-pass threads, 8),
`--limit` (test N rows), `--no-images` (text pass only), `--image-models dino,clip,siglip`
(default all), `--visual-cos`/`--visual-text` (re-shot visual-match tier), `--no-visual-match`
(disable it), `--no-store-links` (skip scraping real Amazon seller store links and use a
seller search link instead). Store links are scraped per unique seller by default and cached
in `scripts/.crossref_cache/seller_links.json`.

### Source of truth
`All_products.xlsx` (`SKU New Cat Mapping`): `Product Name`, `Sku Number`, `Seller Name`,
`Product URL`, `L1`–`L4`. It has **no image/description columns**, so Gajab images are
pulled on demand from the storefront gateway (`gatewayservice.gajab.com/.../product-detail/<slug>/<id>`,
then `resize.gajab.com`) and cached to `scripts/.crossref_cache/gajab_images.json`.
Duplicate product names (same title across variant SKUs — ~10k of 30k rows) are collapsed
to one representative.

### Algorithm
1. **Amazon loader** auto-detects columns case-insensitively (`Title`, `Seller`, `URL`,
   `Image URL`/`Image 1..10`, `ASIN`, `Brand`, `Category`) and picks the sheet whose header
   holds a title column, so both the blackbox and scraper sheet shapes work.
2. **Text pass**: normalize titles (lower, strip punctuation/units/stopwords, singularize,
   dedupe); exact normalized-title match → confidence 100; else block via a token inverted
   index and rank top-12 by token-set/bigram Jaccard + partial ratio + SequenceMatcher +
   brand-first-token bonus.
3. **Image pass (uncertain rows only)**: fetch the Amazon image + the top-12 Gajab
   candidates' images. Score with **pHash Hamming** plus three semantic vision models —
   **DINOv2-small** (structural), **CLIP ViT-B/32** and **SigLIP base** (semantic).
   `image_score = 0.30·phash_sim + 0.70·(0.5·semantic_mean + 0.5·semantic_min)`; the
   `min` term requires *all* models to agree, which keeps precision high. Fuse
   `0.6·text + 0.4·image`. Two visual tiers override the fused score:
   **Image Duplicate** — near-identical file (min cosine ≥ 0.90, pHash ≤ 12, text ≥ 15) →
   forced ≥ 92; and **Image+Title** — the same product *re-photographed* (min cosine ≥ 0.85,
   pHash ≤ 35, text ≥ 45) → forced ≥ 86. `--image-models dino,clip,siglip` selects a subset
   (default all); the per-model cosines, semantic min and pHash distance are exported as
   columns for tuning.
4. **Verdict**: `Exists` (≥ threshold), `Review` (55–threshold), `Not Found` (<55); plus a
   `Review Needed` flag. Matched Gajab SKU / title / category / seller / URL are reported.

### Output workbook (3 sheets)
- **Results** — Amazon columns + `Match Status` (green/red/amber), `Review Needed`,
  `Match Confidence`, `Match Type`, `Matched Gajab SKU/Product Name/Category/Seller/URL`,
  `Amazon Seller Name`, `Amazon Seller Store Link`, `Text Score`, `Image Score`.
- **Summary** — counts (Exists/Not Found/Review), unique sellers, params.
- **Candidates** — top-12 Gajab candidates per Amazon row (audit trail).

### Notes / performance
- ~500 Amazon rows × 30k Gajab products, full image pass: **~7 min** (image fetch is the
  bottleneck; core ties to the gateway). Cache warm-up makes re-runs much faster.
- **"100% match" = exact normalized title (confidence 100).** Everything else is a
  confidence score. The Amazon sheet carries UPC/GTIN/EAN but `All_products.xlsx` has no
  barcode column, so matching is title+image only — treat `Review` rows as human-verifiable.
- No single model is discriminative enough alone (DINOv2-small: different same-category
  products ~0.5–0.7 cosine; CLIP/SigLIP are semantic and even less exact). Fusing all three
  with a `min` term means a "visual duplicate" needs unanimous agreement, so different
  products score low (measured image score ~40–48 on negatives vs 100 on identical images).
  Identical supplier images are the strongest signal. Enable the `rapidfuzz` package for
  faster text scoring (optional; pure-Python fallback used otherwise).
- Vision weights (~90 MB DINOv2 + ~600 MB CLIP + ~800 MB SigLIP) download on first use and
  are cached by HuggingFace; `--image-models` limits which load, and any model that fails to
  load is dropped automatically.

### Local upload UI (`scripts/crossref_server.py`)
A tiny stdlib web server (no build step) around the script. **Bulk-upload multiple
Amazon sheets at once**; they run **one by one** (a global batch lock serialises whole
batches) and each finished sheet appears in its own tab with a preview and its own Excel
download — while the remaining sheets keep running. Per-sheet **Cancel** (kills the running
subprocess, or drops a queued sheet) and **Retry** controls; a **Cancel all** button stops the
whole batch. Exports are named `<sheet>_amazon_gajab_match_<YYYYMMDD_HHMMSS>.xlsx`
(per-sheet, so nothing mixes).
```bash
python3 scripts/crossref_server.py --port 8788   # then open http://localhost:8788
```
Endpoints: `GET /` (tabbed page), `GET /api/config`, `POST /api/match` (multipart, repeat
`file` for several sheets) → `{ batchId, sheets:[{id,name}] }`, `GET /api/batch/<id>`
(per-sheet status/summary), `GET /api/jobs/<id>` (status + log + progress),
`GET /api/preview/<id>` (first 200 rows), `GET /api/download/<id>` (xlsx),
`POST /api/cancel/<id>`, `POST /api/retry/<id>`, `POST /api/cancel-batch/<batchId>`. Each
sheet runs `amazon_crossref.py` as a subprocess; job files land in `scripts/.crossref_jobs/<id>/`
(gitignored). Note: this is a standalone tool, separate from the `artifacts/video-finder`
React app (which has no page for it).


