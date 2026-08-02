from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from engine_v2.config import V2Settings
from engine_v2.domain.enums import DataQuality, Direction, EntryPlan, Horizon
from engine_v2.domain.models import Observation, OpportunityCandidate
from engine_v2.domain.registry import build_default_registry
from engine_v2.features.cross_asset import dynamic_relationship
from engine_v2.ingestion.yfinance_delayed import YFinanceDelayedProvider
from engine_v2.ingestion.official_events import OfficialEventsProvider, _economic_observation
from engine_v2.opportunities.scanner import rank_candidates, scan_opportunities
from engine_v2.opportunities.scorer import score_candidate
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


def test_global_candidate_ranking_is_independent_of_product_order(tmp_path, monkeypatch):
    early_product = {
        "product_id": "EARLY_PRODUCT",
        "direction": "long",
        "valid_for_shadow": True,
        "valid_for_user_execution": False,
        "net_edge_bps": 10,
        "confidence": 0.9,
        "heuristic_setup_score": 5,
        "candidate_status": "research_only_long",
        "execution_permission": "shadow_only",
        "setup_quality": "research",
    }
    later_product = {
        "product_id": "LATER_PRODUCT",
        "direction": "short",
        "valid_for_shadow": True,
        "valid_for_user_execution": False,
        "net_edge_bps": 20,
        "confidence": 0.6,
        "heuristic_setup_score": 4,
        "candidate_status": "research_only_short",
        "execution_permission": "shadow_only",
        "setup_quality": "research",
    }
    assert [item["product_id"] for item in rank_candidates([early_product, later_product])] == [
        "LATER_PRODUCT",
        "EARLY_PRODUCT",
    ]

    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    settings = V2Settings(mode="fixture", live_enabled=False, duckdb_path="data/x.duckdb", parquet_root="data/raw")
    engine = V2Engine(tmp_path, settings)
    snapshot = {
        "mode": "fixture",
        "data_unavailable": False,
        "ranked_candidates": [early_product, later_product, {
            "product_id": "NO_TRADE_PRODUCT",
            "direction": "no_trade",
            "valid_for_shadow": False,
            "valid_for_user_execution": False,
            "candidate_status": "no_trade",
            "execution_permission": "no_trade",
            "setup_quality": "no_trade",
        }],
        "account_overlay": {},
        "data_quality": {"score": 100},
        "computed_features": {"regime": {}},
        "portfolio_constraints": {},
    }
    decision = engine._decision_from_snapshot(snapshot)
    assert decision["final_action"] == "research_only_short"
    assert decision["candidate_rank"][0]["product_id"] == "LATER_PRODUCT"


def test_equal_score_candidates_use_order_independent_product_tiebreak(tmp_path, monkeypatch):
    tie_a = {
        "product_id": "PRODUCT_A",
        "direction": "long",
        "setup_type": "retest",
        "valid_for_shadow": True,
        "valid_for_user_execution": False,
        "net_edge_bps": 10,
        "confidence": 0.5,
        "heuristic_setup_score": 4,
        "candidate_status": "research_only_long",
        "execution_permission": "shadow_only",
        "setup_quality": "research",
    }
    tie_b = {**tie_a, "product_id": "PRODUCT_B"}
    forward = [item["product_id"] for item in rank_candidates([tie_b, tie_a])]
    reverse = [item["product_id"] for item in rank_candidates([tie_a, tie_b])]
    assert forward == reverse == ["PRODUCT_A", "PRODUCT_B"]

    created_at = datetime.now(timezone.utc)
    object_a = OpportunityCandidate(
        candidate_id="candidate-z",
        created_at=created_at,
        product_id="PRODUCT_A",
        direction=Direction.LONG,
        horizon=Horizon.INTRADAY,
        entry_plan=EntryPlan.RETEST,
        invalidation=None,
        setup_type="retest",
        heuristic_setup_score=4,
        net_edge_bps=10,
        confidence=0.5,
        valid_for_shadow=True,
        valid_for_user_execution=False,
    )
    object_b = OpportunityCandidate(
        candidate_id="candidate-a",
        created_at=created_at,
        product_id="PRODUCT_B",
        direction=Direction.LONG,
        horizon=Horizon.INTRADAY,
        entry_plan=EntryPlan.RETEST,
        invalidation=None,
        setup_type="retest",
        heuristic_setup_score=4,
        net_edge_bps=10,
        confidence=0.5,
        valid_for_shadow=True,
        valid_for_user_execution=False,
    )
    assert [item.product_id for item in rank_candidates([object_b, object_a])] == [
        "PRODUCT_A",
        "PRODUCT_B",
    ]

    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    settings = V2Settings(mode="fixture", live_enabled=False, duckdb_path="data/x.duckdb", parquet_root="data/raw")
    engine = V2Engine(tmp_path, settings)
    base_snapshot = {
        "mode": "fixture",
        "data_unavailable": False,
        "ranked_candidates": [tie_b, tie_a],
        "account_overlay": {},
        "data_quality": {"score": 100},
        "computed_features": {"regime": {}},
        "portfolio_constraints": {},
    }
    reversed_snapshot = {**base_snapshot, "ranked_candidates": [tie_a, tie_b]}
    forward_decision = engine._decision_from_snapshot(base_snapshot)
    reverse_decision = engine._decision_from_snapshot(reversed_snapshot)
    assert forward_decision["final_action"] == reverse_decision["final_action"] == "research_only_long"
    assert forward_decision["selected_product_id"] == reverse_decision["selected_product_id"] == "PRODUCT_A"


def test_fixture_weak_directional_candidates_cannot_enter_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    settings = V2Settings(mode="fixture", live_enabled=False, duckdb_path="data/x.duckdb", parquet_root="data/raw")
    engine = V2Engine(tmp_path, settings)
    snapshot = asyncio.run(engine.build_snapshot(live=False))
    directional = [
        item for item in snapshot["ranked_candidates"]
        if item["direction"] in {"long", "short"}
    ]
    assert directional
    assert all(item["valid_for_shadow"] is False for item in directional)
    assert all("heuristic_below_threshold" in item["reason_codes"] for item in directional)
    assert engine.last_decision["final_action"] == "no_trade"
    assert engine.storage.open_candidates() == []


def _strong_shadow_candidate() -> dict:
    product = build_default_registry().product("BTC_BINANCE_PERP").to_dict()
    snapshot = {
        "mode": "live",
        "data_quality": {"score": 100},
        "features": {
            "technical_structure": 1,
            "momentum": 1,
            "orderflow": 1,
            "derivatives": 1,
            "cross_asset": 1,
            "event": 1,
            "liquidity": 1,
            "technical": {
                "latest_close": 60000,
                "atr_14_pct_closed": 0.01,
                "return_4": 0.01,
                "return_24": 0.03,
                "trend_state": "bullish",
            },
        },
        "costs": {"spread_bps": 2, "taker_fee_bps": 2, "estimated_slippage_bps": 1},
        "product_context": {},
        "min_heuristic_score": 3,
    }
    return score_candidate(product, Direction.LONG, snapshot).to_dict()


def test_shadow_outcome_and_calibration_insufficient_sample(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    settings = V2Settings(mode="fixture", live_enabled=False, duckdb_path="data/x.duckdb", parquet_root="data/raw")
    engine = V2Engine(tmp_path, settings)
    asyncio.run(engine.build_snapshot(live=False))
    now = datetime.now(timezone.utc)
    candidate = _strong_shadow_candidate()
    assert candidate["valid_for_shadow"] is True
    assert engine.storage.save_candidates("strong-snapshot", now - timedelta(minutes=15), [candidate]) == 1
    duplicate = {**candidate, "candidate_id": "v2-duplicate"}
    assert engine.storage.save_candidates("strong-snapshot-2", now - timedelta(minutes=14), [duplicate]) == 0
    assert len(engine.storage.open_candidates()) == 1
    candidate = engine.storage.open_candidates()[0]["payload"]
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
    source.extend([
        {"timestamp": (base + timedelta(minutes=15 * (40 + i))).isoformat(), "return": 0.2, "session": "overnight"}
        for i in range(40)
    ])
    relationship = dynamic_relationship(source, target, minimum_samples=30)
    assert relationship["alignment"]["method"] == "utc_floor_nearest"
    assert relationship["alignment"]["source_count"] == 40
    assert relationship["session_overlap_ratio"] == 1.0
    assert relationship["usable"] is True

    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    stale_source = [
        {"timestamp": (old + timedelta(minutes=15 * i)).isoformat(), "return": i / 1000, "session": "regular"}
        for i in range(40)
    ]
    stale_source.append({"timestamp": datetime.now(timezone.utc).isoformat(), "return": 1, "session": "regular"})
    stale_target = [
        {"timestamp": (old + timedelta(minutes=15 * i, seconds=30)).isoformat(), "return": i / 1000, "session": "regular"}
        for i in range(40)
    ]
    stale = dynamic_relationship(stale_source, stale_target, minimum_samples=30)
    assert stale["current_confirmation"]["status"] == "delayed"
    assert stale["current_confirmation"]["latest_aligned_time"].startswith("2020-01-01")
    assert evaluate_product_guard(
        {"product_id": "SOXL_YF", "underlying_id": "SOXL"},
        {"underlying_price_age": 1, "underlying_price_stale": True},
    )["reasons"] == ["underlying_stale"]
    warnings = evaluate_product_guard(
        {"product_id": "HYNIX", "underlying_id": "SK_HYNIX_KRX"},
        {"underlying_close_age": 10, "usd_krw_age": None, "krx_session_state": "regular"},
    )["warnings"]
    assert "usd_krw_age_unavailable" in warnings


def test_official_series_has_no_release_event_timestamp():
    observation = _economic_observation(
        "bls",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        {"series_id": "CUSR0000SA0", "value": "1.0"},
    )
    assert observation.data_type == "economic_series"
    assert observation.source_event_time is None
    assert observation.source_publish_time is None
    assert observation.quality == DataQuality.TIMESTAMP_UNKNOWN
    assert observation.payload["release_timestamp_available"] is False
    result = asyncio.run(OfficialEventsProvider().fetch_events())
    assert result.quality == DataQuality.PLAN_NOT_AVAILABLE
    assert result.reason == "release_timestamp_not_available"


def test_shadow_candidate_stays_open_before_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_STORAGE_BACKEND", "sqlite")
    settings = V2Settings(mode="fixture", live_enabled=False, duckdb_path="data/x.duckdb", parquet_root="data/raw")
    engine = V2Engine(tmp_path, settings)
    candidate = _strong_shadow_candidate()
    now = datetime.now(timezone.utc)
    candidate["time_expiry"] = (now + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    assert engine.storage.save_candidates("expiry-snapshot", now, [candidate]) == 1
    trigger = float(candidate["trigger_price"])
    future = Observation(
        "future-no-trigger",
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
            "open": trigger + 2,
            "high": trigger + 5,
            "low": trigger + 1,
            "close": trigger + 3,
            "is_final": True,
        },
    )
    engine.storage.append_observation(future)
    runner = ShadowRunner(engine, mode="fixture")
    assert runner._settle_open_candidates() == 0
    assert len(engine.storage.open_candidates()) == 1

    expired = _strong_shadow_candidate()
    expired["candidate_id"] = "v2-expired"
    expired["setup_type"] = "expired_setup"
    expired["time_expiry"] = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    assert engine.storage.save_candidates("expired-snapshot", now - timedelta(hours=2), [expired]) == 1
    assert runner._settle_open_candidates() == 1
    assert len(engine.storage.open_candidates()) == 1
    assert engine.storage.evaluation_summary()["outcome_count"] == 1
