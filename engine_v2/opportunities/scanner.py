from __future__ import annotations

from typing import Any, Iterable

from engine_v2.domain.enums import Direction
from engine_v2.domain.models import OpportunityCandidate

from .scorer import score_candidate


def scan_opportunities(products: Iterable[dict[str, Any]], snapshot: dict[str, Any], *, min_net_edge_bps: float = 8.0) -> list[OpportunityCandidate]:
    candidates: list[OpportunityCandidate] = []
    for product in products:
        for direction in (Direction.LONG, Direction.SHORT, Direction.NO_TRADE):
            candidates.append(score_candidate(product, direction, snapshot, min_net_edge_bps=min_net_edge_bps))
    return sorted(candidates, key=lambda item: (item.valid, item.net_edge_bps if item.net_edge_bps is not None else -10_000, item.confidence or 0), reverse=True)
