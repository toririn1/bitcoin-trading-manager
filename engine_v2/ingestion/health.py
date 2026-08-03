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
    last_discovery_success_at: datetime | None = None
    last_data_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_code: str | None = None
    last_error: str | None = None
    last_latency_ms: float | None = None
    latency_ms: float | None = None
    observation_count: int = 0
    candle_count_by_timeframe: dict[str, int] = field(default_factory=dict)
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
            "last_attempt_at": _iso(self.last_attempt_at),
            "last_success_at": _iso(self.last_success_at),
            "last_discovery_success_at": _iso(self.last_discovery_success_at),
            "last_data_success_at": _iso(self.last_data_success_at),
            "last_error_at": _iso(self.last_error_at),
            "last_error_code": self.last_error_code,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
            "latency_ms": self.latency_ms,
            "observation_count": self.observation_count,
            "candle_count_by_timeframe": dict(sorted(self.candle_count_by_timeframe.items())),
            "consecutive_failures": self.consecutive_failures,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_reset_at": _iso(self.rate_limit_reset_at),
            "rate_limit_state": self.rate_limit_state,
            "circuit_state": self.circuit_state,
            "quality": self.quality.value,
            "notes": self.notes,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


class ProviderHealthRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ProviderHealth] = {}

    def get(self, provider: str) -> ProviderHealth:
        return self._items.setdefault(provider, ProviderHealth(provider))

    def attempt(self, provider: str) -> None:
        self.get(provider).last_attempt_at = datetime.now(timezone.utc)

    def success(
        self,
        provider: str,
        *,
        latency_ms: float | None = None,
        notes: list[str] | None = None,
        quality: DataQuality = DataQuality.OK,
        observation_count: int = 0,
        candle_count_by_timeframe: dict[str, int] | None = None,
        operation: str = "data",
    ) -> None:
        item = self.get(provider)
        now = datetime.now(timezone.utc)
        item.last_success_at = now
        if operation == "discovery":
            item.last_discovery_success_at = now
        else:
            item.last_data_success_at = now
        item.last_latency_ms = latency_ms
        item.latency_ms = latency_ms
        item.observation_count += max(0, int(observation_count))
        for timeframe, count in (candle_count_by_timeframe or {}).items():
            item.candle_count_by_timeframe[timeframe] = item.candle_count_by_timeframe.get(timeframe, 0) + max(0, int(count))
        item.last_error = None
        item.last_error_code = None
        item.consecutive_failures = 0
        item.circuit_state = "closed"
        item.quality = quality
        if notes:
            item.notes = list(dict.fromkeys([*item.notes, *notes]))[-10:]

    def error(
        self,
        provider: str,
        code: str,
        quality: DataQuality = DataQuality.PROVIDER_ERROR,
        *,
        operation: str = "data",
    ) -> None:
        item = self.get(provider)
        item.last_error_at = datetime.now(timezone.utc)
        item.last_error_code = code
        item.last_error = code
        item.consecutive_failures += 1
        item.quality = quality
        if operation == "discovery" and "discovery_failed" not in item.notes:
            item.notes = [*item.notes, "discovery_failed"][-10:]
        if item.consecutive_failures >= 3:
            item.circuit_state = "open"

    def update_rate_limit(self, provider: str, remaining: int | None, reset_at: datetime | None = None) -> None:
        item = self.get(provider)
        item.rate_limit_remaining = remaining
        item.rate_limit_reset_at = reset_at
        item.rate_limit_state = "available" if remaining is None or remaining > 0 else "exhausted"

    def all(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._items.values()]
