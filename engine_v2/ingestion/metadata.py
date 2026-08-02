from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from engine_v2.domain.enums import ProductType
from engine_v2.domain.models import parse_datetime


_DATE_PATTERNS = (
    re.compile(r"(?<![0-9])([0-9]{8})(?![0-9])"),
    re.compile(r"[-_]([0-9]{2}[A-Z]{3}[0-9]{2})(?:$|[-_])", re.IGNORECASE),
)


def payload_hash(value: Any) -> str:
    raw = json.dumps(value or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_expiry(value: Any) -> datetime | None:
    if value in (None, "", 0, "0", "0.0"):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        if number > 0:
            return datetime.fromtimestamp(number, tz=timezone.utc)
    return parse_datetime(str(value))


def _symbol_expiry(symbol: str) -> datetime | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(symbol)
        if not match:
            continue
        value = match.group(1)
        for fmt in ("%Y%m%d", "%d%b%y"):
            try:
                return datetime.strptime(value.upper(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def classify_contract(item: dict[str, Any], *, product_type: ProductType | None = None) -> tuple[str, datetime | None]:
    symbol = str(item.get("symbol") or item.get("contract") or item.get("name") or "")
    text = " ".join(
        str(item.get(key) or "") for key in (
            "contractType", "contract_type", "instrument_type", "kind", "type",
            "underlyingType", "underlyingSubType",
        )
    ).upper()
    expiry = None
    for key in ("deliveryTime", "delivery_time", "expireTime", "expire_time", "expiry", "expiration_timestamp"):
        expiry = parse_expiry(item.get(key))
        if expiry:
            break
    expiry = expiry or _symbol_expiry(symbol)
    if "TRADIFI" in text or any(token in text for token in ("EQUITY", "ETF", "STOCK", "CFD")):
        return "tradifi_perpetual", expiry
    if "OPTION" in text or (product_type == ProductType.OPTION):
        return "option", expiry
    if expiry or any(token in text for token in ("FUTURE", "DELIVERY", "DATED")):
        return "dated_future", expiry
    if "PERPETUAL" in text:
        return "perpetual", None
    if product_type == ProductType.PERPETUAL:
        metadata_keys = {"contractType", "contract_type", "deliveryTime", "delivery_time", "expireTime", "expire_time", "status", "settleCoin", "quoteCoin"}
        if any(key in item for key in metadata_keys):
            return "perpetual", None
        return "unknown", None
    if product_type == ProductType.SPOT or "SPOT" in text:
        return "spot", None
    if product_type == ProductType.CFD:
        return "cfd", expiry
    return "unknown", expiry


def canonical_product_id(base: str, venue: str, contract_type: str, expiry: datetime | None = None) -> str:
    tag = {
        "perpetual": "PERP",
        "spot": "SPOT",
        "dated_future": "FUT",
        "option": "OPTION",
        "cfd": "CFD",
        "tradifi_perpetual": "TRADIFI_PERP",
    }.get(contract_type, "UNKNOWN")
    suffix = f"_{expiry.astimezone(timezone.utc):%Y%m%d}" if contract_type == "dated_future" and expiry else ""
    return f"{base.upper()}_{venue.upper()}_{tag}{suffix}"
