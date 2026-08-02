from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from engine_v2.engine import V2Engine

from .serializers import envelope


class ManualEventRequest(BaseModel):
    headline: str = Field(min_length=1)
    url: str | None = None
    discovered_via: str = "manual_intake"
    first_seen_at: str | None = None
    published_at: str | None = None
    event_time: str | None = None
    original_source: str | None = None
    category: str | None = None
    affected_assets: list[str] = Field(default_factory=list)
    affected_factors: list[str] = Field(default_factory=list)
    expected: float | None = None
    actual: float | None = None
    previous: float | None = None
    notes: str | None = None


def register_v2_routes(app: FastAPI, root_dir: str | Path | None = None) -> V2Engine:
    engine = V2Engine(root_dir=root_dir or Path.cwd())
    app.state.v2_engine = engine
    router = APIRouter(prefix="/api/v2", tags=["v2"])

    def get_engine(request: Request) -> V2Engine:
        return request.app.state.v2_engine

    @router.get("/universe")
    async def universe(request: Request):
        engine = get_engine(request)
        return envelope(engine.registry.to_dict())

    @router.get("/products")
    async def products(request: Request):
        engine = get_engine(request)
        return envelope([product.to_dict() for product in engine.registry.products.values()])

    @router.get("/provider-health")
    async def provider_health(request: Request):
        engine = get_engine(request)
        return envelope({"providers": engine.manager.health(), "capabilities": [provider.capabilities.to_dict() for provider in engine.manager.providers.values()]})

    @router.get("/data-health")
    async def data_health(request: Request):
        return get_engine(request).data_health()

    @router.get("/snapshot")
    async def snapshot(request: Request, mode: str | None = None, live: bool | None = None):
        engine = get_engine(request)
        data = await engine.build_snapshot(mode=mode, live=live)
        return envelope(data, generated_at=data.get("generated_at"))

    @router.get("/demo/snapshot")
    async def demo_snapshot(request: Request):
        engine = get_engine(request)
        data = await engine.build_snapshot(mode="fixture")
        return envelope(data, generated_at=data.get("generated_at"))

    @router.get("/cross-asset")
    async def cross_asset(request: Request):
        engine = get_engine(request)
        snapshot = engine.last_snapshot or await engine.build_snapshot()
        computed = snapshot.get("computed_features", {})
        return envelope({
            "relationships": computed.get("cross_asset_state", {}),
            "score": computed.get("cross_asset"),
            "mode": snapshot.get("mode"),
            "note": "Only session-overlap and sample-qualified relationships are usable.",
        })

    @router.get("/factors")
    async def factors(request: Request):
        engine = get_engine(request)
        snapshot = engine.last_snapshot or await engine.build_snapshot()
        return envelope(snapshot.get("factor_state", {}))

    @router.get("/events")
    async def events(request: Request):
        return envelope(get_engine(request).events)

    @router.post("/events/manual-intake")
    async def manual_intake(body: ManualEventRequest, request: Request):
        if os.getenv("SAVE_TICKER_MANUAL_INTAKE_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
            raise HTTPException(status_code=404, detail="manual intake disabled")
        configured_token = (os.getenv("SAVE_TICKER_INTAKE_TOKEN") or "").strip()
        supplied_token = request.headers.get("x-intake-token", "")
        if configured_token:
            if supplied_token != configured_token:
                raise HTTPException(status_code=401, detail="intake token required")
        else:
            client_host = request.client.host if request.client else ""
            if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
                raise HTTPException(status_code=403, detail="manual intake is local-only until SAVE_TICKER_INTAKE_TOKEN is configured")
        event = get_engine(request).manual_event(body.model_dump())
        return envelope(event)

    @router.get("/opportunities")
    async def opportunities(request: Request, mode: str | None = None, live: bool | None = None):
        engine = get_engine(request)
        snapshot = engine.last_snapshot or await engine.build_snapshot(mode=mode, live=live)
        return envelope(snapshot.get("ranked_candidates", []), generated_at=snapshot.get("generated_at"))

    @router.get("/decision")
    async def decision(request: Request, mode: str | None = None, live: bool | None = None):
        engine = get_engine(request)
        return envelope(await engine.decision(mode=mode, live=live))

    @router.get("/evaluation/summary")
    async def evaluation_summary(request: Request):
        return envelope(get_engine(request).storage.evaluation_summary())

    @router.get("/evaluation/calibration")
    async def evaluation_calibration():
        return envelope({"brier_score": None, "log_loss": None, "ece": None, "quality": "partial", "reason": "outcome_store_needs_shadow_history"})

    @router.get("/status")
    async def status(request: Request):
        return envelope(get_engine(request).status())

    app.include_router(router)
    return engine
