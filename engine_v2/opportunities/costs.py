from __future__ import annotations

from typing import Any


def estimate_cost_bps(product: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    """Return costs only when the required product/market inputs exist."""
    fee = _first_number(market.get("taker_fee_bps"), product.get("taker_fee_bps"), (product.get("capabilities") or {}).get("taker_fee_bps"))
    fee_source = "observed" if _first_number(market.get("taker_fee_bps")) is not None else "configured" if _first_number(product.get("taker_fee_bps")) is not None else "missing"
    commission = _first_number(market.get("commission_bps"), product.get("commission_bps"), (product.get("capabilities") or {}).get("commission_bps"))
    spread = _first_number(market.get("spread_bps"))
    slippage = _first_number(market.get("estimated_slippage_bps"))
    funding = _first_number(market.get("funding_bps"))
    premium = _first_number(market.get("premium_discount_bps"))
    fx = _first_number(market.get("fx_conversion_bps"))
    impact = _first_number(market.get("market_impact_bps"))
    components = {"maker_taker_fee_bps": fee, "spread_bps": spread, "slippage_bps": slippage, "funding_bps": funding, "commission_bps": commission, "premium_discount_bps": premium, "fx_conversion_bps": fx, "market_impact_bps": impact}
    required_missing = [name for name in ("maker_taker_fee_bps", "spread_bps") if components[name] is None]
    if required_missing:
        return {**components, "estimated_cost_bps": None, "quality": "missing", "missing": required_missing}
    total = sum(value or 0.0 for value in components.values())
    slippage_source = str(market.get("slippage_source") or "observed")
    quality = "observed" if slippage is not None and slippage_source == "observed" else "configured" if fee_source == "configured" else "partial"
    return {**components, "estimated_cost_bps": total, "quality": quality, "fee_source": fee_source, "slippage_source": slippage_source, "missing": [] if slippage is not None else ["slippage"]}


def net_edge(gross_edge_bps: float | None, cost: dict[str, Any]) -> float | None:
    if gross_edge_bps is None or cost.get("estimated_cost_bps") is None:
        return None
    return float(gross_edge_bps) - float(cost["estimated_cost_bps"])


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            number = float(value)
            if number == number and abs(number) != float("inf"):
                return number
        except (TypeError, ValueError):
            continue
    return None
