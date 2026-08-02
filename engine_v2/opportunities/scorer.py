from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import Direction, EntryPlan, Horizon, quality_penalty
from engine_v2.domain.models import OpportunityCandidate

from .costs import estimate_cost_bps, net_edge
from .product_guards import evaluate_product_guard


def score_candidate(
    product: dict[str, Any],
    direction: Direction,
    snapshot: dict[str, Any],
    *,
    min_net_edge_bps: float = 8.0,
    horizon_name: str = "intraday",
) -> OpportunityCandidate:
    features = snapshot.get("features", {})
    quality = snapshot.get("data_quality", {})
    horizon_analysis = (snapshot.get("horizons") or {}).get(horizon_name) or {}
    analysis_ready = bool(horizon_analysis.get("analysis_readiness", True))
    regime = str(horizon_analysis.get("regime") or (features.get("regime") or {}).get("state") or "unknown")
    direction_sign = 1 if direction == Direction.LONG else -1 if direction == Direction.SHORT else 0
    trend = _score(features.get("technical_structure"), 20, direction_sign)
    momentum = _score(features.get("momentum"), 10, direction_sign)
    orderflow = _score(features.get("orderflow"), 15, direction_sign)
    derivatives = _score(features.get("derivatives"), 15, direction_sign)
    cross_asset = _score(features.get("cross_asset"), 15, direction_sign)
    event = _score(features.get("event"), 10, direction_sign)
    liquidity = _score(features.get("liquidity"), 10, direction_sign)
    product_risk = -abs(float(features.get("product_risk") or 0))
    portfolio_fit = float(features.get("portfolio_fit") or 0)
    heuristic = trend + momentum + orderflow + derivatives + cross_asset + event + liquidity + product_risk + portfolio_fit

    quality_score = float(quality.get("score") or 0)
    product_context = snapshot.get("product_context", {})
    guard = evaluate_product_guard(product, product_context)
    costs = estimate_cost_bps(product, snapshot.get("costs", {}))
    calibrated = _calibrated_edge(snapshot, product.get("product_id"), direction.value)
    gross_edge = _number(calibrated.get("gross_edge_bps"))
    edge = net_edge(gross_edge, costs)
    edge_quality = str(calibrated.get("status") or ("calibrated" if edge is not None else "uncalibrated"))

    created_at = datetime.now(timezone.utc)
    entry = _entry_plan(product, direction, features, created_at, snapshot, horizon_name=horizon_name, horizon_analysis=horizon_analysis)
    reasons: list[str] = ["no_trade_candidate"] if direction == Direction.NO_TRADE else []
    risks = list(guard.get("warnings", [])) + list(guard.get("reasons", []))
    gate_reasons: list[str] = []
    heuristic_threshold = float(snapshot.get("min_heuristic_score") or 3.0)

    if direction != Direction.NO_TRADE:
        if heuristic < heuristic_threshold:
            gate_reasons.append("heuristic_below_threshold")
        if entry["trigger_price"] is None:
            gate_reasons.append("trigger_missing")
        if quality_score < 55:
            gate_reasons.append("data_quality_gate")
        if not analysis_ready:
            gate_reasons.append("analysis_readiness_gate")
        if snapshot.get("mode") == "fixture":
            reasons.append("fixture_synthetic")
        if not guard.get("allowed", True):
            gate_reasons.extend(guard.get("reasons", []))
        if costs.get("estimated_cost_bps") is None:
            gate_reasons.append("cost_missing")
            gate_reasons.extend(costs.get("missing") or [])
        if edge is None:
            gate_reasons.append("edge_uncalibrated")
        if edge is not None and edge < min_net_edge_bps:
            gate_reasons.append("net_edge_below_threshold")
        if entry["risk_reward"] is not None and entry["risk_reward"] < float(snapshot.get("min_rr", 1.5)):
            gate_reasons.append("risk_reward_below_minimum")

    reasons.extend(gate_reasons)
    unique_reasons = list(dict.fromkeys(reasons))
    if direction == Direction.NO_TRADE:
        candidate_status = "no_trade"
        valid_for_shadow = False
        valid_for_user_execution = False
        permission = "no_trade"
        plan = EntryPlan.NO_ENTRY
        setup_quality = "no_trade"
    elif entry["trigger_price"] is None:
        candidate_status = "data_unavailable"
        valid_for_shadow = False
        valid_for_user_execution = False
        permission = "data_unavailable"
        plan = EntryPlan.NO_ENTRY
        setup_quality = "data_unavailable"
    else:
        candidate_status = (
            f"actionable_{direction.value}"
            if not gate_reasons
            else f"research_only_{direction.value}"
        )
        valid_for_shadow = quality_score >= 55 and heuristic >= heuristic_threshold and analysis_ready
        valid_for_user_execution = not gate_reasons
        permission = "manual_confirmation_required" if valid_for_user_execution else "shadow_only"
        plan = entry["entry_plan"]
        setup_quality = "actionable" if valid_for_user_execution else "research"

    invalidation_reason = _invalidation_reason(unique_reasons, entry)
    candidate_stage = (
        "user_actionable_candidate" if valid_for_user_execution
        else "shadow_eligible_candidate" if valid_for_shadow
        else "diagnostic_candidate"
    )
    return OpportunityCandidate(
        candidate_id=f"v2-{uuid4().hex[:12]}",
        created_at=created_at,
        product_id=str(product.get("product_id")),
        direction=direction,
        horizon=_horizon_value(horizon_name),
        entry_plan=plan,
        invalidation=invalidation_reason,
        targets=[entry["target_price"]] if entry["target_price"] is not None else [],
        setup_quality=setup_quality,
        heuristic_setup_score=heuristic,
        edge_quality=edge_quality,
        cost_quality=costs.get("quality", "missing"),
        mode=str(snapshot.get("mode") or "live"),
        session=product.get("trading_session"),
        technical_score=trend,
        momentum_score=momentum,
        orderflow_score=orderflow,
        derivatives_score=derivatives,
        cross_asset_score=cross_asset,
        event_score=event,
        data_quality_score=quality_score,
        liquidity_score=liquidity,
        product_risk_score=product_risk,
        portfolio_fit_score=portfolio_fit,
        gross_edge_bps=gross_edge,
        estimated_cost_bps=costs.get("estimated_cost_bps"),
        net_edge_bps=edge,
        confidence=_confidence(quality_score, heuristic, edge_quality),
        reason_codes=unique_reasons,
        risk_codes=list(dict.fromkeys(risks)),
        source_snapshot_id=snapshot.get("snapshot_id"),
        valid=direction == Direction.NO_TRADE or valid_for_shadow or valid_for_user_execution,
        candidate_status=candidate_status,
        valid_for_shadow=valid_for_shadow,
        valid_for_user_execution=valid_for_user_execution,
        setup_type=entry["setup_type"],
        trigger_price=entry["trigger_price"],
        stop_price=entry["stop_price"],
        target_price=entry["target_price"],
        time_expiry=entry["time_expiry"],
        invalidation_reason=invalidation_reason,
        execution_permission=permission,
        calibration_group={
            "product_id": product.get("product_id"),
            "direction": direction.value,
            "setup": entry["setup_type"],
            "horizon": horizon_name,
            "regime": regime,
        },
        strategy_family=entry["strategy_family"],
        regime=regime,
        context_timeframe=entry["context_timeframe"],
        setup_timeframe=entry["setup_timeframe"],
        trigger_timeframe=entry["trigger_timeframe"],
        trigger_condition=entry["trigger_condition"],
        entry_zone=entry["entry_zone"],
        expiry=entry["time_expiry"],
        risk_reward=entry["risk_reward"],
        structure_score=entry["structure_score"],
        trend_score=trend,
        volatility_score=entry["volatility_score"],
        level_score=entry["level_score"],
        orderflow_confirmation=entry["orderflow_confirmation"],
        regime_compatibility=entry["regime_compatibility"],
        analysis_readiness=analysis_ready,
        failure_conditions=entry["failure_conditions"],
        candidate_stage=candidate_stage,
    )


def _entry_plan(
    product: dict[str, Any],
    direction: Direction,
    features: dict[str, Any],
    created_at: datetime,
    snapshot: dict[str, Any],
    *,
    horizon_name: str = "intraday",
    horizon_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if direction == Direction.NO_TRADE:
        return {
            "entry_plan": EntryPlan.NO_ENTRY,
            "setup_type": "no_trade",
            "strategy_family": "no_trade",
            "trigger_price": None,
            "stop_price": None,
            "target_price": None,
            "time_expiry": None,
            "risk_reward": None,
            "trigger_condition": None,
            "entry_zone": None,
            "context_timeframe": None,
            "setup_timeframe": None,
            "trigger_timeframe": None,
            "structure_score": None,
            "volatility_score": None,
            "level_score": None,
            "orderflow_confirmation": None,
            "regime_compatibility": "not_applicable",
            "failure_conditions": [],
        }
    analysis = horizon_analysis or {}
    technical = {**(features.get("technical") or {}), **(analysis.get("technical") or {})}
    structure = analysis.get("structure") or technical.get("structure") or {}
    latest = _number(technical.get("latest_close")) or _number(features.get("latest_close"))
    if latest is None or latest <= 0:
        return {
            "entry_plan": EntryPlan.NO_ENTRY,
            "setup_type": "no_entry",
            "strategy_family": "no_trade",
            "trigger_price": None,
            "stop_price": None,
            "target_price": None,
            "time_expiry": None,
            "risk_reward": None,
            "trigger_condition": None,
            "entry_zone": None,
            "context_timeframe": analysis.get("context_timeframe"),
            "setup_timeframe": analysis.get("setup_timeframe"),
            "trigger_timeframe": analysis.get("trigger_timeframe"),
            "structure_score": 0.0,
            "volatility_score": 0.0,
            "level_score": 0.0,
            "orderflow_confirmation": "missing",
            "regime_compatibility": "insufficient_data",
            "failure_conditions": ["latest_close_missing"],
        }
    sign = 1 if direction == Direction.LONG else -1
    atr_pct = _number(technical.get("atr_14_pct_closed"))
    atr = latest * (atr_pct if atr_pct and atr_pct > 0 else 0.005)
    regime = str(analysis.get("regime") or "range")
    failed_breakout = bool(structure.get("failed_breakout") or structure.get("sweep_reclaim"))
    strong_trend = regime in {"trend_up", "trend_down"} and abs(_number(technical.get("adx_14")) or 0) >= 20
    if failed_breakout and not strong_trend:
        strategy_family = "countertrend_long" if direction == Direction.LONG else "countertrend_short"
        setup_type = "liquidity_sweep_reclaim" if structure.get("sweep_reclaim") else "failed_breakout_reversion"
        plan = EntryPlan.CONDITIONAL_TRIGGER
        trigger = latest + sign * atr * 0.05
        rr = 1.5
    elif regime in {"trend_up", "trend_down", "breakout_transition"}:
        strategy_family = "trend_follow_long" if direction == Direction.LONG else "trend_follow_short"
        if structure.get("retest"):
            setup_type = "breakout_retest"
        elif technical.get("compression"):
            setup_type = "compression_breakout"
        else:
            setup_type = "trend_pullback"
        plan = EntryPlan.RETEST if setup_type in {"breakout_retest", "trend_pullback"} else EntryPlan.BREAKOUT_CONFIRMATION
        trigger = latest + sign * atr * 0.10
        rr = 2.0
    else:
        strategy_family = "countertrend_long" if direction == Direction.LONG else "countertrend_short"
        setup_type = "range_edge_rejection"
        plan = EntryPlan.CONDITIONAL_TRIGGER
        trigger = latest + sign * atr * 0.05
        rr = 1.5
    risk = max(atr * (1.0 if strategy_family.startswith("countertrend") else 1.5), latest * 0.002)
    stop = trigger - sign * risk
    target = trigger + sign * risk * rr
    expiry_hours = {"ultra_short": 2, "short": 8, "medium": 24, "long": 72, "intraday": 24}.get(horizon_name, 24)
    return {
        "entry_plan": plan,
        "setup_type": setup_type,
        "strategy_family": strategy_family,
        "trigger_price": trigger,
        "stop_price": stop,
        "target_price": target,
        "time_expiry": created_at + timedelta(hours=expiry_hours),
        "risk_reward": rr,
        "trigger_condition": f"closed_{analysis.get('trigger_timeframe') or '15m'}_candle_confirms_{direction.value}",
        "entry_zone": {"low": min(trigger, latest), "high": max(trigger, latest)},
        "context_timeframe": analysis.get("context_timeframe"),
        "setup_timeframe": analysis.get("setup_timeframe"),
        "trigger_timeframe": analysis.get("trigger_timeframe"),
        "structure_score": 1.0 if structure.get("labels") else 0.0,
        "volatility_score": 1.0 if technical.get("expansion") or technical.get("compression") is not None else 0.0,
        "level_score": 1.0 if structure.get("range_high") or structure.get("range_low") else 0.0,
        "orderflow_confirmation": "available" if features.get("orderflow") is not None else "missing",
        "regime_compatibility": "compatible" if not (strong_trend and strategy_family.startswith("countertrend")) else "blocked",
        "failure_conditions": ["stop_price", "structure_break", "time_expiry"],
    }


def _horizon_value(value: str) -> Horizon:
    try:
        return Horizon(value)
    except ValueError:
        return Horizon.INTRADAY


def _invalidation_reason(reasons: list[str], entry: dict[str, Any]) -> str:
    if reasons:
        return ",".join(reasons)
    if entry.get("stop_price") is not None:
        return "stop_price_or_time_expiry"
    return "entry_plan_invalid"


def _calibrated_edge(snapshot: dict[str, Any], product_id: str | None, direction: str) -> dict[str, Any]:
    values = snapshot.get("calibrated_edges") or {}
    value = values.get(product_id, {}).get(direction) if isinstance(values.get(product_id), dict) else None
    return value if isinstance(value, dict) else {}


def _score(value: Any, maximum: float, sign: int) -> float:
    if value is None:
        return 0.0
    try:
        return max(-maximum, min(maximum, float(value) * sign))
    except (TypeError, ValueError):
        return 0.0


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _confidence(quality_score: float, heuristic: float, edge_quality: str) -> float | None:
    if quality_score <= 0:
        return None
    calibration_bonus = 12 if edge_quality == "calibrated" else 0
    return max(0.0, min(1.0, (quality_score + max(-50.0, min(50.0, heuristic)) + calibration_bonus) / 150.0))
