from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from engine_v2.config.source_priority import ttl_for
from engine_v2.domain.enums import DataQuality, quality_penalty
from engine_v2.domain.models import parse_datetime


def assess_observation(row: dict[str, Any], *, now: datetime | None = None, ttl_seconds: int | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    source_time = parse_datetime(row.get("source_event_time") or row.get("event_time"))
    if source_time is None:
        return {"quality": DataQuality.TIMESTAMP_UNKNOWN.value, "is_fresh": False, "age_seconds": None, "penalty": quality_penalty(DataQuality.TIMESTAMP_UNKNOWN), "reason": "source_timestamp_missing"}
    age = max(0.0, (now - source_time).total_seconds())
    ttl = ttl_seconds if ttl_seconds is not None else ttl_for(str(row.get("data_type") or "ticker"))
    stale = age > ttl
    quality = DataQuality.STALE if stale else DataQuality.OK
    return {"quality": quality.value, "is_fresh": not stale, "age_seconds": age, "penalty": quality_penalty(quality), "reason": "ttl_expired" if stale else None}


def aggregate_quality(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(rows)
    if not values:
        return {"quality": DataQuality.PARTIAL.value, "score": 0, "missing": ["no_observations"]}
    missing = [str(row.get("reason") or row.get("data_type") or "unknown") for row in values if row.get("quality") not in {DataQuality.OK.value, DataQuality.PARTIAL.value, "ok", "partial"}]
    score = max(0, min(100, round(sum(100 + quality_penalty(row.get("quality", "unknown")) for row in values) / len(values))))
    if missing:
        # A large OK sample can otherwise round a partial dataset back to 100.
        score = min(score, max(0, 100 - 5 * len(set(missing))))
    return {"quality": "ok" if not missing else "partial", "score": score, "missing": missing, "observation_count": len(values)}


def candidate_gate(quality: dict[str, Any], *, minimum_score: int = 55) -> tuple[bool, list[str]]:
    reasons = []
    if int(quality.get("score") or 0) < minimum_score:
        reasons.append("data_quality_gate")
    if quality.get("quality") in {"invalid_semantics", "validation_error", "unsupported", "plan_not_available", "authentication_required"}:
        reasons.append("invalid_data_semantics")
    return not reasons, reasons
