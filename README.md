# Product URL Scraper

Scrapes product details (title, description, image, dimensions) from **Flipkart**, **Meesho**, and **Amazon** product pages listed in a Google Sheet or local Excel/CSV file.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Google Sheets API credentials (optional)

If you want to read/write directly from a Google Sheet:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or select an existing one).
3. Enable **Google Sheets API** and **Google Drive API**.
4. Go to **Credentials** → **Create Credentials** → **Service Account**.
5. Download the JSON key file and save it as `credentials.json` in this project root (or set `GOOGLE_SHEET_CREDENTIALS` env var to the path).
6. Share your target Google Sheet with the service account email (found in the JSON file) as an **Editor**.

### 3. Environment variables (optional)

Copy `.env.example` or set these:

| Variable | Default | Description |
|---|---|---|
| `SCRAPER_MIN_DELAY` | `2.0` | Min seconds to wait between requests |
| `SCRAPER_MAX_DELAY` | `6.0` | Max seconds to wait between requests |
| `SCRAPER_MAX_RETRIES` | `2` | Number of retries per URL on failure |
| `SCRAPER_HEADLESS` | `true` | Run browser headless (`false` to watch scraping) |
| `SCRAPER_PAGE_TIMEOUT` | `30000` | Page interaction timeout in ms |
| `SCRAPER_NAV_TIMEOUT` | `30000` | Navigation timeout in ms |
| `GOOGLE_SHEET_CREDENTIALS` | `credentials.json` | Path to service account JSON file |

## Usage

### Google Sheet

```bash
python main.py --sheet-id <YOUR_SHEET_ID> --sheet-name Sheet1
```

### Local Excel / CSV

```bash
python main.py --file products.xlsx
python main.py --file products.csv
```

### Options

| Flag | Description |
|---|---|
| `--sheet-id` | Google Sheet ID (from the sheet URL) |
| `--sheet-name` | Tab name (default: `Sheet1`) |
| `--file` | Path to `.xlsx` or `.csv` file |
| `--force` | Re-scrape URLs even if already marked "success" |
| `--headless` | Force headless mode on/off (overrides env) |
| `--proxy` | Proxy URL (stubbed, not yet implemented) |

## Sheet format

The first row must contain a header. One column must contain **URL** (case-insensitive) in its header. All other columns are preserved.

After scraping, these columns are added:

| Column | Content |
|---|---|
| `Title` | Product title |
| `Description` | Product description |
| `Image URL` | Main product image URL |
| `Dimensions` | Parsed dimensions (e.g. `20 x 15 x 5 cm`) |
| `HSN` | Always empty (for manual entry) |
| `Status` | `success`, `failed`, or `blocked` |
| `Error` | Error message if failed/blocked |

## Project structure

```
├── main.py                 # CLI orchestrator
├── config.py               # Settings (delays, retries, etc.)
├── scrapers/
│   ├── base.py             # BaseScraper class + ScraperResult
│   ├── flipkart.py         # Flipkart scraper
│   ├── meesho.py           # Meesho scraper
│   └── amazon.py           # Amazon scraper
├── utils/
│   ├── parsing.py          # Regex helpers (dimensions, domain)
│   └── sheet_io.py         # Google Sheets / Excel / CSV I/O
├── requirements.txt
└── README.md
```

## Notes

- **CSS selectors may break** — if a site updates its HTML, scrapers may return empty data. Check the `Status` and `Error` columns.
- **HSN codes** are not available on public product pages. That column is left for manual entry or a separate category-to-HSN mapping step.
- **Rate limiting** — built-in random delays (2–6 s) between requests. Adjust via env vars.
- **Block detection** — if a CAPTCHA or block page is detected, the URL is marked `blocked` and skipped.
