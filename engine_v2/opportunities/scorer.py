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
) -> OpportunityCandidate:
    features = snapshot.get("features", {})
    quality = snapshot.get("data_quality", {})
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
    entry = _entry_plan(product, direction, features, created_at, snapshot)
    reasons: list[str] = ["no_trade_candidate"] if direction == Direction.NO_TRADE else []
    risks = list(guard.get("warnings", [])) + list(guard.get("reasons", []))
    gate_reasons: list[str] = []

    if direction != Direction.NO_TRADE:
        if entry["trigger_price"] is None:
            gate_reasons.append("trigger_missing")
        if quality_score < 55:
            gate_reasons.append("data_quality_gate")
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
        valid_for_shadow = quality_score >= 55
        valid_for_user_execution = not gate_reasons
        permission = "manual_confirmation_required" if valid_for_user_execution else "shadow_only"
        plan = entry["entry_plan"]
        setup_quality = "actionable" if valid_for_user_execution else "research"

    invalidation_reason = _invalidation_reason(unique_reasons, entry)
    return OpportunityCandidate(
        candidate_id=f"v2-{uuid4().hex[:12]}",
        created_at=created_at,
        product_id=str(product.get("product_id")),
        direction=direction,
        horizon=Horizon.INTRADAY,
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
            "horizon": Horizon.INTRADAY.value,
            "regime": (features.get("regime") or {}).get("state") or "unknown",
        },
    )


def _entry_plan(
    product: dict[str, Any],
    direction: Direction,
    features: dict[str, Any],
    created_at: datetime,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    if direction == Direction.NO_TRADE:
        return {
            "entry_plan": EntryPlan.NO_ENTRY,
            "setup_type": "no_trade",
            "trigger_price": None,
            "stop_price": None,
            "target_price": None,
            "time_expiry": None,
            "risk_reward": None,
        }
    technical = features.get("technical") or {}
    latest = _number(technical.get("latest_close")) or _number(features.get("latest_close"))
    if latest is None or latest <= 0:
        return {
            "entry_plan": EntryPlan.NO_ENTRY,
            "setup_type": "no_entry",
            "trigger_price": None,
            "stop_price": None,
            "target_price": None,
            "time_expiry": None,
            "risk_reward": None,
        }
    sign = 1 if direction == Direction.LONG else -1
    atr_pct = _number(technical.get("atr_14_pct_closed"))
    atr = latest * (atr_pct if atr_pct and atr_pct > 0 else 0.005)
    return_4 = _number(technical.get("return_4")) or 0.0
    return_24 = _number(technical.get("return_24")) or 0.0
    trend = str(technical.get("trend_state") or "")
    aligned = (direction == Direction.LONG and trend == "bullish") or (direction == Direction.SHORT and trend == "bearish")
    if aligned and abs(return_24) >= 0.003:
        setup_type = "breakout"
        plan = EntryPlan.BREAKOUT_CONFIRMATION
        trigger = latest + sign * max(atr * 0.10, latest * 0.0005)
    elif return_4 * sign < 0:
        setup_type = "pullback"
        plan = EntryPlan.LIMIT_PULLBACK
        trigger = latest - sign * max(atr * 0.10, latest * 0.0005)
    else:
        setup_type = "retest"
        plan = EntryPlan.RETEST
        trigger = latest + sign * atr * 0.05
    risk = max(atr * 1.5, latest * 0.002)
    stop = trigger - sign * risk
    rr = max(1.5, float(snapshot.get("min_rr", 1.5)))
    target = trigger + sign * risk * rr
    return {
        "entry_plan": plan,
        "setup_type": setup_type,
        "trigger_price": trigger,
        "stop_price": stop,
        "target_price": target,
        "time_expiry": created_at + timedelta(hours=24),
        "risk_reward": rr,
    }


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
