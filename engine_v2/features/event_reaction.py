from __future__ import annotations

from typing import Any


def event_reaction(pre_returns: list[float], post_returns: dict[str, list[float]], *, expected_move: float | None = None, surprise: float | None = None, novelty: float | None = None) -> dict[str, Any]:
    pre_move = sum(pre_returns) if pre_returns else None
    reaction = {horizon: sum(values) if values else None for horizon, values in post_returns.items()}
    first = next((value for value in reaction.values() if value is not None), None)
    reversal = first is not None and any(value is not None and value * first < 0 for value in reaction.values())
    already_priced = _clip(0.5 + (abs(pre_move or 0) / abs(expected_move) if expected_move else 0) * 0.25 + (0.2 if reversal else 0) - (novelty or 0) * 0.15)
    return {"pre_event_move": pre_move, "reaction": reaction, "reaction_reversal": reversal, "already_priced_probability": already_priced, "surprise": surprise, "novelty": novelty, "quality": "ok" if first is not None else "partial"}


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))
