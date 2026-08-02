from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from engine_v2.domain.enums import DataQuality


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_code: str | None = None
    last_error: str | None = None
    last_latency_ms: float | None = None
    latency_ms: float | None = None
    observation_count: int = 0
    consecutive_failures: int = 0
    rate_limit_remaining: int | None = None
    rate_limit_reset_at: datetime | None = None
    rate_limit_state: str = "unknown"
    circuit_state: str = "closed"
    quality: DataQuality = DataQuality.TIMESTAMP_UNKNOWN
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "last_attempt_at": self.last_attempt_at.isoformat().replace("+00:00", "Z") if self.last_attempt_at else None,
            "last_success_at": self.last_success_at.isoformat().replace("+00:00", "Z") if self.last_success_at else None,
            "last_error_at": self.last_error_at.isoformat().replace("+00:00", "Z") if self.last_error_at else None,
            "last_error_code": self.last_error_code,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
            "latency_ms": self.latency_ms,
            "observation_count": self.observation_count,
            "consecutive_failures": self.consecutive_failures,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_reset_at": self.rate_limit_reset_at.isoformat().replace("+00:00", "Z") if self.rate_limit_reset_at else None,
            "rate_limit_state": self.rate_limit_state,
            "circuit_state": self.circuit_state,
            "quality": self.quality.value,
            "notes": self.notes,
        }


class ProviderHealthRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ProviderHealth] = {}

    def get(self, provider: str) -> ProviderHealth:
        return self._items.setdefault(provider, ProviderHealth(provider))

    def attempt(self, provider: str) -> None:
        self.get(provider).last_attempt_at = datetime.now(timezone.utc)

    def success(self, provider: str, *, latency_ms: float | None = None, notes: list[str] | None = None, quality: DataQuality = DataQuality.OK, observation_count: int = 0) -> None:
        item = self.get(provider)
        item.last_success_at = datetime.now(timezone.utc)
        item.last_latency_ms = latency_ms
        item.latency_ms = latency_ms
        item.observation_count = observation_count
        item.last_error = None
        item.consecutive_failures = 0
        item.circuit_state = "closed"
        item.quality = quality
        if notes:
            item.notes = notes[-10:]

    def error(self, provider: str, code: str, quality: DataQuality = DataQuality.PROVIDER_ERROR) -> None:
        item = self.get(provider)
        item.last_error_at = datetime.now(timezone.utc)
        item.last_error_code = code
        item.last_error = code
        item.consecutive_failures += 1
        item.quality = quality
        if item.consecutive_failures >= 3:
            item.circuit_state = "open"

    def update_rate_limit(self, provider: str, remaining: int | None, reset_at: datetime | None = None) -> None:
        item = self.get(provider)
        item.rate_limit_remaining = remaining
        item.rate_limit_reset_at = reset_at
        item.rate_limit_state = "available" if remaining is None or remaining > 0 else "exhausted"

    def all(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._items.values()]
