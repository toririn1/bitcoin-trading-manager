from __future__ import annotations

from collections import defaultdict
from typing import Any


FACTOR_MEMBERS = {
    "us_broad_tech": ("NQ", "QQQ"),
    "us_semiconductor": ("SOXX", "SMH", "SOXL", "NVDA", "MU", "TSM"),
    "kr_semiconductor": ("SK_HYNIX_KRX", "SAMSUNG_KRX", "KOSPI", "KOSDAQ"),
    "rates_fx": ("US_2Y", "US_10Y", "DXY", "USD_KRW"),
    "credit_risk": ("HYG", "LQD"),
    "energy_geopolitics": ("WTI", "GOLD"),
    "crypto_liquidity": ("BTC", "ETH", "STABLECOIN_LIQUIDITY", "BTC_ETF_FLOW"),
    "crypto_leverage": ("BTC", "ETH"),
    "crypto_spot_demand": ("BTC", "ETH", "BTC_ETF_FLOW"),
}


def factor_state(asset_returns: dict[str, float | None], *, weights: dict[str, float] | None = None) -> dict[str, Any]:
    weights = weights or {}
    states = {}
    for factor, members in FACTOR_MEMBERS.items():
        values = [(asset_returns.get(member), weights.get(member, 1.0)) for member in members if asset_returns.get(member) is not None]
        if not values:
            states[factor] = {"value": None, "sample_count": 0, "quality": "insufficient_data", "members_used": []}
            continue
        total = sum(weight for _, weight in values)
        states[factor] = {"value": sum(float(value) * weight for value, weight in values) / total, "sample_count": len(values), "quality": "ok" if len(values) >= 2 else "partial", "members_used": [members[index] for index, item in enumerate(values) if item[0] is not None]}
    return states


def product_factor_exposure(asset_id: str, *, factor_values: dict[str, Any] | None = None) -> dict[str, float]:
    exposure = defaultdict(float)
    if asset_id in {"BTC", "ETH"}:
        exposure.update({"crypto_liquidity": 1.0, "crypto_leverage": 0.8, "crypto_spot_demand": 0.8, "us_broad_tech": 0.2})
    elif asset_id == "SOXL":
        exposure.update({"us_semiconductor": 1.5, "us_broad_tech": 0.8})
    elif asset_id == "SK_HYNIX_KRX":
        exposure.update({"kr_semiconductor": 1.0, "us_semiconductor": 0.3, "rates_fx": 0.2})
    return dict(exposure)


def portfolio_concentration(positions: list[dict[str, Any]], *, max_exposure: float = 2.5) -> dict[str, Any]:
    totals = defaultdict(float)
    for position in positions:
        asset_id = str(position.get("asset_id") or position.get("underlying_id") or "")
        notional = abs(float(position.get("notional") or position.get("notional_usd") or 0))
        for factor, exposure in product_factor_exposure(asset_id).items():
            totals[factor] += notional * abs(exposure)
    gross = sum(abs(float(position.get("notional") or position.get("notional_usd") or 0)) for position in positions)
    normalized = {factor: value / gross if gross else None for factor, value in totals.items()}
    breaches = [factor for factor, value in normalized.items() if value is not None and value > max_exposure]
    return {"factor_exposure": normalized, "breaches": breaches, "quality": "ok" if positions else "partial"}
