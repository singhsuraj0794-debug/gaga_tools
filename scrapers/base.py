from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any

import config
from playwright.sync_api import Page

logger = logging.getLogger(__name__)


class ScraperResult:
    def __init__(
        self,
        title: str | None = None,
        description: str | None = None,
        image_url: str | None = None,
        dimensions: str | None = None,
        hsn: str | None = None,
        status: str = "failed",
        error: str | None = None,
    ):
        self.title = title or ""
        self.description = description or ""
        self.image_url = image_url or ""
        self.dimensions = dimensions
        self.hsn = None
        self.status = status
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "image_url": self.image_url,
            "dimensions": self.dimensions,
            "hsn": self.hsn,
            "status": self.status,
            "error": self.error,
        }


class BaseScraper(ABC):
    def __init__(self, page: Page):
        self.page = page

    @abstractmethod
    def scrape(self, url: str) -> ScraperResult:
        ...

    def random_delay(self) -> None:
        delay = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
        logger.debug("Sleeping %.1f seconds", delay)
        time.sleep(delay)

    def safe_text(
        self,
        selector: str,
        attr: str | None = None,
        default: str | None = None,
    ) -> str | None:
        try:
            el = self.page.query_selector(selector)
            if not el:
                return default
            if attr:
                return (el.get_attribute(attr) or "").strip() or default
            return el.text_content() or default
        except Exception:
            return default

    def safe_attr(self, selector: str, attr: str, default: str | None = None) -> str | None:
        return self.safe_text(selector, attr=attr, default=default)

    def detect_blocked(self, expected_selectors: list[str]) -> bool:
        url = self.page.url.lower()
        if any(kw in url for kw in ("captcha", "challenge", "blocked", "sorry")):
            return True

        for sel in expected_selectors:
            try:
                if self.page.wait_for_selector(sel, timeout=5000):
                    return False
            except Exception:
                continue

        return True
