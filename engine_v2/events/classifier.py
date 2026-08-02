from __future__ import annotations

from engine_v2.domain.enums import EventCategory
from engine_v2.domain.models import Event


KEYWORDS = {
    EventCategory.MACRO: ("cpi", "inflation", "payroll", "gdp", "pce"),
    EventCategory.CENTRAL_BANK: ("fed", "fomc", "ecb", "boj", "rate decision"),
    EventCategory.EARNINGS: ("earnings", "guidance", "revenue"),
    EventCategory.GEOPOLITICS: ("war", "tariff", "sanction", "ceasefire", "missile"),
    EventCategory.REGULATION: ("sec", "regulation", "ban", "approval"),
    EventCategory.EXCHANGE: ("exchange", "listing", "delist", "halt"),
}


def classify(event: Event) -> Event:
    if event.category != EventCategory.UNKNOWN:
        return event
    text = event.headline.lower()
    for category, keywords in KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            event.category = category
            break
    return event
