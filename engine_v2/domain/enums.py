from __future__ import annotations

from enum import Enum


class DataQuality(str, Enum):
    OK = "ok"
    STALE = "stale"
    PARTIAL = "partial"
    ESTIMATED = "estimated"
    TIMESTAMP_UNKNOWN = "timestamp_unknown"
    UNSUPPORTED = "unsupported"
    INVALID_SEMANTICS = "invalid_semantics"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PROVIDER_ERROR = "provider_error"
    VALIDATION_ERROR = "validation_error"


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    FX = "fx"
    RATE = "rate"
    COMMODITY = "commodity"
    CREDIT = "credit"
    STABLECOIN = "stablecoin"


class ProductType(str, Enum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURE = "future"
    CFD = "cfd"
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NO_TRADE = "no_trade"


class EntryPlan(str, Enum):
    MARKET = "market"
    LIMIT_PULLBACK = "limit_pullback"
    BREAKOUT_CONFIRMATION = "breakout_confirmation"
    RETEST = "retest"
    CONDITIONAL_TRIGGER = "conditional_trigger"
    NO_ENTRY = "no_entry"


class Horizon(str, Enum):
    SCALP = "scalp"
    INTRADAY = "intraday"
    SWING = "swing"
    POSITION = "position"


class EventStatus(str, Enum):
    REPORTED = "reported"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    REVISED = "revised"
    RETRACTED = "retracted"
    STALE = "stale"


class EventCategory(str, Enum):
    MACRO = "macro"
    CENTRAL_BANK = "central_bank"
    EARNINGS = "earnings"
    CORPORATE = "corporate"
    REGULATION = "regulation"
    GEOPOLITICS = "geopolitics"
    EXCHANGE = "exchange"
    MARKET = "market"
    UNKNOWN = "unknown"


def is_usable_quality(value: DataQuality | str) -> bool:
    quality = value.value if isinstance(value, DataQuality) else str(value)
    return quality in {DataQuality.OK.value, DataQuality.PARTIAL.value}


def quality_penalty(value: DataQuality | str) -> int:
    quality = value.value if isinstance(value, DataQuality) else str(value)
    return {
        DataQuality.OK.value: 0,
        DataQuality.PARTIAL.value: -8,
        DataQuality.ESTIMATED.value: -14,
        DataQuality.STALE.value: -18,
        DataQuality.TIMESTAMP_UNKNOWN.value: -22,
        DataQuality.UNSUPPORTED.value: -30,
        DataQuality.INVALID_SEMANTICS.value: -30,
        DataQuality.RATE_LIMITED.value: -24,
        DataQuality.AUTHENTICATION_REQUIRED.value: -24,
        DataQuality.PROVIDER_ERROR.value: -25,
        DataQuality.VALIDATION_ERROR.value: -30,
    }.get(quality, -30)
