from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from playwright.sync_api import sync_playwright

import config
from scrapers.amazon import AmazonScraper
from scrapers.base import ScraperResult
from scrapers.flipkart import FlipkartScraper
from scrapers.meesho import MeeshoScraper
from utils.parsing import platform_from_url
from utils.sheet_io import (
    find_url_column,
    read_sheet,
    write_results,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape product data from Flipkart, Meesho, and Amazon URLs.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sheet-id", help="Google Sheet ID to read from")
    group.add_argument("--file", help="Path to local Excel (.xlsx) or CSV file")

    parser.add_argument("--sheet-name", default=config.DEFAULT_SHEET_NAME, help="Sheet tab name (default: Sheet1)")
    parser.add_argument("--force", action="store_true", help="Re-scrape URLs even if already marked success")
    parser.add_argument("--proxy", help="Proxy URL (stubbed, not implemented)")
    parser.add_argument("--headless", action="store_true", default=None, help="Run browser headless")
    return parser.parse_args()


def get_scraper(platform: str, page: Any) -> Any:
    scrapers = {
        "flipkart": FlipkartScraper,
        "meesho": MeeshoScraper,
        "amazon": AmazonScraper,
    }
    cls = scrapers.get(platform)
    if cls is None:
        raise ValueError(f"Unknown platform: {platform}")
    return cls(page)


def should_skip(
    url: str, rows: list[list[str]], url_col: int, row_idx: int, force: bool
) -> bool:
    if force:
        return False

    status_col = None
    for i, h in enumerate(rows[0]):
        if h.strip().lower() == "status":
            status_col = i
            break
    if status_col is None:
        return False

    row = rows[row_idx]
    if url_col < len(row) and status_col < len(row):
        if row[status_col].strip().lower() == "success":
            logger.info("Row %d: already marked success, skipping", row_idx + 1)
            return True
    return False


def process_url(
    url: str,
    page: Any,
) -> dict[str, str]:
    url = url.strip()
    if not url:
        return {
            "title": "",
            "description": "",
            "image_url": "",
            "dimensions": "",
            "hsn": "",
            "status": "failed",
            "error": "Empty URL",
        }

    platform = platform_from_url(url)
    if platform is None:
        logger.warning("Unrecognised domain for URL: %s", url)
        return {
            "title": "",
            "description": "",
            "image_url": "",
            "dimensions": "",
            "hsn": "",
            "status": "failed",
            "error": f"Unsupported domain: {url}",
        }

    last_error: str | None = None
    for attempt in range(1, config.MAX_RETRIES + 2):
        try:
            scraper_cls = get_scraper(platform, page)
            result: ScraperResult = scraper_cls.scrape(url)

            if result.status == "blocked":
                logger.warning("[%s] Blocked on attempt %d/%d", platform, attempt, config.MAX_RETRIES + 1)
                return result.to_dict()

            if result.status == "success":
                logger.info("[%s] Success on attempt %d", platform, attempt)
                return result.to_dict()

            last_error = result.error or "Unknown error"
            logger.warning("[%s] Attempt %d failed: %s", platform, attempt, last_error)

        except Exception as exc:
            last_error = str(exc)
            logger.warning("[%s] Attempt %d exception: %s", platform, attempt, last_error)

        if attempt <= config.MAX_RETRIES:
            time.sleep(3)

    logger.error("[%s] All attempts exhausted for %s", platform, url)
    return {
        "title": "",
        "description": "",
        "image_url": "",
        "dimensions": "",
        "hsn": "",
        "status": "failed",
        "error": last_error or "Max retries exceeded",
    }


def main() -> None:
    args = parse_args()
    headless = args.headless if args.headless is not None else config.HEADLESS

    logger.info("Reading input data...")
    rows, ws, source_type = read_sheet(
        sheet_id=args.sheet_id,
        sheet_name=args.sheet_name,
        file_path=args.file,
    )

    if not rows or len(rows) < 2:
        logger.warning("No data rows found in sheet.")
        return

    url_col = find_url_column(rows[0])
    if url_col is None:
        logger.error("Could not find a URL column in headers: %s", rows[0])
        sys.exit(1)

    logger.info("URL column found at index %d ('%s')", url_col, rows[0][url_col])

    url_results: list[dict[str, str] | None] = [None] * (len(rows) - 1)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1280, "height": 720},
            locale="en-IN",
        )
        context.set_default_timeout(config.PAGE_TIMEOUT)

        data_rows = rows[1:]
        for row_idx, row in enumerate(data_rows):
            if url_col >= len(row):
                logger.info("Row %d: no URL found, skipping", row_idx + 2)
                continue

            url = row[url_col].strip()
            if not url:
                continue

            if should_skip(url, rows, url_col, row_idx + 1, args.force):
                url_results[row_idx] = None
                continue

            logger.info("Processing row %d: %s", row_idx + 2, url)
            page = context.new_page()

            try:
                result = process_url(url, page)
                url_results[row_idx] = result
            finally:
                page.close()

        browser.close()

    success_count = sum(
        1 for r in url_results if r and r.get("status") == "success"
    )
    fail_count = sum(
        1 for r in url_results if r and r.get("status") == "failed"
    )
    blocked_count = sum(
        1 for r in url_results if r and r.get("status") == "blocked"
    )
    logger.info(
        "Done. %d success, %d failed, %d blocked out of %d URLs processed.",
        success_count,
        fail_count,
        blocked_count,
        len(url_results),
    )

    logger.info("Writing results back to sheet...")
    write_results(ws, rows, source_type, url_col, url_results)
    logger.info("All done.")


if __name__ == "__main__":
    main()
