from __future__ import annotations

from typing import Any


def simulate_fill(plan: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    order_type = str(plan.get("order_type") or "market")
    bid = _number(market.get("bid"))
    ask = _number(market.get("ask"))
    mid = _number(market.get("mid")) or ((bid + ask) / 2 if bid is not None and ask is not None else None)
    if mid is None:
        return {"status": "not_filled", "reason": "quote_missing", "fill_price": None, "filled_quantity": 0}
    if order_type == "limit":
        limit_price = _number(plan.get("limit_price"))
        direction = str(plan.get("direction") or "long")
        triggered = limit_price is not None and ((direction == "long" and ask <= limit_price) or (direction == "short" and bid >= limit_price))
        if not triggered:
            return {"status": "not_triggered", "reason": "limit_not_touched", "fill_price": None, "filled_quantity": 0}
    slippage = _number(market.get("slippage_bps")) or 0.0
    direction_sign = 1 if str(plan.get("direction") or "long") == "long" else -1
    fill = mid * (1 + direction_sign * slippage / 10000)
    available = _number(market.get("available_depth"))
    requested = _number(plan.get("quantity")) or 0.0
    filled = min(requested, available) if available is not None else requested
    return {"status": "filled" if filled > 0 else "not_filled", "reason": None if filled > 0 else "quantity_missing", "fill_price": fill if filled > 0 else None, "filled_quantity": filled, "partial": available is not None and filled < requested}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None
