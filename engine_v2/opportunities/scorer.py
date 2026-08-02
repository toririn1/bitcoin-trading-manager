from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import Direction, EntryPlan, Horizon
from engine_v2.domain.models import OpportunityCandidate
from engine_v2.domain.enums import quality_penalty

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
    quality_score = float(quality.get("score") or 0)
    quality_component = max(-30.0, min(0.0, (quality_score - 100) * 0.3))
    portfolio_fit = float(features.get("portfolio_fit") or 0)
    gross = trend + momentum + orderflow + derivatives + cross_asset + event + liquidity + product_risk + quality_component + portfolio_fit
    cost = estimate_cost_bps(product, snapshot.get("costs", {}))
    edge = net_edge(gross * 5, cost)
    guard = evaluate_product_guard(product, snapshot.get("product_context", {}))
    reasons = ["no_trade_candidate"] if direction == Direction.NO_TRADE else []
    risks = list(guard.get("warnings", [])) + list(guard.get("reasons", []))
    gate_reasons = []
    if quality_score < 55:
        gate_reasons.append("data_quality_gate")
    if not guard.get("allowed", True):
        gate_reasons.extend(guard.get("reasons", []))
    if edge is None or edge < min_net_edge_bps:
        gate_reasons.append("net_edge_below_threshold")
    if direction == Direction.NO_TRADE:
        valid = True
        entry_plan = EntryPlan.NO_ENTRY
    else:
        valid = not gate_reasons
        entry_plan = EntryPlan.CONDITIONAL_TRIGGER if valid else EntryPlan.NO_ENTRY
    reasons.extend(gate_reasons)
    confidence = max(0.0, min(1.0, (gross + 30) / 160)) if direction != Direction.NO_TRADE else max(0.0, min(1.0, (100 - quality_score) / 100))
    return OpportunityCandidate(
        candidate_id=f"v2-{uuid4().hex[:12]}", created_at=datetime.now(timezone.utc), product_id=str(product.get("product_id")), direction=direction, horizon=Horizon.INTRADAY, entry_plan=entry_plan, invalidation="data or setup invalidation" if valid else "candidate gate failed", setup_quality="actionable" if valid and direction != Direction.NO_TRADE else "no_trade", technical_score=trend, momentum_score=momentum, orderflow_score=orderflow, derivatives_score=derivatives, cross_asset_score=cross_asset, event_score=event, data_quality_score=quality_score, liquidity_score=liquidity, product_risk_score=product_risk, portfolio_fit_score=portfolio_fit, gross_edge_bps=gross * 5, estimated_cost_bps=cost["estimated_cost_bps"], net_edge_bps=edge, confidence=confidence, reason_codes=list(dict.fromkeys(reasons)), risk_codes=list(dict.fromkeys(risks)), source_snapshot_id=snapshot.get("snapshot_id"), valid=valid,
    )


def _score(value: Any, maximum: float, sign: int) -> float:
    if value is None:
        return 0.0
    try:
        return max(-maximum, min(maximum, float(value) * sign))
    except (TypeError, ValueError):
        return 0.0
