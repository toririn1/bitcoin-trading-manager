from __future__ import annotations

import os

from engine_v2.domain.enums import DataQuality

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult


class GateCFDProvider(MarketDataProvider):
    name = "gate_cfd"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            self.name,
            "gate_cfd",
            {"provider_status"},
            True,
            True,
            ["CFD catalog and market endpoints are not implemented; no CFD symbols are registered."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        if not os.getenv("GATE_CFD_ENABLED", "").lower() in {"1", "true", "yes"}:
            return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="provider_disabled")
        if not os.getenv("GATE_CFD_API_BASE"):
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="discovery_endpoint_not_configured")
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="official_cfd_schema_requires_account_catalog_configuration")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="cfd_catalog_not_discovered")
