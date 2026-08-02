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


def factor_state(asset_returns: dict[str, Any], *, weights: dict[str, float] | None = None) -> dict[str, Any]:
    weights = weights or {}
    states = {}
    for factor, members in FACTOR_MEMBERS.items():
        values = []
        for member in members:
            raw = asset_returns.get(member)
            if isinstance(raw, dict):
                value = raw.get("return")
                age = raw.get("source_age")
                quality = raw.get("quality", "unknown")
            else:
                value = raw
                age = None
                quality = "ok" if value is not None else "insufficient_data"
            try:
                value = float(value) if value is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None:
                values.append((member, value, weights.get(member, 1.0), age, quality))
        if not values:
            states[factor] = {
                "value": None,
                "return": None,
                "zscore": None,
                "stability": None,
                "sample_count": 0,
                "quality": "insufficient_data",
                "members_used": [],
                "member_weights": {},
                "source_age": None,
            }
            continue
        total_weight = sum(weight for _, _, weight, _, _ in values)
        factor_value = sum(value * weight for _, value, weight, _, _ in values) / total_weight
        sample = [value for _, value, _, _, _ in values]
        mean = sum(sample) / len(sample)
        variance = sum((value - mean) ** 2 for value in sample) / (len(sample) - 1) if len(sample) > 1 else 0
        std = variance ** 0.5
        states[factor] = {
            "value": factor_value,
            "return": factor_value,
            "zscore": (factor_value - mean) / std if std else 0.0,
            "stability": None,
            "sample_count": len(values),
            "quality": "ok" if len(values) >= 2 and all(item[4] in {"ok", "partial", "delayed"} for item in values) else "partial",
            "members_used": [member for member, _, _, _, _ in values],
            "member_weights": {member: weight for member, _, weight, _, _ in values},
            "source_age": max((age for _, _, _, age, _ in values if age is not None), default=None),
        }
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
