from __future__ import annotations

from engine_v2.domain.enums import DataQuality

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult


class OfficialEventsProvider(MarketDataProvider):
    name = "official_events"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(self.name, "official_events", {"federal_reserve", "bls", "bea", "treasury", "opendart", "exchange_announcements", "company_ir"}, False, True, ["Event source URLs are configured explicitly; no HTML scraper is used."])

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids=None) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="event_calendar_backfill_is_event_specific")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="event_calendar_requires_configured_source")
