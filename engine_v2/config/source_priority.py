from __future__ import annotations

SOURCE_PRIORITY = {
    "official_exchange": 100,
    "official_government": 100,
    "official_company": 95,
    "primary_wire": 80,
    "licensed_data_vendor": 70,
    "aggregator": 50,
    "official_account": 45,
    "industry_reporter": 35,
    "community": 15,
    "unknown": 0,
}

DATA_TYPE_TTLS_SECONDS = {
    "trade": 5,
    "orderbook": 5,
    "liquidation_pulse": 15,
    "ticker": 30,
    "funding": 180,
    "open_interest": 180,
    "candle_5m": 90,
    "candle_15m": 180,
    "candle_1h": 600,
    "candle_4h": 1800,
    "candle_1d": 7200,
    "macro_release": 86400,
    "daily_fundamental": 43200,
}


def ttl_for(data_type: str) -> int:
    return DATA_TYPE_TTLS_SECONDS.get(data_type, 900)


def source_rank(source_class: str | None) -> int:
    return SOURCE_PRIORITY.get(source_class or "unknown", 0)
