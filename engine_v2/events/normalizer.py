from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from engine_v2.domain.enums import EventCategory, EventStatus
from engine_v2.domain.models import Event, parse_datetime


def normalize_event(payload: dict[str, Any], *, discovered_via: str = "manual_intake") -> Event:
    now = datetime.now(timezone.utc)
    headline = str(payload.get("headline") or "").strip()
    if not headline:
        raise ValueError("headline_required")
    published = parse_datetime(payload.get("published_at"))
    event_time = parse_datetime(payload.get("event_time")) or published
    source_url = str(payload.get("url") or payload.get("source_url") or "").strip() or None
    raw_hash = hashlib.sha256(str(sorted(payload.items())).encode()).hexdigest()
    category = _category(payload.get("category"))
    return Event(
        event_id=str(payload.get("event_id") or raw_hash[:24]),
        headline=headline,
        original_source=payload.get("original_source"),
        discovered_via=str(payload.get("discovered_via") or discovered_via),
        source_url=source_url,
        published_at=published,
        event_time=event_time,
        first_seen_at=parse_datetime(payload.get("first_seen_at")) or now,
        category=category,
        subcategory=payload.get("subcategory"),
        affected_assets=[str(item) for item in payload.get("affected_assets", [])],
        affected_factors=[str(item) for item in payload.get("affected_factors", [])],
        expected=_number(payload.get("expected")),
        actual=_number(payload.get("actual")),
        previous=_number(payload.get("previous")),
        previous_revised=_number(payload.get("previous_revised")),
        novelty=_number(payload.get("novelty")),
        source_reliability=_number(payload.get("source_reliability")),
        status=EventStatus.REPORTED,
        summary=payload.get("notes") or payload.get("summary"),
        raw_payload_hash=raw_hash,
    )


def _category(value: Any) -> EventCategory:
    try:
        return EventCategory(str(value))
    except ValueError:
        return EventCategory.UNKNOWN


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
