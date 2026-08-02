#!/usr/bin/env python3
"""Dependency-light regression checks for the manual dashboard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyzer
import decision_support
import market_context
import server
from agents.judge import JudgeResult
from agents.risk_triad import RiskTriadResult
from analysis_performance import evaluate_analysis_record
from decision_bridge import (
    apply_trade_quality,
    build_decision_support,
    ensure_api_compatibility,
    format_decision_context,
)
from scripts.backfill_performance import enrich_record


def main():
    # Market helpers.
    assert decision_support.oi_changes([100, 110, 121])["1h"] == 10.0
    rebate = decision_support.rebate_metrics(10, 3, 100, 0.7)
    assert rebate["expected_rebate_pending"] == 4.0

    # R:R and conditional language control setup quality, not market direction.
    rr_case = build_decision_support(
        {"market_direction": "상방 우위"},
        {"account_equity": 1000, "today_fee_paid": 0},
    )
    apply_trade_quality(
        rr_case,
        100,
        {"entry": 100, "stop": 95, "target": 103},
        "지금 롱 진입",
    )
    assert rr_case["market_direction"] == "상방 우위"
    assert rr_case["setup_action_verdict"] == "wait_for_trigger"
    assert rr_case["entry_expectancy"] == "poor"

    conditional = build_decision_support(
        {"market_direction": "상방 우위"},
        {"account_equity": 1000, "today_fee_paid": 0},
    )
    apply_trade_quality(
        conditional,
        63212,
        {"entry": 63212, "stop": 62930, "target": 64000},
        "$63,610 15m 종가 돌파 후 리테스트 지지 시 롱",
    )
    assert conditional["setup_action_verdict"] == "wait_for_trigger"
    assert conditional["entry_expectancy"] in {"conditional_good", "acceptable"}
    assert conditional["trigger_condition"]

    # Gross warning cannot remain allow; conservative net ratio selects the tier.
    fee_blocked = build_decision_support(
        {"market_direction": "상방 우위"},
        {
            "account_equity": 1195.25,
            "today_fee_paid": 384.007332,
            "today_rebate_received": 243.98274,
            "expected_rebate_pending": 24.8223924,
        },
    )
    assert fee_blocked["account_execution_permission"] in {"blocked", "hard_block"}
    assert fee_blocked["market_direction"] == "상방 우위"
    assert "new_entry_when_blocked" in fee_blocked["forbidden_action_codes"]

    reduced = build_decision_support(
        {"market_direction": "상방 우위"},
        {"account_equity": 1000, "today_fee_paid": 30},
    )
    assert reduced["account_execution_permission"] == "reduce_size_only"
    assert "new_entry_when_blocked" not in reduced["forbidden_action_codes"]
    assert "high_leverage" in reduced["forbidden_action_codes"]

    # Missing Gate/CoinGlass-like optional data is a reason, not an exception/block.
    missing_gate = build_decision_support({"market_direction": "중립"}, {"account_equity": 1000})
    assert missing_gate["fee_summary"]["reason"] == "fee history unavailable"
    assert missing_gate["account_execution_permission"] not in {"blocked", "hard_block"}
    assert "market_direction" in format_decision_context(missing_gate)

    # Migration-safe API defaults and UI-missing-field serialization.
    payload = ensure_api_compatibility({"signal": "매수", "decision_support": {"source_labels": None}})
    decision = payload["decision_support"]
    assert decision["execution_permission"] == "manual_confirm_required"
    assert decision["action_verdict"] == "wait_for_trigger"
    json.dumps(payload, ensure_ascii=False)

    # Judge/Risk defaults are structured even when LLMs are disabled.
    judge = JudgeResult(False, "", "", "", "", "").to_payload()
    risk = RiskTriadResult(False, 0).to_payload()
    assert judge["market_view"] and judge["account_permission"]
    assert risk["market_view"] and risk["account_permission"]

    # History evaluation supports both computed and null fallback paths.
    record = {"price": 100, "signal": "매수", "trade_levels": {"stop": 95, "target": 105}}
    candles = [
        {"close": 101, "high": 102, "low": 99},
        {"close": 102, "high": 103, "low": 100},
        {"close": 103, "high": 104, "low": 101},
        {"close": 104, "high": 105, "low": 102},
    ]
    perf = evaluate_analysis_record(record, candles)
    assert perf["return_30m"] is not None and perf["return_4h"] is not None
    assert evaluate_analysis_record(record, None)["return_30m"] is None
    timed_record = {**record, "timestamp": "2026-07-10T00:00:00+00:00"}
    timed_candles = [
        {"timestamp": "2026-07-10T00:30:00+00:00", "close": 101, "high": 102, "low": 99},
        {"timestamp": "2026-07-10T01:00:00+00:00", "close": 102, "high": 103, "low": 100},
        {"timestamp": "2026-07-10T02:00:00+00:00", "close": 103, "high": 104, "low": 101},
        {"timestamp": "2026-07-10T04:00:00+00:00", "close": 104, "high": 105, "low": 102},
    ]
    backfilled = enrich_record(timed_record, timed_candles)
    assert backfilled["return_30m"] == 1.0
    assert backfilled["return_1h"] == 2.0
    assert backfilled["return_4h"] == 4.0
    assert enrich_record({**record, "timestamp": None}, timed_candles)["return_30m"] is None

    print("smoke_test: ok")


if __name__ == "__main__":
    main()
