from __future__ import annotations

from engine_v2.domain.enums import DataQuality

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult


class ManualNewsProvider(MarketDataProvider):
    name = "manual_news"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(self.name, "manual_intake", {"reported_news", "source_url", "clipboard_intake"}, False, True, ["Manual intake is reported until an original/official source confirms it."])

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids=None) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="manual_event_source_has_no_product_catalog")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="manual_event_source_has_no_market_backfill")
