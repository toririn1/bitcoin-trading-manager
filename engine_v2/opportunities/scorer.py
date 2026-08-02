from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import Direction, EntryPlan, Horizon, quality_penalty
from engine_v2.domain.models import OpportunityCandidate

from .costs import estimate_cost_bps, net_edge
from .product_guards import evaluate_product_guard


def score_candidate(product: dict[str, Any], direction: Direction, snapshot: dict[str, Any], *, min_net_edge_bps: float = 8.0) -> OpportunityCandidate:
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
    quality_component = quality_penalty(quality.get("quality", "unknown"))
    product_context = snapshot.get("product_context", {})
    guard = evaluate_product_guard(product, product_context)
    costs = estimate_cost_bps(product, snapshot.get("costs", {}))
    calibrated = _calibrated_edge(snapshot, product.get("product_id"), direction.value)
    gross_edge = calibrated.get("gross_edge_bps")
    edge = net_edge(gross_edge, costs)
    edge_quality = "calibrated" if edge is not None else "uncalibrated"
    reasons: list[str] = ["no_trade_candidate"] if direction == Direction.NO_TRADE else []
    risks = list(guard.get("warnings", [])) + list(guard.get("reasons", []))
    gate_reasons: list[str] = []
    if quality_score < 55:
        gate_reasons.append("data_quality_gate")
    if snapshot.get("mode") == "fixture":
        gate_reasons.append("fixture_synthetic")
    if not guard.get("allowed", True):
        gate_reasons.extend(guard.get("reasons", []))
    if costs.get("estimated_cost_bps") is None and direction != Direction.NO_TRADE:
        gate_reasons.append("cost_missing")
    if edge is None and direction != Direction.NO_TRADE:
        gate_reasons.append("edge_uncalibrated")
    if edge is not None and edge < min_net_edge_bps:
        gate_reasons.append("net_edge_below_threshold")
    valid = direction == Direction.NO_TRADE or not gate_reasons
    setup_quality = "no_trade" if direction == Direction.NO_TRADE else "heuristic"
    reasons.extend(gate_reasons)
    return OpportunityCandidate(
        candidate_id=f"v2-{uuid4().hex[:12]}",
        created_at=datetime.now(timezone.utc),
        product_id=str(product.get("product_id")),
        direction=direction,
        horizon=Horizon.INTRADAY,
        entry_plan=EntryPlan.CONDITIONAL_TRIGGER if valid and direction != Direction.NO_TRADE else EntryPlan.NO_ENTRY,
        invalidation="data or setup invalidation",
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
        confidence=None,
        reason_codes=list(dict.fromkeys(reasons)),
        risk_codes=list(dict.fromkeys(risks)),
        source_snapshot_id=snapshot.get("snapshot_id"),
        valid=valid,
    )


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
