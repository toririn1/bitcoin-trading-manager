from __future__ import annotations

from typing import Any


def estimate_cost_bps(product: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    fee = _number(market.get("taker_fee_bps")) or _number(product.get("taker_fee_bps")) or 6.0
    spread = _number(market.get("spread_bps")) or 0.0
    slippage = _number(market.get("estimated_slippage_bps")) or max(1.0, spread * 0.5)
    funding = abs(_number(market.get("funding_bps")) or 0.0)
    commission = _number(market.get("commission_bps")) or 0.0
    premium = abs(_number(market.get("premium_discount_bps")) or 0.0)
    fx = abs(_number(market.get("fx_conversion_bps")) or 0.0)
    impact = _number(market.get("market_impact_bps")) or 0.0
    total = fee + spread + slippage + funding + commission + premium + fx + impact
    return {"maker_taker_fee_bps": fee, "spread_bps": spread, "slippage_bps": slippage, "funding_bps": funding, "commission_bps": commission, "premium_discount_bps": premium, "fx_conversion_bps": fx, "market_impact_bps": impact, "estimated_cost_bps": total, "quality": "ok"}


def net_edge(gross_edge_bps: float | None, cost: dict[str, Any]) -> float | None:
    if gross_edge_bps is None:
        return None
    return float(gross_edge_bps) - float(cost.get("estimated_cost_bps") or 0)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None
