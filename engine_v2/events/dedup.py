from __future__ import annotations

import hashlib
import re
from typing import Iterable

from engine_v2.domain.models import Event


def normalized_headline(headline: str) -> str:
    text = re.sub(r"[^\w\s]", " ", headline.lower())
    return " ".join(text.split())


def event_key(event: Event) -> str:
    raw = "|".join([normalized_headline(event.headline), str(event.event_time or event.published_at or ""), str(event.source_url or "")])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def deduplicate(events: Iterable[Event]) -> list[Event]:
    clusters: dict[str, Event] = {}
    for event in events:
        key = event_key(event)
        existing = clusters.get(key)
        if existing is None or (event.source_reliability or 0) > (existing.source_reliability or 0):
            clusters[key] = event
    return list(clusters.values())
