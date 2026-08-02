from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from engine_v2.config import V2Settings
from engine_v2.domain.enums import DataQuality, Direction, ProductType
from engine_v2.domain.models import Candle, Observation, ProductSpec
from engine_v2.features.technical import analyze_horizons
from engine_v2.ingestion.bybit import BybitPublicProvider
from engine_v2.ingestion.http import JSONResponse
from engine_v2.opportunities.scorer import score_candidate
from engine_v2.storage.database import V2Storage


def _candle(product: str, timeframe: str, index: int, *, final: bool = True) -> Observation:
    seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}[timeframe]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds * index)
    close = 100 + index * 0.2
    body = Candle(
        product, timeframe, start, start + timedelta(seconds=seconds),
        close - 0.1, close + 0.5, close - 0.5, close, 100.0,
        is_final=final, source="test", collected_at=start, available_at=start,
        quality=DataQuality.OK,
    )
    return Observation(
        f"{product}-{timeframe}-{index}-{final}", "test", "test", product,
        f"candle_{timeframe}", start, None, start, start, start, None,
        DataQuality.OK, "2.0", body.to_dict(),
    )


def test_history_limits_are_the_requested_contract():
    settings = V2Settings()
    assert dict(settings.history_limits) == {
        "1m": 1500, "5m": 2000, "15m": 2000, "1h": 1500,
        "4h": 1000, "1d": 800, "1w": 250,
    }


def test_history_readiness_deduplicates_forming_and_final_candle(tmp_path):
    store = V2Storage(tmp_path / "db", tmp_path / "engine.duckdb", tmp_path / "raw")
    forming = _candle("BTC_BYBIT_PERP", "1m", 1, final=False)
    final = _candle("BTC_BYBIT_PERP", "1m", 1, final=True)
    store.append_observations([forming, final, *[_candle("BTC_BYBIT_PERP", "1m", i) for i in range(2, 42)]])
    rows = store.candle_history("BTC_BYBIT_PERP", "1m", limit=100)
    assert len(rows) == 41
    assert sum(row["is_final"] is False for row in rows) == 0
    readiness = store.history_readiness("BTC_BYBIT_PERP", "1m", requested=40)
    assert readiness["closed_count"] == 41
    assert readiness["analysis_ready"] is True
    store.close()


def test_bybit_dated_contract_is_not_labeled_perpetual():
    class FakeClient:
        async def get(self, url, params=None, headers=None):
            return JSONResponse({
                "result": {"list": [{
                    "symbol": "BTCUSDT-28AUG26",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT",
                    "settleCoin": "USDT",
                    "contractType": "Linear",
                    "deliveryTime": "1787961600000",
                    "status": "Trading",
                    "lotSizeFilter": {},
                    "priceFilter": {},
                }]}
            }, 200, {}, 0)

    result = asyncio.run(BybitPublicProvider(client=FakeClient()).discover_products(["BTC"]))
    assert len(result.products) == 1
    product = result.products[0]
    assert product.product_id == "BTC_BYBIT_FUT_20260828"
    assert product.product_type == ProductType.FUTURE
    assert product.contract_type == "dated_future"
    assert product.is_tradable is True


def test_horizon_analysis_requires_all_three_timeframes():
    candles = {
        timeframe: [_candle("BTC", timeframe, index).payload for index in range(40)]
        for timeframe in ("1m", "5m", "15m", "1h", "4h", "1d", "1w")
    }
    result = analyze_horizons(candles, minimum_samples=30)
    assert set(result) == {"ultra_short", "short", "medium", "long"}
    assert all(value["analysis_readiness"] is True for value in result.values())
    assert result["short"]["context_timeframe"] == "1h"
    assert result["long"]["trigger_timeframe"] == "4h"


def test_technical_trigger_can_be_shadow_eligible_without_cost_or_calibration():
    product = ProductSpec(
        "BTC_BYBIT_PERP", "BTC", "bybit", "bybit_linear", "BTCUSDT",
        ProductType.PERPETUAL, is_tradable=True, short_supported=True,
        contract_type="perpetual",
    ).to_dict()
    horizon = {
        "analysis_readiness": True,
        "regime": "trend_up",
        "context_timeframe": "1h",
        "setup_timeframe": "15m",
        "trigger_timeframe": "5m",
        "structure": {"labels": [{"label": "HH"}], "range_high": 101, "range_low": 99},
        "technical": {"latest_close": 100, "atr_14_pct_closed": 0.01, "trend_state": "bullish", "return_4": 0.01},
    }
    candidate = score_candidate(
        product,
        Direction.LONG,
        {
            "mode": "live",
            "horizons": {"short": horizon},
            "data_quality": {"score": 100},
            "features": {
                "technical_structure": 1, "momentum": 1, "orderflow": 1,
                "derivatives": 0, "cross_asset": 0, "event": 0, "liquidity": 0,
                "technical": horizon["technical"],
            },
            "costs": {},
            "product_context": {},
            "min_heuristic_score": 3,
        },
        horizon_name="short",
    )
    assert candidate.valid_for_shadow is True
    assert candidate.valid_for_user_execution is False
    assert candidate.candidate_stage == "shadow_eligible_candidate"
    assert candidate.cost_quality == "missing"
    assert candidate.trigger_timeframe == "5m"
    assert candidate.stop_price is not None
    assert candidate.targets


def test_provider_health_payload_contains_runtime_fields():
    from engine_v2.ingestion.health import ProviderHealthRegistry

    registry = ProviderHealthRegistry()
    registry.attempt("test")
    registry.success("test", latency_ms=12.5, observation_count=7)
    row = registry.all()[0]
    assert row["last_attempt_at"]
    assert row["last_success_at"]
    assert row["latency_ms"] == 12.5
    assert row["observation_count"] == 7
    assert row["rate_limit_state"] == "unknown"
