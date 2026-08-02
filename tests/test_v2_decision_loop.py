from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from engine_v2.config import V2Settings
from engine_v2.domain.enums import DataQuality
from engine_v2.domain.models import Observation
from engine_v2.domain.registry import build_default_registry
from engine_v2.features.cross_asset import dynamic_relationship
from engine_v2.ingestion.yfinance_delayed import YFinanceDelayedProvider
from engine_v2.opportunities.scanner import scan_opportunities
from engine_v2.opportunities.product_guards import evaluate_product_guard
from engine_v2.shadow_runner import ShadowRunner
from engine_v2.engine import V2Engine


def test_reference_products_and_spot_short_are_excluded(monkeypatch):
    async def discover():
        return await YFinanceDelayedProvider().discover_products(["SOXX"])
    reference = asyncio.run(discover()).products[0].to_dict()
    assert reference["role"] == "reference"
    assert reference["is_tradable"] is False
    assert scan_opportunities([reference], {"features": {}}) == []

    registry = build_default_registry()
    spot = registry.product("BTC_BINANCE_SPOT").to_dict()
    snapshot = {
        "mode": "live",
        "data_quality": {"score": 100},
        "features": {"technical": {"latest_close": 60000, "atr_14_pct_closed": 0.01}},
        "costs": {"spread_bps": 2, "taker_fee_bps": 10, "estimated_slippage_bps": 1},
        "product_context": {},
    }
    candidates = scan_opportunities([spot], snapshot)
    assert {item.direction.value for item in candidates} == {"long", "no_trade"}


def test_fixture_directional_candidates_have_replayable_plans(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    settings = V2Settings(mode="fixture", live_enabled=False, duckdb_path="data/x.duckdb", parquet_root="data/raw")
    engine = V2Engine(tmp_path, settings)
    snapshot = asyncio.run(engine.build_snapshot(live=False))
    directional = [
        item for item in snapshot["ranked_candidates"]
        if item["direction"] in {"long", "short"} and item["valid_for_shadow"]
    ]
    assert directional
    assert all(item["trigger_price"] and item["stop_price"] and item["target_price"] for item in directional)
    assert all("trigger_missing" not in item["reason_codes"] for item in directional)
    assert all(not item["valid_for_user_execution"] for item in directional)


def test_shadow_outcome_and_calibration_insufficient_sample(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    settings = V2Settings(mode="fixture", live_enabled=False, duckdb_path="data/x.duckdb", parquet_root="data/raw")
    engine = V2Engine(tmp_path, settings)
    asyncio.run(engine.build_snapshot(live=False))
    candidate = engine.storage.open_candidates()[0]["payload"]
    now = datetime.now(timezone.utc)
    trigger = float(candidate["trigger_price"])
    future = Observation(
        "future-test",
        "fixture",
        "fixture",
        candidate["product_id"],
        "candle_15m",
        now + timedelta(minutes=15),
        None,
        now,
        now,
        now,
        None,
        DataQuality.OK,
        "2.0",
        {
            "open_time": now + timedelta(minutes=15),
            "close_time": now + timedelta(minutes=30),
            "open": trigger,
            "high": float(candidate["target_price"]),
            "low": float(candidate["stop_price"]) + 1,
            "close": float(candidate["target_price"]),
            "is_final": True,
        },
    )
    engine.storage.append_observation(future)
    settled = ShadowRunner(engine, mode="fixture")._settle_open_candidates()
    assert settled >= 1
    assert engine.storage.evaluation_summary()["outcome_count"] >= 1
    assert engine.storage.calibration_summary(min_samples=30)["status"] == "insufficient_sample"


def test_session_aware_nearest_alignment_and_guards():
    base = datetime(2026, 1, 5, tzinfo=timezone.utc)
    source = [
        {"timestamp": (base + timedelta(minutes=15 * i)).isoformat(), "return": i / 1000, "session": "regular"}
        for i in range(40)
    ]
    target = [
        {"timestamp": (base + timedelta(minutes=15 * i, seconds=30)).isoformat(), "return": i / 1000, "session": "regular"}
        for i in range(40)
    ]
    relationship = dynamic_relationship(source, target, minimum_samples=30)
    assert relationship["alignment"]["method"] == "utc_floor_nearest"
    assert relationship["usable"] is True
    assert evaluate_product_guard(
        {"product_id": "SOXL_YF", "underlying_id": "SOXL"},
        {"underlying_price_age": 1, "underlying_price_stale": True},
    )["reasons"] == ["underlying_stale"]
    warnings = evaluate_product_guard(
        {"product_id": "HYNIX", "underlying_id": "SK_HYNIX_KRX"},
        {"underlying_close_age": 10, "usd_krw_age": None, "krx_session_state": "regular"},
    )["warnings"]
    assert "usd_krw_age_unavailable" in warnings
