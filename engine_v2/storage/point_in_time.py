from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from engine_v2.domain.models import parse_datetime


def available_at_or_before(record: dict[str, Any], decision_time: datetime) -> bool:
    decision = decision_time.astimezone(timezone.utc) if decision_time.tzinfo else decision_time.replace(tzinfo=timezone.utc)
    available = parse_datetime(record.get("available_at"))
    if available is None:
        return False
    return available <= decision


def filter_available(records: Iterable[dict[str, Any]], decision_time: datetime) -> list[dict[str, Any]]:
    return [record for record in records if available_at_or_before(record, decision_time)]


def latest_before(records: Iterable[dict[str, Any]], decision_time: datetime, *, key: str = "source_event_time") -> dict[str, Any] | None:
    usable = filter_available(records, decision_time)
    if not usable:
        return None
    return max(usable, key=lambda item: parse_datetime(item.get(key)) or datetime.min.replace(tzinfo=timezone.utc))


def point_in_time_join(left: Iterable[dict[str, Any]], right: Iterable[dict[str, Any]], decision_time: datetime, key: str) -> list[dict[str, Any]]:
    left_rows = filter_available(left, decision_time)
    right_rows = filter_available(right, decision_time)
    right_by_key: dict[Any, list[dict[str, Any]]] = {}
    for row in right_rows:
        right_by_key.setdefault(row.get(key), []).append(row)
    joined: list[dict[str, Any]] = []
    for row in left_rows:
        matches = right_by_key.get(row.get(key), [])
        if not matches:
            joined.append({**row, "point_in_time_match": None})
            continue
        latest = max(matches, key=lambda item: parse_datetime(item.get("available_at")) or datetime.min.replace(tzinfo=timezone.utc))
        joined.append({**row, "point_in_time_match": latest})
    return joined
