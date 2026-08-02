from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from engine_v2.domain.enums import DataQuality
from engine_v2.domain.models import Observation, ProductSpec


@dataclass(slots=True)
class ProviderCapabilities:
    provider_name: str
    venue: str
    capabilities: set[str] = field(default_factory=set)
    requires_auth: bool = False
    read_only: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "venue": self.venue,
            "capabilities": sorted(self.capabilities),
            "requires_auth": self.requires_auth,
            "read_only": self.read_only,
            "notes": self.notes,
        }


@dataclass(slots=True)
class ProviderResult:
    provider: str
    data: list[Observation] = field(default_factory=list)
    products: list[ProductSpec] = field(default_factory=list)
    quality: DataQuality = DataQuality.OK
    reason: str | None = None
    request_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "data": [item.to_dict() for item in self.data],
            "products": [item.to_dict() for item in self.products],
            "quality": self.quality.value,
            "reason": self.reason,
            "request_count": self.request_count,
        }


class MarketDataProvider(ABC):
    name: str = "unknown"

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError

    @abstractmethod
    async def discover_capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError

    @abstractmethod
    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    async def backfill(self, product: ProductSpec, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        raise NotImplementedError

    async def subscribe(self, *args: Any, **kwargs: Any) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="stream_not_configured")

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, **self.capabilities.to_dict()}


def observation_time(payload: dict[str, Any], *keys: str) -> datetime | None:
    from engine_v2.domain.models import parse_datetime

    for key in keys:
        if key not in payload or payload[key] in (None, ""):
            continue
        value = payload[key]
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            return datetime.fromtimestamp(number, tz=__import__("datetime").timezone.utc)
        parsed = parse_datetime(str(value))
        if parsed:
            return parsed
    return None
