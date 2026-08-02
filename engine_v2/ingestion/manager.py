from __future__ import annotations

import time
from typing import Any

from engine_v2.domain.enums import DataQuality
from engine_v2.domain.registry import AssetRegistry
from engine_v2.storage.database import V2Storage

from .base import MarketDataProvider, ProviderResult
from .health import ProviderHealthRegistry


class MarketDataManager:
    def __init__(self, registry: AssetRegistry, storage: V2Storage, providers: list[MarketDataProvider] | None = None) -> None:
        self.registry = registry
        self.storage = storage
        self.providers: dict[str, MarketDataProvider] = {provider.name: provider for provider in providers or []}
        self.health_registry = ProviderHealthRegistry()

    def register(self, provider: MarketDataProvider) -> None:
        self.providers[provider.name] = provider

    @staticmethod
    def _result_quality_ok(result: ProviderResult) -> bool:
        return result.quality in {DataQuality.OK, DataQuality.PARTIAL, DataQuality.DELAYED}

    def _record_result(self, provider: str, result: ProviderResult, latency_ms: float) -> None:
        if self._result_quality_ok(result):
            self.health_registry.success(
                provider,
                latency_ms=latency_ms,
                notes=[result.reason] if result.reason else None,
                quality=result.quality,
            )
        else:
            self.health_registry.error(
                provider,
                result.reason or result.quality.value,
                quality=result.quality,
            )

    async def discover(self, underlying_ids: list[str] | None = None) -> list[ProviderResult]:
        results: list[ProviderResult] = []
        for provider in self.providers.values():
            started = time.perf_counter()
            try:
                result = await provider.discover_products(underlying_ids)
                self.registry.register_discovered_products(result.products)
                self._record_result(provider.name, result, (time.perf_counter() - started) * 1000)
            except Exception as exc:
                self.health_registry.error(provider.name, type(exc).__name__)
                result = ProviderResult(provider.name, quality=DataQuality.PROVIDER_ERROR, reason=str(exc))
            results.append(result)
        return results

    async def backfill(self, product_id: str, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        product = self.registry.product(product_id)
        if product is None:
            return ProviderResult("registry", quality=DataQuality.VALIDATION_ERROR, reason="product_not_discovered")
        provider = self.providers.get(product.provider)
        if provider is None:
            return ProviderResult(product.provider, quality=DataQuality.VALIDATION_ERROR, reason="provider_not_registered")
        started = time.perf_counter()
        try:
            result = await provider.backfill(product, timeframe=timeframe, limit=limit)
            self.storage.append_observations(result.data)
            self._record_result(provider.name, result, (time.perf_counter() - started) * 1000)
            return result
        except Exception as exc:
            self.health_registry.error(provider.name, type(exc).__name__)
            return ProviderResult(provider.name, quality=DataQuality.PROVIDER_ERROR, reason=str(exc))

    def health(self) -> list[dict[str, Any]]:
        return self.health_registry.all()
