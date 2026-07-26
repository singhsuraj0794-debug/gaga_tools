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

### Happy-flow steps (no login required)
1. home_load → 2. category_load → 3. product_detail_load → 4. bargain_flow

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
