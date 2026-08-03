from __future__ import annotations

import time
from collections import Counter

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

    def _record_result(self, provider: str, result: ProviderResult, latency_ms: float, *, operation: str) -> None:
        counts = Counter()
        for observation in result.data:
            if observation.data_type.startswith("candle_"):
                counts[observation.data_type.removeprefix("candle_")] += 1
        if self._result_quality_ok(result):
            self.health_registry.success(
                provider,
                latency_ms=latency_ms,
                notes=[result.reason] if result.reason else None,
                quality=result.quality,
                observation_count=len(result.data),
                candle_count_by_timeframe=dict(counts),
                operation=operation,
            )
        else:
            self.health_registry.error(
                provider,
                result.reason or result.quality.value,
                quality=result.quality,
                operation=operation,
            )

    async def discover(self, underlying_ids: list[str] | None = None) -> list[ProviderResult]:
        results: list[ProviderResult] = []
        for provider in self.providers.values():
            started = time.perf_counter()
            try:
                self.health_registry.attempt(provider.name)
                result = await provider.discover_products(underlying_ids)
                self.registry.register_discovered_products(result.products)
                self._record_result(provider.name, result, (time.perf_counter() - started) * 1000, operation="discovery")
            except Exception as exc:
                self.health_registry.error(provider.name, type(exc).__name__, operation="discovery")
                result = ProviderResult(provider.name, quality=DataQuality.PROVIDER_ERROR, reason=f"{type(exc).__name__}:{exc}")
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
            self.health_registry.attempt(provider.name)
            result = await provider.backfill(product, timeframe=timeframe, limit=limit)
            self.storage.append_observations(result.data)
            self._record_result(provider.name, result, (time.perf_counter() - started) * 1000, operation="market_data")
            return result
        except Exception as exc:
            self.health_registry.error(provider.name, type(exc).__name__, operation="market_data")
            return ProviderResult(provider.name, quality=DataQuality.PROVIDER_ERROR, reason=f"{type(exc).__name__}:{exc}")

    async def backfill_history(
        self,
        product_id: str,
        *,
        timeframe: str,
        requested: int,
        minimum_closed: int = 30,
    ) -> ProviderResult:
        existing = self.storage.history_readiness(
            product_id,
            timeframe,
            requested=requested,
            minimum_closed=minimum_closed,
        )
        if existing["analysis_ready"]:
            return ProviderResult(
                self.registry.product(product_id).provider if self.registry.product(product_id) else "registry",
                quality=DataQuality.OK,
                reason="history_cached",
            )
        return await self.backfill(product_id, timeframe=timeframe, limit=requested)

    def health(self) -> list[dict[str, Any]]:
        return self.health_registry.all()
