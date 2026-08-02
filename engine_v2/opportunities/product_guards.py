from __future__ import annotations

from typing import Any


def evaluate_product_guard(product: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    underlying = str(product.get("underlying_id") or product.get("asset_id") or "")
    warnings: list[str] = []
    reasons: list[str] = []
    allowed = True
    if underlying == "SOXL":
        if context.get("underlying_price_age") is None or context.get("underlying_price_stale"):
            allowed = False
            reasons.append("underlying_stale")
        for key in ("daily_leverage_product_warning", "path_dependency_warning", "volatility_decay_warning", "after_hours_reference_warning", "weekend_warning"):
            if context.get(key):
                warnings.append(key)
    if underlying == "SK_HYNIX_KRX":
        for key in ("krx_session_state", "underlying_close_age", "usd_krw_age", "usdt_usd_age"):
            if context.get(key) in {None, "stale", "unknown"}:
                warnings.append(f"{key}_unavailable")
        if context.get("underlying_stale"):
            allowed = False
            reasons.append("stale_krx_underlying")
    if product.get("product_type") == "perpetual" and underlying == "BTC":
        for key in ("funding", "basis", "oi_concentration", "spot_perp_divergence"):
            if context.get(key) is None:
                warnings.append(f"{key}_missing")
    return {"allowed": allowed, "warnings": warnings, "reasons": reasons, "product_id": product.get("product_id"), "underlying_id": underlying}
