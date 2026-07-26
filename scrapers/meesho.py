from __future__ import annotations

import logging
import re

import config
from playwright.sync_api import Page

from scrapers.base import BaseScraper, ScraperResult
from utils.parsing import extract_dimensions

logger = logging.getLogger(__name__)

TITLE_SELECTORS = [
    "h1[class*='ProductTitle']",
    "h1[class*='title']",
    "h1",
    "span[class*='ProductTitle']",
]

IMAGE_SELECTORS = [
    "img[class*='ProductImage']",
    "img[class*='product-image']",
    "div[class*='image-gallery'] img",
    "div[class*='_3kLhfh'] img",
    "img[src*='meesho.com'][alt*='product']",
]

PRICE_SELECTORS = [
    "span[class*='ProductPrice']",
    "h4[class*='price']",
    "span[class*='_3X4ENl']",
]

DESC_SELECTORS = [
    "div[class*='ProductDetails']",
    "div[class*='_1T2p_i']",
    "div[class*='about-product']",
    "div[class*='Description']",
]

SPEC_SELECTORS = [
    "div[class*='Specification']",
    "div[class*='_3WTcdf']",
    "div[class*='product-detail']",
]


class MeeshoScraper(BaseScraper):
    def scrape(self, url: str) -> ScraperResult:
        logger.info("Navigating to Meesho: %s", url)
        self.page.goto(url, timeout=config.NAV_TIMEOUT, wait_until="domcontentloaded")
        self.random_delay()

        if self.detect_blocked(TITLE_SELECTORS):
            logger.warning("Meesho block page detected for %s", url)
            return ScraperResult(status="blocked", error="Blocked by Meesho")

        self.page.wait_for_load_state("networkidle", timeout=config.PAGE_TIMEOUT)

        try:
            self.page.wait_for_selector(
                "h1", timeout=config.PAGE_TIMEOUT
            )
        except Exception:
            pass

        title = self._extract_title()
        image_url = self._extract_image()
        description = self._extract_description()
        spec_text = self._extract_specs()
        dimensions = extract_dimensions(spec_text)

        logger.info("Meesho result — title: %s, status: success", (title or "?")[:60])
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
            if result and len(result.strip()) > 5:
                return result.strip()
        return None

    def _extract_image(self) -> str | None:
        for sel in IMAGE_SELECTORS:
            result = self.safe_attr(sel, "src")
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
        for sel in SPEC_SELECTORS:
            els = self.page.query_selector_all(sel)
            for el in els:
                text = (el.text_content() or "").strip()
                if text:
                    parts.append(text)

        full = " | ".join(parts)
        return full if full else None



