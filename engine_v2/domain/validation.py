from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .enums import DataQuality
from .models import Observation, ensure_utc


def validate_source_time(source_event_time: datetime | None, collected_at: datetime) -> tuple[DataQuality, str | None]:
    if source_event_time is None:
        return DataQuality.TIMESTAMP_UNKNOWN, "source_event_time_missing"
    event = ensure_utc(source_event_time)
    collected = ensure_utc(collected_at)
    if event is None or collected is None:
        return DataQuality.TIMESTAMP_UNKNOWN, "timestamp_unparseable"
    if event > collected.astimezone(timezone.utc):
        return DataQuality.VALIDATION_ERROR, "source_event_time_in_future"
    return DataQuality.OK, None


def validate_observation(observation: Observation) -> list[str]:
    errors: list[str] = []
    quality, reason = validate_source_time(observation.source_event_time, observation.collected_at)
    if observation.quality == DataQuality.OK and quality != DataQuality.OK:
        errors.append(reason or quality.value)
    if observation.available_at and observation.available_at < observation.first_seen_at:
        errors.append("available_at_before_first_seen_at")
    if not observation.data_type:
        errors.append("data_type_missing")
    if not isinstance(observation.payload, dict):
        errors.append("payload_not_object")
    return errors


def require_float(value: Any, *, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not_numeric:{value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("non_finite_number")
    return number
