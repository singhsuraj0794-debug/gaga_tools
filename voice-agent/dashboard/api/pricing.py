"""Deterministic pricing boundary. This module must never be imported by LLM code."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class PriceConfig:
    product_id: str
    name: str
    list_price: int
    floor_price: int
    target_price: int


@dataclass(frozen=True)
class RangeGrant:
    session_token: str
    product_id: str
    min_offer: int
    max_offer: int
    expires_at: int
    round_number: int

    def ai_payload(self) -> dict[str, Any]:
        return {
            "session_token": self.session_token,
            "min_offer": self.min_offer,
            "max_offer": self.max_offer,
            "expires_at": self.expires_at,
        }


class PricingEngine:
    """Owns all pricing math and keeps grants server-side for validation."""

    def __init__(self, configs: dict[str, PriceConfig], secret: Optional[bytes] = None):
        self._configs = configs
        self._secret = secret or secrets.token_bytes(32)
        self._grants: dict[str, RangeGrant] = {}

    def public_products(self) -> list[dict[str, Any]]:
        return [
            {"id": p.product_id, "name": p.name, "list_price": p.list_price}
            for p in self._configs.values()
        ]

    def admin_config(self, product_id: str) -> PriceConfig:
        return self._configs[product_id]

    def issue_range(
        self,
        product_id: str,
        session_id: str,
        round_number: int = 1,
        elapsed_seconds: int = 0,
        buyer_signals: Optional[list[str]] = None,
    ) -> RangeGrant:
        config = self._configs[product_id]
        round_number = max(1, min(round_number, 12))
        # Concessions are deterministic and bounded by the protected floor.
        progress = min(0.85, (round_number - 1) * 0.09 + min(elapsed_seconds, 600) / 6000)
        buyer_bonus = 0.02 if "ready_to_buy" in (buyer_signals or []) else 0
        max_offer = round(config.list_price - (config.list_price - config.target_price) * min(0.95, progress + buyer_bonus))
        min_offer = max(config.floor_price, round(max_offer - max(1, config.list_price * 0.04)))
        expires_at = int(time.time()) + 300
        raw = f"{product_id}:{session_id}:{round_number}:{expires_at}:{secrets.token_hex(8)}".encode()
        token = hmac.new(self._secret, raw, hashlib.sha256).hexdigest()
        grant = RangeGrant(token, product_id, min_offer, max_offer, expires_at, round_number)
        self._grants[token] = grant
        return grant

    def validate_offer(self, session_token: str, offer: int) -> tuple[bool, str]:
        grant = self._grants.get(session_token)
        if grant is None:
            return False, "unknown_or_expired_session"
        if time.time() > grant.expires_at:
            self._grants.pop(session_token, None)
            return False, "unknown_or_expired_session"
        if offer < grant.min_offer or offer > grant.max_offer:
            return False, "offer_outside_approved_range"
        return True, "accepted"


DEFAULT_PRICES = {
    "prestige-cutter": PriceConfig("prestige-cutter", "Prestige Veggie Cutter", 799, 499, 599),
    "milton-flask": PriceConfig("milton-flask", "Milton Thermosteel Flask", 1299, 799, 999),
    "panda-lamp": PriceConfig("panda-lamp", "Silicone Panda Night Lamp", 999, 549, 699),
}
