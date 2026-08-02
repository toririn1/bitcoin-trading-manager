from __future__ import annotations

from typing import Any, Iterable


def weighted_oi(rows: Iterable[dict[str, Any]], *, price: float | None = None) -> dict[str, Any]:
    total_notional = 0.0
    weighted_change = 0.0
    venues = []
    for row in rows:
        oi = _number(row.get("open_interest"))
        change = _number(row.get("change_pct"))
        row_price = _number(row.get("price")) or price or 0
        contract_size = _number(row.get("contract_size")) or 1
        if oi is None or row_price <= 0:
            continue
        notional = abs(oi * row_price * contract_size)
        total_notional += notional
        if change is not None:
            weighted_change += notional * change
        venues.append({"venue": row.get("venue"), "notional_usd": notional, "change_pct": change})
    if not venues or total_notional <= 0:
        return {"venue_oi_usd": None, "notional_weighted_oi_change": None, "venue_oi_share": {}, "cross_exchange_oi_dispersion": None, "quality": "partial", "reason": "oi_notional_missing"}
    shares = {str(item["venue"]): item["notional_usd"] / total_notional for item in venues}
    changes = [item["change_pct"] for item in venues if item["change_pct"] is not None]
    return {"venue_oi_usd": total_notional, "notional_weighted_oi_change": weighted_change / total_notional if changes else None, "venue_oi_share": shares, "cross_exchange_oi_dispersion": max(changes) - min(changes) if len(changes) > 1 else 0.0 if changes else None, "quality": "ok" if changes else "partial", "reason": None if changes else "oi_change_missing"}


def funding_basis(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    funding = []
    basis = []
    for row in rows:
        oi = _number(row.get("open_interest_usd")) or 0
        funding_value = _number(row.get("funding_rate"))
        basis_value = _number(row.get("basis"))
        if funding_value is not None:
            funding.append((funding_value, oi))
        if basis_value is not None:
            basis.append((basis_value, oi))
    total_oi = sum(weight for _, weight in funding)
    return {"weighted_funding": sum(value * weight for value, weight in funding) / total_oi if total_oi else None, "funding_dispersion": max((value for value, _ in funding), default=None) - min((value for value, _ in funding), default=None) if funding else None, "annualized_basis": sum(value * (weight or 1) for value, weight in basis) / sum(weight or 1 for _, weight in basis) if basis else None, "quality": "ok" if funding or basis else "partial"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None
