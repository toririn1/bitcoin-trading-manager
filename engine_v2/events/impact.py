from __future__ import annotations

from typing import Any

from engine_v2.features.event_reaction import event_reaction


def estimate_already_priced(event: dict[str, Any], *, pre_returns: list[float], post_returns: dict[str, list[float]]) -> dict[str, Any]:
    return event_reaction(pre_returns, post_returns, expected_move=event.get("expected_move"), surprise=event.get("surprise"), novelty=event.get("novelty"))
