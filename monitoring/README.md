# gajab.com Synthetic Monitoring

End-user experience monitoring for gajab.com: Lighthouse audits, Core Web Vitals,
and automated happy-flow checks with Twilio OTP login.

## Architecture

```
run_monitor.py          ← entrypoint (runs hourly via cron / GitHub Actions)
├── lighthouse_audit.py  ← Lighthouse CLI audits + PageSpeed Insights API
├── happy_flow.py        ← Playwright journey + Twilio OTP polling
├── supabase_client.py   ← Stores results in Supabase
├── slack_alert.py       ← Posts failures to Slack
├── config.py            ← All thresholds in one place
└── dashboard/           ← Web UI dashboard (served from Supabase or static hosting)
```

## Prerequisites

- Python 3.10+
- Node.js (for the `lighthouse` CLI — `npm install -g lighthouse`)
- Playwright browsers (`playwright install chromium`)
- Twilio account with a phone number capable of receiving SMS
- Supabase project
- Slack incoming webhook URL

## Setup

```bash
# 1. Install Python dependencies
pip install -r monitoring/requirements.txt

# 2. Install Lighthouse CLI
npm install -g lighthouse

# 3. Install Playwright browser
playwright install chromium

# 4. Configure environment
cp monitoring/.env.example monitoring/.env
# Edit monitoring/.env with your credentials

# 5. Run the database migration
#   - Open your Supabase SQL Editor
#   - Run the contents of monitoring/supabase_migration.sql

# 6. Run the monitor
cd monitoring && python run_monitor.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase anon/service key |
| `SLACK_WEBHOOK_URL` | Yes | Slack incoming webhook URL |
| `TWILIO_SID` | Yes | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Yes | Twilio auth token |
| `MONITOR_PHONE` | Yes | Phone number registered on gajab.com (e.g. +9198XXXXXXXX) |
| `PAGESPEED_API_KEY` | No | PageSpeed Insights API key (for field data) |

All thresholds and time budgets are configurable via env vars — see `.env.example`.

## What It Checks

### Lighthouse Audits (every run)

Pages: home, category listing, product detail

Metrics: Performance Score, LCP, CLS, TBT, SI

Field data (once daily via PageSpeed Insights API): LCP, CLS, FID

### Happy-Flow Check (every run)

1. Load home page
2. Browse to category listing
3. View product detail
4. Login via phone + OTP (Twilio polling, ~3s intervals, 30s timeout)
5. Open bargain flow & start bargaining
6. Set offer price & submit
7. Add to cart
8. Navigate to checkout (stop before payment)

Each step asserts: element visible, no time budget violations.

## Alerting

On failure or threshold breach, posts to Slack with:
- Which page/step failed
- The metric that tripped the alert
- OTP delivery failures are reported distinctly from step failures

## Dashboard

Open `monitoring/dashboard/index.html` in a browser.

For local development, pass Supabase credentials as query params:
```
index.html?supabaseUrl=https://xxx.supabase.co&supabaseKey=xxx
```

Or deploy the dashboard folder to any static host (Vercel, Netlify, Supabase Storage).

## Adding a New Page/Flow to Monitor

1. Add the URL to `URLS` dict in `config.py`
2. Add any new time budgets to `TIME_BUDGETS_SECONDS`
3. Add the audit call in `lighthouse_audit.py:audit_all_pages()`
4. For a new flow step, add it in `happy_flow.py:run_happy_flow()`
5. Thresholds are managed in `config.py:THRESHOLDS`

## Scheduling

### GitHub Actions (recommended)

The `.github/workflows/monitor.yml` workflow runs hourly.
Set the required env vars as repository secrets.

### Cron (alternative)

```cron
0 * * * * cd /path/to/monitoring && /usr/bin/python3 run_monitor.py >> /var/log/monitor.log 2>&1
```
