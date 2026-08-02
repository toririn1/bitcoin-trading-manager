from __future__ import annotations

from typing import Any, Iterable


def summarize_outcomes(outcomes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(outcomes)
    returns = [float(row["net_return"]) for row in rows if row.get("net_return") is not None]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    return {"count": len(rows), "trigger_rate": sum(1 for row in rows if row.get("triggered")) / len(rows) if rows else None, "win_rate": len(wins) / len(returns) if returns else None, "net_expectancy": sum(returns) / len(returns) if returns else None, "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else None, "average_win": sum(wins) / len(wins) if wins else None, "average_loss": sum(losses) / len(losses) if losses else None, "cost_drag": sum(float(row.get("fees") or 0) for row in rows)}
