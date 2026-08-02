from __future__ import annotations

from typing import Any, Iterable

from engine_v2.domain.enums import Direction
from engine_v2.domain.models import OpportunityCandidate

from .scorer import score_candidate


def scan_opportunities(products: Iterable[dict[str, Any]], snapshot: dict[str, Any], *, min_net_edge_bps: float = 8.0) -> list[OpportunityCandidate]:
    candidates: list[OpportunityCandidate] = []
    for product in products:
        if product.get("role") != "tradable" or not product.get("is_tradable"):
            continue
        directions = [Direction.LONG]
        if product.get("short_supported") and product.get("product_type") != "spot":
            directions.append(Direction.SHORT)
        directions.append(Direction.NO_TRADE)
        for direction in directions:
            candidates.append(score_candidate(product, direction, snapshot, min_net_edge_bps=min_net_edge_bps))
    return rank_candidates(candidates)


def rank_candidates(
    candidates: Iterable[OpportunityCandidate | dict[str, Any]],
) -> list[OpportunityCandidate | dict[str, Any]]:
    """Apply score-descending ranking with stable product-order tie-breaks."""
    ordered = sorted(candidates, key=candidate_tiebreak_key)
    return sorted(ordered, key=candidate_rank_key, reverse=True)


def candidate_rank_key(
    candidate: OpportunityCandidate | dict[str, Any],
) -> tuple[int, int, float, float, float]:
    """Rank execution eligibility, shadow eligibility, edge, confidence, then setup score."""
    valid_for_user = _flag(_candidate_value(candidate, "valid_for_user_execution"))
    valid_for_shadow = _flag(_candidate_value(candidate, "valid_for_shadow"))
    net_edge = _number(_candidate_value(candidate, "net_edge_bps"), default=-10_000.0)
    confidence = _number(_candidate_value(candidate, "confidence"), default=-10_000.0)
    heuristic = _number(_candidate_value(candidate, "heuristic_setup_score"), default=-10_000.0)
    return (valid_for_user, valid_for_shadow, net_edge, confidence, heuristic)


def candidate_tiebreak_key(
    candidate: OpportunityCandidate | dict[str, Any],
) -> tuple[str, str, str]:
    """Use stable public fields only; candidate_id is intentionally excluded."""
    return (
        _text(_candidate_value(candidate, "product_id")),
        _text(_candidate_value(candidate, "direction")),
        _text(_candidate_value(candidate, "setup_type")),
    )


def _candidate_value(candidate: OpportunityCandidate | dict[str, Any], name: str) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(name)
    return getattr(candidate, name, None)


def _text(value: Any) -> str:
    value = getattr(value, "value", value)
    return str(value or "")


def _flag(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    return int(str(value).lower() in {"1", "true", "yes"})


def _number(value: Any, *, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default
