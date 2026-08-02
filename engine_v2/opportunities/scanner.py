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
    return sorted(
        candidates,
        key=lambda item: (
            item.valid_for_user_execution,
            item.valid_for_shadow,
            item.net_edge_bps if item.net_edge_bps is not None else -10_000,
            item.confidence or 0,
        ),
        reverse=True,
    )
