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


def test_bybit_close_time_uses_milliseconds_and_marks_forming_candle():
    from engine_v2.domain.models import ProductSpec, parse_datetime
    from engine_v2.domain.enums import ProductType
    from engine_v2.ingestion.http import JSONResponse
    from engine_v2.ingestion.bybit import BybitPublicProvider
    from datetime import timedelta

    class FakeClient:
        async def get(self, url, params=None, headers=None):
            if url.endswith("/kline"):
                now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                old_ms = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
                return JSONResponse({"result": {"list": [
                    [str(now_ms - 60_000), "1", "1", "1", "1", "1", "1"],
                    [str(old_ms), "1", "1", "1", "1", "1", "1"],
                ]}}, 200, {}, 0)
            if url.endswith("/recent-trade"):
                return JSONResponse({"result": {"list": []}}, 200, {}, 0)
            return JSONResponse({"result": {"list": []}}, 200, {}, 0)

    product = ProductSpec("BTC_BYBIT_PERP", "BTC", "bybit", "bybit_linear", "BTCUSDT", ProductType.PERPETUAL, is_tradable=True)
    result = asyncio.run(BybitPublicProvider(client=FakeClient()).backfill(product, timeframe="15m", limit=2))
    candles = [item.payload for item in result.data if item.data_type == "candle_15m"]
    assert len(candles) == 2
    for candle in candles:
        assert candle["close_time"] is not None
        assert (parse_datetime(candle["close_time"]) - parse_datetime(candle["open_time"])).total_seconds() == 900
    assert any(candle["is_final"] is False for candle in candles)
    assert any(candle["is_final"] is True for candle in candles)


def test_orderbook_uses_best_bid_and_best_ask_and_rejects_crossed_book():
    from engine_v2.features.microstructure import orderbook_features

    valid = orderbook_features({"bids": [["99", "2"], ["100", "1"]], "asks": [["102", "1"], ["101", "2"]]})
    assert valid["best_bid"] == 100
    assert valid["best_ask"] == 101
    assert valid["best_bid"] < valid["mid_price"] < valid["best_ask"]
    assert valid["spread_bps"] >= 0
    crossed = orderbook_features({"bids": [["101", "1"]], "asks": [["100", "1"]]})
    assert crossed["quality"] == "invalid_semantics"
    assert crossed["reason"] == "crossed_book"


def test_trade_cvd_sorts_deduplicates_and_separates_notional():
    result = trade_cvd([
        {"product_id": "BTC_BINANCE_PERP", "venue": "binance", "trade_id": "2", "price": 110, "quantity": 1, "aggressor_side": "buy", "event_time": "2026-08-02T00:02:00Z"},
        {"product_id": "BTC_BINANCE_PERP", "venue": "binance", "trade_id": "1", "price": 100, "quantity": 2, "aggressor_side": "sell", "event_time": "2026-08-02T00:01:00Z"},
        {"product_id": "BTC_BINANCE_PERP", "venue": "binance", "trade_id": "1", "price": 100, "quantity": 2, "aggressor_side": "sell", "event_time": "2026-08-02T00:01:00Z"},
    ])
    assert result["duplicate_count"] == 1
    assert result["out_of_order_count"] == 1
    assert result["quantity_cvd"] == -1
    assert result["notional_cvd_usd"] == -90
    assert result["notional_cvd_ratio"] < 0


def test_deribit_options_use_option_product_type():
    from engine_v2.domain.enums import ProductType
    from engine_v2.ingestion.deribit import DeribitOptionsProvider

    assert ProductType.OPTION.value == "option"
    assert "option_instruments" in DeribitOptionsProvider().capabilities.capabilities


def test_duckdb_and_parquet_are_real_write_backends(tmp_path):
    from pathlib import Path
    from engine_v2.storage.database import V2Storage

    store = V2Storage(tmp_path / "db", tmp_path / "engine.duckdb", tmp_path / "raw")
    rows = fixture_observations(count=2)
    assert store.backend == "duckdb"
    assert store.append_observations(rows) == len(rows)
    assert store.append_observations(rows) == 0
    assert len(list(Path(tmp_path / "raw").rglob("*.parquet"))) == len(rows)
    assert store.observations(limit=100)
    store.close()


def test_gate_candle_final_semantics():
    from engine_v2.domain.enums import ProductType
    from engine_v2.domain.models import ProductSpec
    from engine_v2.ingestion.gate_futures import GateFuturesProvider
    from engine_v2.ingestion.http import JSONResponse

    class FakeClient:
        async def get(self, url, params=None, headers=None):
            now = int(datetime.now(timezone.utc).timestamp())
            return JSONResponse([
                {"t": now - 60, "o": "1", "h": "1", "l": "1", "c": "1", "v": "1", "sum": "1"},
                {"t": 1_700_000_000, "o": "1", "h": "1", "l": "1", "c": "1", "v": "1", "sum": "1"},
            ], 200, {}, 0)

    product = ProductSpec("BTC_GATE_PERP", "BTC", "gate_futures", "gate_usdt_futures", "BTC_USDT", ProductType.PERPETUAL, is_tradable=True)
    result = asyncio.run(GateFuturesProvider(client=FakeClient()).backfill(product, timeframe="15m", limit=2))
    candles = [row.payload for row in result.data]
    assert any(row["is_final"] is False for row in candles)
    assert any(row["is_final"] is True for row in candles)
    assert all(row["close_time"] is not None for row in candles)


def test_yfinance_catalog_is_delayed_and_explicit(monkeypatch):
    from engine_v2.ingestion import yfinance_delayed as module
    from engine_v2.domain.enums import DataQuality
    from engine_v2.domain.models import ProductSpec

    monkeypatch.setattr(module, "_history", lambda *args, **kwargs: [{
        "timestamp": datetime(2026, 8, 1, 13, 30, tzinfo=timezone.utc),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 10.0,
    }])
    provider = module.YFinanceDelayedProvider()
    products_result = asyncio.run(provider.discover_products(["QQQ", "SK_HYNIX_KRX"]))
    products = products_result.products
    assert {product.venue_symbol for product in products} == {"QQQ", "000660.KS"}
    assert all(product.capabilities["delay_label"] == "delayed" for product in products)
    result = asyncio.run(provider.backfill(products[0], timeframe="15m", limit=1))
    assert result.quality == DataQuality.DELAYED
    assert result.data[0].quality == DataQuality.DELAYED
    assert result.data[0].payload["is_final"] is True


def test_replay_candidate_records_trigger_fill_exit_and_costs():
    from engine_v2.evaluation import ReplayConfig, replay_candidate

    candles = [
        {"open_time": "2026-01-01T00:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100},
        {"open_time": "2026-01-01T00:15:00Z", "open": 100, "high": 103, "low": 99, "close": 102},
    ]
    result = replay_candidate(
        {"product_id": "BTC", "direction": "long", "entry_plan": "conditional_trigger", "trigger_price": 100, "stop_price": 98, "target_price": 102},
        candles,
        config=ReplayConfig(fee_bps=2, slippage_bps=1),
    )
    assert result["status"] == "filled"
    assert result["exit_reason"] == "target"
    assert result["mfe_bps"] > 0
    assert result["mae_bps"] < 0
    assert result["net_return_bps"] < result["gross_return_bps"]
