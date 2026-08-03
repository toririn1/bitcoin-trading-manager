from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import Direction, EntryPlan, Horizon
from engine_v2.domain.models import OpportunityCandidate

from .costs import estimate_cost_bps, net_edge
from .product_guards import evaluate_product_guard


_HORIZON_EXPIRY_HOURS = {
    "ultra_short": 2,
    "short": 8,
    "medium": 24,
    "long": 72,
    "intraday": 24,
}


def direction_is_semantically_allowed(
    product: dict[str, Any],
    horizon_analysis: dict[str, Any],
    direction: Direction,
) -> bool:
    if direction == Direction.NO_TRADE:
        return True
    if not horizon_analysis or not bool(horizon_analysis.get("analysis_readiness", False)):
        return False
    regime = str(horizon_analysis.get("regime") or "insufficient_data")
    bias = str(horizon_analysis.get("bias") or "")
    if regime == "trend_up":
        return direction == Direction.LONG
    if regime == "trend_down":
        return direction == Direction.SHORT
    if regime == "breakout_transition":
        return (direction.value == bias) and bool(horizon_analysis.get("continuation_readiness"))
    if regime == "compression":
        return direction.value == bias and bool(
            horizon_analysis.get("structure", {}).get("breakout_hold")
            or horizon_analysis.get("structure", {}).get("retest")
            or horizon_analysis.get("bias") in {"long", "short"}
        )
    if regime in {"range", "failed_breakout", "exhaustion"}:
        return _countertrend_direction_allowed(horizon_analysis, direction)
    return False


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
    horizons = snapshot.get("horizons") or {}
    horizon_analysis = horizons.get(horizon_name) or {}
    if not horizons:
        technical_fallback = features.get("technical") or {}
        horizon_analysis = {
            "analysis_readiness": True,
            "regime": "trend_up" if technical_fallback.get("trend_state") in {None, "bullish"} else "trend_down",
            "bias": "long" if technical_fallback.get("trend_state") in {None, "bullish"} else "short",
            "continuation_readiness": True,
            "structure": {"labels": [{"label": "HH" if technical_fallback.get("trend_state") in {None, "bullish"} else "LL"}]},
            "technical": technical_fallback,
        }
    analysis_ready = bool(horizon_analysis.get("analysis_readiness", False))
    regime = str(horizon_analysis.get("regime") or "insufficient_data")
    direction_sign = 1 if direction == Direction.LONG else -1 if direction == Direction.SHORT else 0
    horizon_scores = _horizon_scores(horizon_analysis, direction)
    trend = horizon_scores["trend"]
    momentum = horizon_scores["momentum"]
    structure_score = horizon_scores["structure"]
    volatility_score = horizon_scores["volatility"]
    level_score = horizon_scores["level"]
    orderflow = _score(features.get("orderflow"), 15, direction_sign)
    derivatives = _score(features.get("derivatives"), 15, direction_sign)
    cross_asset = _score(features.get("cross_asset"), 15, direction_sign)
    event = _score(features.get("event"), 10, direction_sign)
    liquidity = _score(features.get("liquidity"), 10, direction_sign)
    product_risk = -abs(float(features.get("product_risk") or 0))
    portfolio_fit = float(features.get("portfolio_fit") or 0)
    heuristic = (
        trend
        + momentum
        + structure_score
        + volatility_score
        + level_score
        + orderflow
        + derivatives
        + cross_asset
        + event
        + liquidity
        + product_risk
        + portfolio_fit
    )

    quality_score = float(quality.get("score") or 0)
    product_context = snapshot.get("product_context", {})
    guard = evaluate_product_guard(product, product_context)
    costs = estimate_cost_bps(product, snapshot.get("costs", {}))
    calibrated = _calibrated_edge(snapshot, product.get("product_id"), direction.value)
    gross_edge = _number(calibrated.get("gross_edge_bps"))
    edge = net_edge(gross_edge, costs)
    edge_quality = str(calibrated.get("status") or ("calibrated" if edge is not None else "uncalibrated"))

    created_at = datetime.now(timezone.utc)
    entry = _entry_plan(
        product,
        direction,
        features,
        created_at,
        snapshot,
        horizon_name=horizon_name,
        horizon_analysis=horizon_analysis,
    )
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
        if entry["regime_compatibility"] == "incompatible":
            gate_reasons.append("regime_incompatible")
        elif entry["regime_compatibility"] == "insufficient_data":
            gate_reasons.append("regime_insufficient_data")
        if not entry["trigger_fired"]:
            gate_reasons.append("trigger_not_fired")
        if snapshot.get("mode") == "fixture":
            reasons.append("fixture_synthetic")
        if not guard.get("allowed", True):
            gate_reasons.extend(guard.get("reasons", []))
        # Costs/calibration are execution gates, never technical shadow gates.
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
    technical_eligible = (
        direction != Direction.NO_TRADE
        and entry["trigger_price"] is not None
        and entry["trigger_fired"]
        and quality_score >= 55
        and analysis_ready
        and heuristic >= heuristic_threshold
        and entry["regime_compatibility"] in {"compatible", "conditional"}
        and guard.get("allowed", True)
    )
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
        valid_for_shadow = technical_eligible
        valid_for_user_execution = not gate_reasons
        candidate_status = (
            f"actionable_{direction.value}"
            if valid_for_user_execution
            else f"research_only_{direction.value}"
        )
        permission = "manual_confirmation_required" if valid_for_user_execution else "shadow_only"
        plan = entry["entry_plan"]
        setup_quality = "actionable" if valid_for_user_execution else (
            "triggered_shadow" if valid_for_shadow else "watching"
        )

    invalidation_reason = _invalidation_reason(unique_reasons, entry)
    if valid_for_user_execution:
        candidate_stage = "user_actionable_candidate"
    elif valid_for_shadow:
        candidate_stage = "triggered_shadow_candidate"
    elif (
        direction != Direction.NO_TRADE
        and entry["trigger_price"] is not None
        and analysis_ready
        and entry["regime_compatibility"] in {"compatible", "conditional"}
    ):
        candidate_stage = "watching_candidate"
    else:
        candidate_stage = "diagnostic_candidate"

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
        structure_score=structure_score,
        trend_score=trend,
        volatility_score=volatility_score,
        level_score=level_score,
        orderflow_confirmation=entry["orderflow_confirmation"],
        regime_compatibility=entry["regime_compatibility"],
        analysis_readiness=analysis_ready,
        failure_conditions=entry["failure_conditions"],
        candidate_stage=candidate_stage,
        trigger_fired=entry["trigger_fired"],
        trigger_fired_at=entry["trigger_fired_at"],
        trigger_evidence=entry["trigger_evidence"],
        watched_since=created_at if entry["trigger_price"] is not None and analysis_ready else None,
        cost_unknown=costs.get("estimated_cost_bps") is None,
        gross_return=None,
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
    analysis = horizon_analysis or {}
    if direction == Direction.NO_TRADE:
        return _empty_entry(analysis, "not_applicable", strategy_family="no_trade")
    technical = {**(features.get("technical") or {}), **(analysis.get("technical") or {})}
    structure = analysis.get("structure") or technical.get("structure") or {}
    latest = _number(technical.get("latest_close")) or _number(features.get("latest_close"))
    if latest is None or latest <= 0:
        return _empty_entry(analysis, "insufficient_data", strategy_family="no_trade", failure=["latest_close_missing"])
    if not bool(analysis.get("analysis_readiness", True)):
        return _empty_entry(analysis, "insufficient_data", strategy_family="no_trade", failure=["analysis_not_ready"])

    regime = str(analysis.get("regime") or "insufficient_data")
    if not direction_is_semantically_allowed(product, analysis, direction):
        compatibility = "insufficient_data" if regime == "insufficient_data" or not analysis.get("analysis_readiness", True) else "incompatible"
        return _empty_entry(analysis, compatibility, strategy_family="incompatible_direction", failure=["direction_regime_mismatch"])

    atr_pct = _number(technical.get("atr_14_pct_closed"))
    atr = latest * (atr_pct if atr_pct and atr_pct > 0 else 0.005)
    location = _number(analysis.get("current_location"))
    range_high = _number(structure.get("range_high"))
    range_low = _number(structure.get("range_low"))
    if range_high is None:
        range_high = _number(analysis.get("resistance"))
    if range_low is None:
        range_low = _number(analysis.get("support"))
    midpoint = ((range_high + range_low) / 2) if range_high is not None and range_low is not None else latest
    explicit_fired = analysis.get("trigger_fired")
    evidence: dict[str, Any] = {
        "horizon": horizon_name,
        "regime": regime,
        "location": location,
        "structure": {
            key: structure.get(key)
            for key in ("bos", "retest", "failed_breakout", "sweep_reclaim", "breakout_hold")
            if key in structure
        },
    }

    if regime in {"range", "failed_breakout", "exhaustion"}:
        long_allowed = direction == Direction.LONG
        setup_type = "liquidity_sweep_reclaim" if structure.get("sweep_reclaim") else "range_edge_rejection"
        support_evidence = bool(
            structure.get("support_rejection")
            or structure.get("rejection") == "support"
            or (structure.get("sweep_reclaim") and location is not None and location <= 0.25)
        )
        resistance_evidence = bool(
            structure.get("resistance_rejection")
            or structure.get("rejection") == "resistance"
            or (structure.get("sweep_reclaim") and location is not None and location >= 0.75)
        )
        evidence["support_rejection"] = support_evidence
        evidence["resistance_rejection"] = resistance_evidence
        trigger = latest
        if long_allowed:
            target = min(range_high if range_high is not None else latest + atr * 2, midpoint)
            stop = trigger - atr
            evidence["trigger"] = "support_rejection_or_sweep_reclaim"
            fired = support_evidence
        else:
            target = max(range_low if range_low is not None else latest - atr * 2, midpoint)
            stop = trigger + atr
            evidence["trigger"] = "resistance_rejection_or_sweep_reclaim"
            fired = resistance_evidence
        rr = abs(target - trigger) / max(abs(trigger - stop), 1e-12)
        entry_plan = EntryPlan.CONDITIONAL_TRIGGER
        strategy_family = "countertrend_long" if long_allowed else "countertrend_short"
        compatibility = "compatible"
    elif regime == "compression":
        sign = 1 if direction == Direction.LONG else -1
        strategy_family = "compression_breakout"
        setup_type = "compression_breakout"
        boundary = range_high if sign > 0 else range_low
        trigger = (boundary + sign * atr * 0.10) if boundary is not None else latest + sign * atr
        stop = trigger - sign * atr
        target = trigger + sign * atr * 2
        rr = 2.0
        fired = latest >= trigger if sign > 0 else latest <= trigger
        entry_plan = EntryPlan.BREAKOUT_CONFIRMATION
        compatibility = "conditional"
        evidence["trigger"] = "close_beyond_compression_boundary"
    else:
        sign = 1 if direction == Direction.LONG else -1
        strategy_family = "trend_follow_long" if direction == Direction.LONG else "trend_follow_short"
        setup_type = "breakout_retest" if structure.get("retest") else "trend_pullback"
        trigger = latest if structure.get("labels") or analysis.get("continuation_readiness") else latest + sign * atr * 0.10
        stop = trigger - sign * atr * 1.5
        target = trigger + sign * atr * 3.0
        rr = 2.0
        fired = trigger == latest or (latest >= trigger if sign > 0 else latest <= trigger)
        entry_plan = EntryPlan.RETEST if setup_type == "breakout_retest" else EntryPlan.CONDITIONAL_TRIGGER
        compatibility = "compatible"
        evidence["trigger"] = "closed_structure_continuation"

    if explicit_fired is not None:
        fired = bool(explicit_fired)
    trigger_time = created_at if fired else None
    return {
        "entry_plan": entry_plan,
        "setup_type": setup_type,
        "strategy_family": strategy_family,
        "trigger_price": trigger,
        "stop_price": stop,
        "target_price": target,
        "time_expiry": created_at + timedelta(hours=_HORIZON_EXPIRY_HOURS.get(horizon_name, 24)),
        "risk_reward": rr,
        "trigger_condition": f"closed_{analysis.get('trigger_timeframe') or '15m'}_candle_confirms_{direction.value}",
        "entry_zone": {"low": min(trigger, latest), "high": max(trigger, latest)},
        "context_timeframe": analysis.get("context_timeframe"),
        "setup_timeframe": analysis.get("setup_timeframe"),
        "trigger_timeframe": analysis.get("trigger_timeframe"),
        "structure_score": 1.0 if structure.get("labels") else 0.0,
        "volatility_score": 1.0 if technical.get("expansion") or technical.get("compression") else 0.0,
        "level_score": 1.0 if range_high is not None or range_low is not None else 0.0,
        "orderflow_confirmation": "available" if features.get("orderflow") is not None else "missing",
        "regime_compatibility": compatibility,
        "failure_conditions": ["stop_price", "structure_break", "time_expiry"],
        "trigger_fired": bool(fired),
        "trigger_fired_at": trigger_time,
        "trigger_evidence": evidence,
    }


def _empty_entry(
    analysis: dict[str, Any],
    compatibility: str,
    *,
    strategy_family: str,
    failure: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "entry_plan": EntryPlan.NO_ENTRY,
        "setup_type": "no_trade" if strategy_family == "no_trade" else "no_entry",
        "strategy_family": strategy_family,
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
        "regime_compatibility": compatibility,
        "failure_conditions": failure or [],
        "trigger_fired": False,
        "trigger_fired_at": None,
        "trigger_evidence": {},
    }


def _countertrend_direction_allowed(analysis: dict[str, Any], direction: Direction) -> bool:
    if not analysis.get("countertrend_readiness"):
        return False
    location = _number(analysis.get("current_location"))
    if location is None:
        return False
    structure = analysis.get("structure") or {}
    if direction == Direction.LONG:
        return location <= 0.25 and bool(
            structure.get("support_rejection")
            or structure.get("rejection") == "support"
            or structure.get("sweep_reclaim")
        )
    if direction == Direction.SHORT:
        return location >= 0.75 and bool(
            structure.get("resistance_rejection")
            or structure.get("rejection") == "resistance"
            or structure.get("sweep_reclaim")
        )
    return False


def _horizon_scores(analysis: dict[str, Any], direction: Direction) -> dict[str, float]:
    if direction == Direction.NO_TRADE:
        return {"trend": 0.0, "momentum": 0.0, "structure": 0.0, "volatility": 0.0, "level": 0.0}
    sign = 1 if direction == Direction.LONG else -1
    bias = str(analysis.get("bias") or "")
    bias_sign = 1 if bias == "long" else -1 if bias == "short" else 0
    trend_strength = _number(analysis.get("trend_strength")) or 0.0
    trend = max(-20.0, min(20.0, (trend_strength / 5.0) * sign * bias_sign))
    momentum_value = _number(analysis.get("momentum_state"))
    momentum = 0.0 if momentum_value is None else max(-10.0, min(10.0, ((momentum_value - 50.0) / 5.0) * sign))
    structure = analysis.get("structure") or {}
    labels = structure.get("labels") or []
    last_label = str(labels[-1].get("label") if labels and isinstance(labels[-1], dict) else "")
    structure_alignment = 1 if (sign > 0 and last_label in {"HH", "HL"}) or (sign < 0 and last_label in {"LH", "LL"}) else -1 if last_label else 0
    structure_score = float(structure_alignment * 8)
    if (sign > 0 and structure.get("bos") == "bullish") or (sign < 0 and structure.get("bos") == "bearish"):
        structure_score += 5
    if (sign > 0 and structure.get("bos") == "bearish") or (sign < 0 and structure.get("bos") == "bullish"):
        structure_score -= 5
    volatility_state = analysis.get("volatility_state")
    volatility = 0.0
    if _number(volatility_state) is not None:
        volatility = max(-5.0, min(8.0, abs(float(volatility_state)) * 100))
    elif str(analysis.get("regime")) == "compression":
        volatility = 2.0
    elif str(analysis.get("regime")) == "breakout_transition":
        volatility = 6.0
    location = _number(analysis.get("current_location"))
    level = 0.0
    if location is not None:
        if sign > 0:
            level = max(-5.0, min(8.0, (0.5 - location) * 12))
        else:
            level = max(-5.0, min(8.0, (location - 0.5) * 12))
    return {
        "trend": trend,
        "momentum": momentum,
        "structure": structure_score,
        "volatility": volatility,
        "level": level,
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
