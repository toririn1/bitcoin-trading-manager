from __future__ import annotations

from typing import Any

from engine_v2.features.cross_asset import dynamic_relationship, insufficient_relationship


REFERENCE_UNDERLYINGS = {
    "BTC": ["QQQ", "SOXX", "SOXL", "VIX", "USD_KRW", "SK_HYNIX_KRX"],
    "SOXL": ["SOXX", "NVDA", "BTC"],
    "SK_HYNIX_KRX": ["SOXX", "USD_KRW", "BTC"],
    "QQQ": ["BTC", "SOXX", "VIX"],
    "SOXX": ["SOXL", "NVDA", "QQQ", "SK_HYNIX_KRX"],
    "NVDA": ["SOXL", "SOXX"],
    "000660": ["SOXX", "USD_KRW", "BTC"],
}


def build_relationships(products: dict[str, dict[str, Any]], series_by_product: dict[str, list[dict[str, Any]]], *, minimum_samples: int, minimum_overlap_ratio: float) -> dict[str, dict[str, dict[str, Any]]]:
    by_underlying: dict[str, str] = {}
    for product_id, product in products.items():
        underlying = str(product.get("underlying_id") or "")
        current = by_underlying.get(underlying)
        if current is None:
            by_underlying[underlying] = product_id
            continue
        current_product = products.get(current, {})
        # Reference feeds are preferred for explanatory relationships; execution
        # candidates remain restricted to role=tradable elsewhere.
        if product.get("role") == "reference" and current_product.get("role") != "reference":
            by_underlying[underlying] = product_id
        elif product.get("provider") == "yfinance_delayed":
            by_underlying[underlying] = product_id
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for product_id, product in products.items():
        underlying = str(product.get("underlying_id") or "")
        references = REFERENCE_UNDERLYINGS.get(underlying, [])
        source = series_by_product.get(product_id, [])
        states = {}
        for reference in references:
            reference_id = by_underlying.get(reference)
            target = series_by_product.get(reference_id, []) if reference_id else []
            states[reference] = (
                dynamic_relationship(
                    source,
                    target,
                    minimum_samples=minimum_samples,
                    minimum_overlap_ratio=minimum_overlap_ratio,
                    timeframe="15m",
                    session_filter="regular",
                )
                if source or target else insufficient_relationship(0, "source_and_target_missing")
            )
        output[product_id] = states
    return output
