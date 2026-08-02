from __future__ import annotations

import os

from engine_v2.domain.enums import DataQuality

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult


class KISProvider(MarketDataProvider):
    name = "kis"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            self.name,
            "kis",
            {"provider_status"},
            True,
            True,
            ["KIS live market/account endpoints are not implemented; fixture parsing is outside live capability claims."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        if not os.getenv("KIS_APP_KEY") or not os.getenv("KIS_APP_SECRET") or not os.getenv("KIS_ACCOUNT"):
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="kis_read_only_credentials_missing")
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="environment_specific_openapi_base_not_configured")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="kis_credentials_or_live_base_missing")
