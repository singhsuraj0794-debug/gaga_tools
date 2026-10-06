"""Dashboard API. Run with: uvicorn server:app --reload --port 8080."""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .pricing import DEFAULT_PRICES, PricingEngine


app = FastAPI(title="Voice Agent Negotiation Dashboard API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8081"], allow_methods=["*"], allow_headers=["*"])
engine = PricingEngine(DEFAULT_PRICES)
AUDIT_LOG: list[dict[str, Any]] = []


def role_from_token(authorization: Optional[str] = Header(default=None)) -> str:
    token = (authorization or "").removeprefix("Bearer ").strip()
    return {"admin-token": "admin", "elevated-admin-token": "elevated_admin", "ai-token": "ai"}.get(token, "")


def require_admin(role: str = Depends(role_from_token)) -> str:
    if role not in {"admin", "elevated_admin"}:
        raise HTTPException(401, "admin authentication required")
    return role


class RangeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    round_number: int = Field(default=1, ge=1, le=12)
    elapsed_seconds: int = Field(default=0, ge=0, le=3600)
    buyer_signals: list[str] = Field(default_factory=list, max_length=10)


class ValidateRequest(BaseModel):
    session_token: str
    offer: int = Field(ge=0)


class ProductConfigRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    list_price: int = Field(gt=0)
    floor_price: int = Field(gt=0)
    target_price: int = Field(gt=0)


MODEL_CONFIG = {
    "stt": {"id": "stt", "name": "Whisper Base", "active": True, "settings": {"language": "auto", "latency_ms": 450}},
    "tts": {"id": "tts", "name": "Coqui TTS", "active": True, "settings": {"voice": "neutral", "exaggeration": 0.35, "latency_ms": 700}},
    "negotiation": {"id": "negotiation", "name": "Negotiation LLM", "active": True, "settings": {"temperature": 0.35, "reasoning": "bounded-range"}},
}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/products")
def products() -> list[dict[str, Any]]:
    return engine.public_products()


@app.post("/products", dependencies=[Depends(require_admin)])
def create_product(product_id: str, body: ProductConfigRequest) -> dict[str, Any]:
    if product_id in engine._configs:
        raise HTTPException(409, "product already exists")
    if not body.floor_price <= body.target_price <= body.list_price:
        raise HTTPException(422, "prices must satisfy floor <= target <= list")
    engine._configs[product_id] = type(next(iter(DEFAULT_PRICES.values())))(product_id, **body.model_dump())
    return {"id": product_id, "name": body.name, "list_price": body.list_price}


@app.get("/products/{product_id}/price-config")
def price_config(product_id: str, role: str = Depends(require_admin)) -> dict[str, Any]:
    try:
        config = engine.admin_config(product_id)
    except KeyError as exc:
        raise HTTPException(404, "product not found") from exc
    if role != "elevated_admin":
        return {"product_id": config.product_id, "name": config.name, "list_price": config.list_price, "protected": True}
    return config.__dict__


@app.put("/products/{product_id}/price-config", dependencies=[Depends(require_admin)])
def update_price_config(product_id: str, body: ProductConfigRequest, role: str = Depends(require_admin)) -> dict[str, Any]:
    if role != "elevated_admin":
        raise HTTPException(403, "elevated admin authentication required")
    if not body.floor_price <= body.target_price <= body.list_price:
        raise HTTPException(422, "prices must satisfy floor <= target <= list")
    if product_id not in engine._configs:
        raise HTTPException(404, "product not found")
    config_type = type(engine._configs[product_id])
    engine._configs[product_id] = config_type(product_id, **body.model_dump())
    return {"id": product_id, "name": body.name, "list_price": body.list_price, "updated": True}


@app.post("/products/{product_id}/negotiation-range")
def negotiation_range(product_id: str, body: RangeRequest, role: str = Depends(role_from_token)) -> dict[str, Any]:
    if role != "ai":
        raise HTTPException(403, "AI credentials required")
    try:
        grant = engine.issue_range(product_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "product not found") from exc
    AUDIT_LOG.append({"event": "range_issued", "product_id": product_id, "session_id": body.session_id, "range_issued": grant.ai_payload(), "timestamp": int(time.time())})
    return grant.ai_payload()


@app.post("/negotiation-range/validate")
def validate_range(body: ValidateRequest, x_internal_secret: Optional[str] = Header(default=None)) -> dict[str, Any]:
    if x_internal_secret != os.getenv("DASHBOARD_INTERNAL_SECRET", "local-internal-secret"):
        raise HTTPException(403, "internal authentication required")
    accepted, reason = engine.validate_offer(body.session_token, body.offer)
    if accepted:
        AUDIT_LOG.append({"event": "deal_accepted", "range_issued": body.session_token, "final_offer_accepted": body.offer, "timestamp": int(time.time())})
    return {"accepted": accepted, "reason": reason}


@app.get("/model-config", dependencies=[Depends(require_admin)])
def model_config() -> list[dict[str, Any]]:
    return list(MODEL_CONFIG.values())


@app.put("/model-config/{model_id}", dependencies=[Depends(require_admin)])
def update_model_config(model_id: str, body: dict[str, Any]) -> dict[str, Any]:
    if model_id not in MODEL_CONFIG:
        raise HTTPException(404, "model configuration not found")
    MODEL_CONFIG[model_id].update({key: body[key] for key in ("name", "active", "settings") if key in body})
    return MODEL_CONFIG[model_id]


@app.get("/sessions", dependencies=[Depends(require_admin)])
def sessions() -> list[dict[str, Any]]:
    return list(reversed(AUDIT_LOG[-50:]))


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "index.html"))
