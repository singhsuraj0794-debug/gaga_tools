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


