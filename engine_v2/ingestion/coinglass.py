from __future__ import annotations

import os

from engine_v2.domain.enums import DataQuality

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult


COINGLASS_DATA_TYPES = {
    "actual_liquidation_history",
    "aggregated_actual_liquidation_history",
    "modeled_liquidation_heatmap",
    "orderbook_heatmap",
    "bid_ask_range_history",
    "large_limit_orders",
    "futures_cvd",
    "spot_cvd",
    "futures_footprint",
    "spot_footprint",
    "oi_history",
    "funding_history",
    "basis",
    "etf_flows",
    "news",
}


class CoinGlassProvider(MarketDataProvider):
    name = "coinglass"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(self.name, "coinglass", COINGLASS_DATA_TYPES, True, True, ["Plan capability is explicit; unsupported endpoints are not returned as empty normal data."])

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        if not os.getenv("COINGLASS_API_KEY"):
            self._capabilities.notes.append("authentication_required")
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        if not os.getenv("COINGLASS_ENABLED", "").lower() in {"1", "true", "yes"}:
            return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="provider_disabled")
        if not os.getenv("COINGLASS_API_KEY"):
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="api_key_missing")
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="plan_capability_probe_required_before_endpoint_use")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="endpoint_not_enabled_by_plan")
