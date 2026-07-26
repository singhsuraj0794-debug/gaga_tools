from __future__ import annotations

import logging
import re

import config
from playwright.sync_api import Page

from scrapers.base import BaseScraper, ScraperResult
from utils.parsing import extract_dimensions

logger = logging.getLogger(__name__)

TITLE_SELECTOR = "span#productTitle"
IMAGE_SELECTOR = "img#landingImage"
IMAGE_FALLBACK_SELECTOR = "div.imgTagWrapper img"
PRICE_SELECTOR = "span.a-price-whole"
PRICE_FRACTION_SELECTOR = "span.a-price-fraction"

DESC_SELECTORS = [
    "div#productDescription p",
    "div#feature-bullets ul li span",
    "div#bookDescription_feature_div div",
]

DETAILS_TABLE_SELECTOR = "table#productDetails_detailBullets_sections1 tr"
DETAILS_TABLE_CELL = "td"


class AmazonScraper(BaseScraper):
    def scrape(self, url: str) -> ScraperResult:
        logger.info("Navigating to Amazon: %s", url)
        self.page.goto(url, timeout=config.NAV_TIMEOUT, wait_until="domcontentloaded")
        self.random_delay()

        if self.detect_blocked([TITLE_SELECTOR]):
            logger.warning("Amazon CAPTCHA / block page detected for %s", url)
            return ScraperResult(status="blocked", error="Blocked by Amazon")

        self.page.wait_for_load_state("networkidle", timeout=config.PAGE_TIMEOUT)

        try:
            self.page.wait_for_selector(TITLE_SELECTOR, timeout=config.PAGE_TIMEOUT)
        except Exception:
            logger.warning("Title selector not found for Amazon: %s", url)

        title = self._extract_title()
        image_url = self._extract_image()
        description = self._extract_description()
        spec_text = self._extract_specs()
        dimensions = extract_dimensions(spec_text)

        logger.info("Amazon result — title: %s, status: success", (title or "?")[:60])
        return ScraperResult(
            title=title,
            description=description,
            image_url=image_url,
            dimensions=dimensions,
            status="success",
        )

    def _extract_title(self) -> str | None:
        result = self.safe_text(TITLE_SELECTOR)
        return result.strip() if result else None

    def _extract_image(self) -> str | None:
        for sel in (IMAGE_SELECTOR, IMAGE_FALLBACK_SELECTOR):
            for attr in ("src", "data-old-hires", "data-a-dynamic-image"):
                result = self.safe_attr(sel, attr)
                if result and not result.endswith(".svg"):
                    return result
        return None

    def _extract_description(self) -> str | None:
        for sel in DESC_SELECTORS:
            result = self.safe_text(sel)
            if result:
                text = re.sub(r"\s+", " ", result).strip()
                if len(text) > 20:
                    return text[:800]
        return None

    def _extract_specs(self) -> str | None:
        parts: list[str] = []
        rows = self.page.query_selector_all(DETAILS_TABLE_SELECTOR)
        for row in rows:
            cells = row.query_selector_all(DETAILS_TABLE_CELL)
            text = " ".join(
                (cell.text_content() or "").strip() for cell in cells
            ).strip()
            if text:
                parts.append(text)

        full = " | ".join(parts)
        return full if full else None



