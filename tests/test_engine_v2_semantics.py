from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

from engine_v2.features.cross_asset import dynamic_relationship
from engine_v2.features.derivatives import weighted_oi
from engine_v2.features.liquidations import aggregate_actual, classify_estimate, classify_snapshot
from engine_v2.features.microstructure import trade_cvd
from engine_v2.features.options import actual_delta_surface
from engine_v2.features.technical import closed_candle_features
from engine_v2.fixture import fixture_observations
from engine_v2.domain.models import LiquidationClusterEstimate, LiquidationEventActual, LiquidationSnapshotPartial
from engine_v2.engine import V2Engine
from engine_v2.storage.point_in_time import filter_available


def test_forming_candle_is_not_used_by_closed_features():
    observations = fixture_observations(count=40)
    candles = [row.payload for row in observations if row.data_type == "candle_15m"]
    features = closed_candle_features(candles, minimum_samples=30)
    assert features["closed_candle_count"] == 40
    assert features["forming_candle_count"] == 1


def test_point_in_time_excludes_future_observation():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    rows = [
        {"available_at": "2026-08-01T23:00:00Z", "value": 1},
        {"available_at": "2026-08-02T01:00:00Z", "value": 2},
    ]
    assert [row["value"] for row in filter_available(rows, now)] == [1]


def test_actual_and_estimated_liquidations_are_distinct():
    event = LiquidationEventActual("BTC_BINANCE_PERP", "e1", "long", 1, 60000, 60000, datetime(2026, 8, 2, tzinfo=timezone.utc), "fixture")
    aggregate = aggregate_actual([event])
    assert aggregate["quality"] == "ok"
    partial = classify_snapshot(LiquidationSnapshotPartial("BTC_BINANCE_PERP", 10, 5, event.event_time, "fixture"))
    estimate = classify_estimate(LiquidationClusterEstimate("BTC_BINANCE_PERP", "long", 59000, 1.6, 0.8, 10, "fixture"))
    assert partial["actual_totalization_allowed"] is False
    assert estimate["quality"] == "estimated"


def test_oi_change_is_not_arithmetic_average():
    result = weighted_oi([
        {"venue": "a", "open_interest": 100, "price": 1000, "change_pct": 10},
        {"venue": "b", "open_interest": 10, "price": 1000, "change_pct": -10},
    ])
    assert result["notional_weighted_oi_change"] == 8.181818181818182


def test_trade_cvd_is_distinct_from_taker_bucket():
    result = trade_cvd([
        {"product_id": "BTC_BINANCE_PERP", "quantity": 2, "aggressor_side": "buy", "event_time": "2026-08-02T00:00:00Z"},
        {"product_id": "BTC_BINANCE_PERP", "quantity": 1, "aggressor_side": "sell", "event_time": "2026-08-02T00:01:00Z"},
    ])
    assert result["trade_cvd"] == 1
    assert "taker_bucket_delta_1h" not in result


def test_actual_delta_required_for_rr():
    missing = actual_delta_surface([{"mark_iv": 50, "type": "C"}])
    assert missing["rr_25d"] is None
    assert missing["quality"] == "partial"
    complete = actual_delta_surface([
        {"mark_iv": 50, "type": "C", "greeks": {"delta": 0.25}},
        {"mark_iv": 55, "type": "P", "greeks": {"delta": -0.25}},
    ])
    assert complete["rr_25d"] == 5


def test_dynamic_relationship_requires_sample_and_overlap():
    source = [{"timestamp": str(i), "return": i / 1000} for i in range(40)]
    target = [{"timestamp": str(i), "return": i / 1000} for i in range(40)]
    result = dynamic_relationship(source, target, minimum_samples=30)
    assert result["usable"] is True
    assert result["state"] == "confirmed_positive_coupling"


def test_force_orders_is_not_in_v2_provider_source():
    source = inspect.getsource(__import__("engine_v2.ingestion.binance", fromlist=["BinancePublicProvider"]))
    assert "forceOrders" not in source


def test_fixture_engine_produces_no_trade_candidate_without_llm():
    async def run():
        engine = V2Engine()
        snapshot = await engine.build_snapshot(live=False)
        assert snapshot["schema_version"] == "2.0"
        assert snapshot["ranked_candidates"]
        assert any(item["direction"] == "no_trade" for item in snapshot["ranked_candidates"])
        assert snapshot["explanation"]["summary"]
        assert snapshot["critic"]["can_change_deterministic_values"] is False
    asyncio.run(run())
