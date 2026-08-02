from __future__ import annotations

import os
from datetime import datetime, timezone

from engine_v2.domain.enums import DataQuality

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult


class GateStockProvider(MarketDataProvider):
    """Read-only catalog boundary for Gate stock products.

    Gate's stock/TradFi availability is account and region dependent. No
    symbol is guessed here: a configured discovery endpoint must return the
    actual catalog before a ProductSpec is registered.
    """

    name = "gate_stock"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(self.name, "gate_stock", {"product_discovery", "market_status", "ticker", "candles", "orderbook", "trades", "fee_info"}, True, True, ["Actual symbols are accepted only from configured discovery response."])

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        if not os.getenv("GATE_STOCK_ENABLED", "").lower() in {"1", "true", "yes"}:
            return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="provider_disabled")
        if not os.getenv("GATE_STOCK_API_BASE"):
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="discovery_endpoint_not_configured")
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="official_stock_schema_requires_account_catalog_configuration")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="stock_catalog_not_discovered")
