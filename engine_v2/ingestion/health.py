from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from engine_v2.domain.enums import DataQuality


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_code: str | None = None
    last_latency_ms: float | None = None
    consecutive_failures: int = 0
    rate_limit_remaining: int | None = None
    rate_limit_reset_at: datetime | None = None
    circuit_state: str = "closed"
    quality: DataQuality = DataQuality.TIMESTAMP_UNKNOWN
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "last_success_at": self.last_success_at.isoformat().replace("+00:00", "Z") if self.last_success_at else None,
            "last_error_at": self.last_error_at.isoformat().replace("+00:00", "Z") if self.last_error_at else None,
            "last_error_code": self.last_error_code,
            "last_latency_ms": self.last_latency_ms,
            "consecutive_failures": self.consecutive_failures,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_reset_at": self.rate_limit_reset_at.isoformat().replace("+00:00", "Z") if self.rate_limit_reset_at else None,
            "circuit_state": self.circuit_state,
            "quality": self.quality.value,
            "notes": self.notes,
        }


class ProviderHealthRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ProviderHealth] = {}

    def get(self, provider: str) -> ProviderHealth:
        return self._items.setdefault(provider, ProviderHealth(provider))

    def success(self, provider: str, *, latency_ms: float | None = None, notes: list[str] | None = None) -> None:
        item = self.get(provider)
        item.last_success_at = datetime.now(timezone.utc)
        item.last_latency_ms = latency_ms
        item.consecutive_failures = 0
        item.circuit_state = "closed"
        item.quality = DataQuality.OK
        if notes:
            item.notes = notes[-10:]

    def error(self, provider: str, code: str, quality: DataQuality = DataQuality.PROVIDER_ERROR) -> None:
        item = self.get(provider)
        item.last_error_at = datetime.now(timezone.utc)
        item.last_error_code = code
        item.consecutive_failures += 1
        item.quality = quality
        if item.consecutive_failures >= 3:
            item.circuit_state = "open"

    def update_rate_limit(self, provider: str, remaining: int | None, reset_at: datetime | None = None) -> None:
        item = self.get(provider)
        item.rate_limit_remaining = remaining
        item.rate_limit_reset_at = reset_at

    def all(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._items.values()]
