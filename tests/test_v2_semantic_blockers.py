from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from engine_v2.config import V2Settings
from engine_v2.domain.enums import Direction, ProductType
from engine_v2.domain.registry import build_default_registry
from engine_v2.engine import V2Engine
from engine_v2.ingestion.bybit import BybitPublicProvider
from engine_v2.ingestion.health import ProviderHealthRegistry
from engine_v2.ingestion.http import JSONResponse
from engine_v2.opportunities.scanner import scan_opportunities
from engine_v2.opportunities.scorer import score_candidate


def _product(*, spot: bool = False) -> dict:
    return {
        "product_id": "BTC_BINANCE_BTCUSDT_SPOT" if spot else "BTC_BINANCE_BTCUSDT_PERP",
        "underlying_id": "BTC",
        "provider": "binance",
        "venue": "binance_spot" if spot else "binance_futures",
        "venue_symbol": "BTCUSDT",
        "product_type": "spot" if spot else "perpetual",
        "role": "tradable",
        "is_tradable": True,
        "short_supported": not spot,
        "taker_fee_bps": None,
    }


def _horizon(
    *,
    regime: str,
    bias: str = "long",
    location: float | None = 0.5,
    countertrend: bool = False,
    structure: dict | None = None,
    trend_strength: float = 25,
    rsi: float = 60,
    trigger_fired: bool | None = None,
) -> dict:
    result = {
        "analysis_readiness": True,
        "regime": regime,
        "bias": bias,
        "current_location": location,
        "countertrend_readiness": countertrend,
        "continuation_readiness": regime in {"trend_up", "trend_down", "breakout_transition"},
        "trend_strength": trend_strength,
        "momentum_state": rsi,
        "volatility_state": 0.02,
        "structure": structure or {"labels": [{"label": "HH" if bias == "long" else "LL"}], "range_high": 110, "range_low": 90},
        "context_timeframe": "1h",
        "setup_timeframe": "15m",
        "trigger_timeframe": "5m",
        "technical": {"latest_close": 100, "atr_14_pct_closed": 0.01},
    }
    if trigger_fired is not None:
        result["trigger_fired"] = trigger_fired
    return result


def _snapshot(product: dict, horizon: dict) -> dict:
    return {
        "mode": "live",
        "horizons": {"short": horizon},
        "data_quality": {"score": 100},
        "features": {
            "technical": horizon["technical"],
            "orderflow": 0,
            "derivatives": 0,
            "cross_asset": 0,
            "event": 0,
            "liquidity": 0,
        },
        "costs": {},
        "product_context": {},
        "min_heuristic_score": 3,
    }


def test_trend_direction_hard_gates_and_spot_no_trade():
    perp = _product()
    down = _horizon(regime="trend_down", bias="short", rsi=40)
    up = _horizon(regime="trend_up", bias="long", rsi=60)

    down_candidates = scan_opportunities([perp], {"horizons": {"short": down}, "data_quality": {"score": 100}, "features": {}})
    assert {item.direction.value for item in down_candidates} == {"short", "no_trade"}
    assert not any(item.strategy_family == "trend_follow_long" for item in down_candidates)

    up_candidates = scan_opportunities([perp], {"horizons": {"short": up}, "data_quality": {"score": 100}, "features": {}})
    assert {item.direction.value for item in up_candidates} == {"long", "no_trade"}
    assert not any(item.strategy_family == "trend_follow_short" for item in up_candidates)

    spot_candidates = scan_opportunities([_product(spot=True)], {"horizons": {"short": down}, "data_quality": {"score": 100}, "features": {}})
    assert {item.direction.value for item in spot_candidates} == {"no_trade"}


def test_countertrend_requires_edge_location_and_rejection():
    perp = _product()
    top = _horizon(
        regime="range",
        bias="neutral",
        location=0.90,
        countertrend=True,
        structure={"range_high": 110, "range_low": 90, "resistance_rejection": True},
    )
    top_candidates = scan_opportunities([perp], {"horizons": {"short": top}, "data_quality": {"score": 100}, "features": {}})
    assert {item.direction.value for item in top_candidates} == {"short", "no_trade"}
    short = next(item for item in top_candidates if item.direction == Direction.SHORT)
    assert short.strategy_family == "countertrend_short"
    assert short.target_price <= 110

    bottom = _horizon(
        regime="range",
        bias="neutral",
        location=0.10,
        countertrend=True,
        structure={"range_high": 110, "range_low": 90, "support_rejection": True},
    )
    bottom_candidates = scan_opportunities([perp], {"horizons": {"short": bottom}, "data_quality": {"score": 100}, "features": {}})
    assert {item.direction.value for item in bottom_candidates} == {"long", "no_trade"}
    long = next(item for item in bottom_candidates if item.direction == Direction.LONG)
    assert long.strategy_family == "countertrend_long"
    assert long.target_price >= 90

    center = _horizon(
        regime="range",
        bias="neutral",
        location=0.50,
        countertrend=True,
        structure={"range_high": 110, "range_low": 90, "support_rejection": True, "resistance_rejection": True},
    )
    center_candidates = scan_opportunities([perp], {"horizons": {"short": center}, "data_quality": {"score": 100}, "features": {}})
    assert {item.direction.value for item in center_candidates} == {"no_trade"}

    not_ready = _horizon(
        regime="range",
        bias="neutral",
        location=0.10,
        countertrend=False,
        structure={"range_high": 110, "range_low": 90, "support_rejection": True},
    )
    assert {item.direction.value for item in scan_opportunities([perp], {"horizons": {"short": not_ready}, "data_quality": {"score": 100}, "features": {}})} == {"no_trade"}


def test_insufficient_data_has_no_directional_strategy():
    product = _product()
    horizon = {**_horizon(regime="trend_up"), "analysis_readiness": False, "regime": "insufficient_data"}
    candidates = scan_opportunities([product], {"horizons": {"short": horizon}, "data_quality": {"score": 100}, "features": {}})
    assert {item.direction.value for item in candidates} == {"no_trade"}


def test_horizon_features_change_score_deterministically():
    product = _product()
    fast = _horizon(regime="trend_up", bias="long", trend_strength=10, rsi=54, location=0.85)
    slow = _horizon(regime="trend_up", bias="long", trend_strength=40, rsi=72, location=0.35)
    first = score_candidate(product, Direction.LONG, _snapshot(product, fast), horizon_name="short")
    second = score_candidate(product, Direction.LONG, _snapshot(product, slow), horizon_name="short")
    assert first.heuristic_setup_score != second.heuristic_setup_score
    repeat = score_candidate(product, Direction.LONG, _snapshot(product, slow), horizon_name="short")
    assert second.heuristic_setup_score == repeat.heuristic_setup_score


def test_incompatible_candidate_cannot_enter_shadow():
    product = _product()
    candidate = score_candidate(product, Direction.LONG, _snapshot(product, _horizon(regime="trend_down", bias="short", rsi=40)), horizon_name="short")
    assert candidate.regime_compatibility == "incompatible"
    assert candidate.valid_for_shadow is False
    assert candidate.candidate_stage == "diagnostic_candidate"


def test_triggered_shadow_can_ignore_unknown_cost_but_user_action_cannot():
    product = _product()
    triggered = score_candidate(
        product,
        Direction.LONG,
        _snapshot(product, _horizon(regime="trend_up", trigger_fired=True)),
        horizon_name="short",
    )
    assert triggered.trigger_fired is True
    assert triggered.valid_for_shadow is True
    assert triggered.valid_for_user_execution is False
    assert triggered.cost_unknown is True
    assert triggered.net_edge_bps is None

    watching = score_candidate(
        product,
        Direction.LONG,
        _snapshot(product, _horizon(regime="trend_up", trigger_fired=False)),
        horizon_name="short",
    )
    assert watching.trigger_fired is False
    assert watching.valid_for_shadow is False
    assert watching.candidate_stage == "watching_candidate"


def test_no_trade_decision_has_no_selected_product(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    engine = V2Engine(
        tmp_path,
        V2Settings(mode="fixture", live_enabled=False, duckdb_path=tmp_path / "x.duckdb", parquet_root=tmp_path / "raw"),
    )
    decision = engine._decision_from_snapshot({
        "mode": "live",
        "data_unavailable": False,
        "ranked_candidates": [{
            "candidate_id": "no-trade",
            "product_id": "BTC_BINANCE_BTCUSDT_PERP",
            "direction": "no_trade",
            "candidate_stage": "diagnostic_candidate",
            "candidate_status": "no_trade",
            "execution_permission": "no_trade",
            "setup_quality": "no_trade",
        }],
        "data_quality": {"score": 100},
        "computed_features": {"regime": {}},
        "account_overlay": {},
        "portfolio_constraints": {},
    })
    assert decision["final_action"] == "no_trade"
    assert decision["selected_product_id"] is None
    assert decision["selected_candidate_id"] == "no-trade"


def test_bybit_discovery_malformed_rows_do_not_raise_unboundlocalerror():
    class FakeClient:
        async def get(self, url, params=None, headers=None):
            return JSONResponse({"result": {"list": [
                {"symbol": "", "baseCoin": "BTC", "status": "Trading"},
                {"symbol": "BTCUSDT", "baseCoin": "BTC", "status": "Trading", "contractType": "Linear", "lotSizeFilter": {}, "priceFilter": {}},
            ]}}, 200, {}, 1)

    result = asyncio.run(BybitPublicProvider(client=FakeClient()).discover_products(["BTC"]))
    assert result.quality.value == "ok"
    assert not any("UnboundLocalError" in str(item) for item in result.products)


def test_provider_health_accumulates_real_observation_counts():
    registry = ProviderHealthRegistry()
    registry.success("bybit", observation_count=4, candle_count_by_timeframe={"1m": 3}, operation="market_data")
    registry.success("bybit", observation_count=2, candle_count_by_timeframe={"1m": 2, "5m": 1}, operation="market_data")
    row = registry.all()[0]
    assert row["observation_count"] == 6
    assert row["candle_count_by_timeframe"] == {"1m": 5, "5m": 1}
    assert row["last_data_success_at"]
    assert row["last_discovery_success_at"] is None


def test_default_snapshot_is_compact_and_does_not_duplicate_raw(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    engine = V2Engine(
        tmp_path,
        V2Settings(mode="fixture", live_enabled=False, duckdb_path=tmp_path / "x.duckdb", parquet_root=tmp_path / "raw"),
    )
    snapshot = asyncio.run(engine.build_snapshot(mode="fixture"))
    encoded = json.dumps(snapshot, default=str, separators=(",", ":")).encode()
    assert len(encoded) < 2 * 1024 * 1024
    assert snapshot["facts"] == []
    assert snapshot["raw_included"] is False
    for state in snapshot["computed_features"]["product_snapshots"].values():
        assert state["observations"] == []


def test_default_snapshot_raw_can_be_paged_separately(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    engine = V2Engine(
        tmp_path,
        V2Settings(mode="fixture", live_enabled=False, duckdb_path=tmp_path / "x.duckdb", parquet_root=tmp_path / "raw"),
    )
    snapshot = asyncio.run(engine.build_snapshot(mode="fixture"))
    raw = asyncio.run(engine.build_snapshot(mode="fixture", include_raw=True))
    assert raw["raw_included"] is True
    assert len(raw["facts"]) > 0


def test_ui_inline_scripts_parse_cleanly(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = Path(__file__).resolve().parents[1].joinpath("static", "index.html").read_text()
    blocks = re.findall(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", html, flags=re.I | re.S)
    executable = [
        body for attrs, body in blocks
        if "application/ld+json" not in attrs.lower()
        and body.strip()
    ]
    assert executable
    for index, body in enumerate(executable):
        path = tmp_path / f"inline-{index}.js"
        path.write_text(body)
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
