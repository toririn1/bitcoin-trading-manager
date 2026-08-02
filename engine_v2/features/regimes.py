from __future__ import annotations

from typing import Any


def classify_regime(features: dict[str, Any], previous: dict[str, str] | None = None) -> dict[str, Any]:
    previous = previous or {}
    trend_value = features.get("return_24")
    volatility = features.get("volatility_20")
    depth = features.get("liquidity_vacuum_score")
    funding = features.get("weighted_funding")
    corr_state = features.get("cross_asset_state")
    result = {
        "trend_regime": _hysteresis(previous.get("trend_regime"), "bullish" if trend_value is not None and trend_value > 0.01 else "bearish" if trend_value is not None and trend_value < -0.01 else "range"),
        "volatility_regime": _hysteresis(previous.get("volatility_regime"), "high" if volatility is not None and volatility > 0.02 else "low" if volatility is not None and volatility < 0.008 else "normal"),
        "liquidity_regime": "thin" if depth is not None and depth > 0.7 else "normal" if depth is not None else "unknown",
        "leverage_regime": "long_crowded" if funding is not None and funding > 0.03 else "short_crowded" if funding is not None and funding < -0.03 else "normal" if funding is not None else "unknown",
        "correlation_regime": corr_state or "unknown",
        "event_risk_regime": features.get("event_risk_regime", "normal"),
        "session_regime": features.get("session_regime", "unknown"),
        "market_depth_regime": "thin" if depth is not None and depth > 0.7 else "normal" if depth is not None else "unknown",
    }
    result["label"] = ", ".join(f"{key.removesuffix('_regime')}={value}" for key, value in result.items() if key.endswith("_regime"))
    return result


def _hysteresis(previous: str | None, candidate: str) -> str:
    if previous and previous != candidate and {previous, candidate} <= {"normal", "range"}:
        return previous
    return candidate
