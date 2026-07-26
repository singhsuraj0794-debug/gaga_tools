from __future__ import annotations

import logging
import re

import config
from playwright.sync_api import Page

from scrapers.base import BaseScraper, ScraperResult
from utils.parsing import extract_dimensions

logger = logging.getLogger(__name__)

TITLE_SELECTORS = [
    "h1 span.VU-ZEz",
    "h1",
    "span[class*='B_NuCI']",
    "span[class*='VU-ZEz']",
]

IMAGE_SELECTORS = [
    "img._396cs4",
    "img[class*='_396cs4']",
    "img[class*='_2r_T1I']",
    "div[class*='_2wP2Eg'] img",
    "img[alt*='product'][src*='flipkart']",
    "div._2SvJPc img",
]

PRICE_SELECTORS = [
    "div.Nx9bqj",
    "div[class*='Nx9bqj']",
    "div[class*='_30jeq3']",
    "div._30jeq3._1_WHN1",
]

DESC_SELECTORS = [
    "div._1mXcCf",
    "div[class*='_1mXcCf']",
    "div[class*='_3VWgvT']",
    "div[class*='_3LzZqs']",
]

SPEC_ROW_SELECTOR = "div._1UhVsV table._2cM9jp tr"
SPEC_CELL_SELECTOR = "td"


class FlipkartScraper(BaseScraper):
    def scrape(self, url: str) -> ScraperResult:
        logger.info("Navigating to Flipkart: %s", url)
        self.page.goto(url, timeout=config.NAV_TIMEOUT, wait_until="domcontentloaded")
        self.random_delay()

        if self.detect_blocked(TITLE_SELECTORS):
            logger.warning("Flipkart block page detected for %s", url)
            return ScraperResult(status="blocked", error="Blocked by Flipkart")

        expected = [s for s in TITLE_SELECTORS if s]
        self.page.wait_for_load_state("networkidle", timeout=config.PAGE_TIMEOUT)

        title = self._extract_title()
        image_url = self._extract_image()
        description = self._extract_description()
        spec_text = self._extract_specs()
        dimensions = extract_dimensions(spec_text)

        logger.info("Flipkart result — title: %s, status: success", (title or "?")[:60])
        return ScraperResult(
            title=title,
            description=description,
            image_url=image_url,
            dimensions=dimensions,
            status="success",
        )

    def _extract_title(self) -> str | None:
        for sel in TITLE_SELECTORS:
            result = self.safe_text(sel)
            if result and len(result) > 5:
                return result.strip()
        return None

    def _extract_image(self) -> str | None:
        for sel in IMAGE_SELECTORS:
            result = self.safe_attr(sel, "src")
            if result and not result.endswith(".svg"):
                return result
            result = self.safe_attr(sel, "data-src")
            if result and not result.endswith(".svg"):
                return result
        return None

    def _extract_description(self) -> str | None:
        for sel in DESC_SELECTORS:
            result = self.safe_text(sel)
            if result:
                text = re.sub(r"\s+", " ", result).strip()
                if len(text) > 20:
                    return text[:500]
        return None

    def _extract_specs(self) -> str | None:
        parts: list[str] = []
        rows = self.page.query_selector_all(SPEC_ROW_SELECTOR)
        for row in rows:
            cells = row.query_selector_all(SPEC_CELL_SELECTOR)
            text = " ".join(
                (cell.text_content() or "").strip() for cell in cells
            ).strip()
            if text:
                parts.append(text)

        full = " | ".join(parts)
        return full if full else None



