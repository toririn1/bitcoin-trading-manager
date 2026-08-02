from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import DataQuality, ProductType
from engine_v2.domain.models import Candle, Observation, ProductSpec

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult
from .http import AsyncJSONClient
from .metadata import canonical_product_id, classify_contract, payload_hash


GATE_API = "https://api.gateio.ws/api/v4"


class GateFuturesProvider(MarketDataProvider):
    name = "gate_futures"

    def __init__(self, *, settle: str = "usdt", timeout: float = 8.0, client: AsyncJSONClient | None = None) -> None:
        self.settle = settle
        self.client = client or AsyncJSONClient(timeout)
        self._capabilities = ProviderCapabilities(
            self.name,
            f"gate_{settle}_futures",
            {"product_discovery", "candles"},
            False,
            True,
            ["Only contracts and candlesticks are implemented; ticker/orderbook/trades/mark/funding/open_interest are not claimed."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        body = await self.client.get(f"{GATE_API}/futures/{self.settle}/contracts")
        rows = body.payload if isinstance(body.payload, list) else []
        products: list[ProductSpec] = []
        for item in rows:
            contract = str(item.get("name") or item.get("contract") or "") if isinstance(item, dict) else ""
            base = _base_asset(contract)
            if not contract or not base or (underlying_ids and base not in underlying_ids):
                continue
            item = item if isinstance(item, dict) else {}
            contract_type, expiry = classify_contract(item, product_type=ProductType.PERPETUAL)
            product_type = ProductType.FUTURE if contract_type == "dated_future" else ProductType.PERPETUAL
            product_id = canonical_product_id(base, "GATE", contract_type, expiry)
            tradable = contract_type in {"perpetual", "dated_future"}
            products.append(ProductSpec(product_id, base, self.name, f"gate_{self.settle}_futures", contract, product_type, quote_currency=self.settle.upper(), settlement_currency=self.settle.upper(), contract_size=_number(item.get("quanto_multiplier")), tick_size=_number(item.get("order_price_round")), min_order_size=_number(item.get("order_size_min")), max_leverage=_number(item.get("leverage_max")), funding_supported=contract_type == "perpetual", short_supported=tradable, price_source="gate_futures_public", is_tradable=tradable, role="tradable" if tradable else "reference", capabilities={"contract": item}, discovered_at=datetime.now(timezone.utc), contract_type=contract_type, expiry=expiry, delivery_time=expiry, settlement_asset=self.settle.upper(), underlying_reference=base, discovery_payload_hash=payload_hash(item)))
        return ProviderResult(self.name, products=products, request_count=1)

    async def backfill(self, product: ProductSpec, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        interval = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "7d"}.get(timeframe)
        if not interval:
            return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason=f"unsupported_timeframe:{timeframe}")
        now = datetime.now(timezone.utc)
        body = await self.client.get(f"{GATE_API}/futures/{self.settle}/candlesticks", {"contract": product.venue_symbol, "interval": interval, "limit": min(limit, 2000)})
        rows = body.payload if isinstance(body.payload, list) else []
        observations: list[Observation] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_time = _dt_epoch(row.get("t") or row.get("timestamp"))
            interval_seconds = _interval_seconds(timeframe)
            close_time = event_time + timedelta(seconds=interval_seconds) if event_time else None
            is_final = close_time <= now if close_time else None
            quality = DataQuality.OK if event_time and close_time else DataQuality.TIMESTAMP_UNKNOWN
            candle = Candle(product.product_id, timeframe, event_time or now, close_time, _number(row.get("o")), _number(row.get("h")), _number(row.get("l")), _number(row.get("c")), _number(row.get("v")), _number(row.get("sum")), None, is_final, self.name, now, now, quality)
            reason = None if quality == DataQuality.OK else "candle_time_missing"
            observations.append(Observation(str(uuid4()), self.name, self.capabilities.venue, product.product_id, f"candle_{timeframe}", event_time, None, now, now, now, None, candle.quality, "2.0", candle.to_dict(), reason))
        return ProviderResult(self.name, data=observations, request_count=1)


def _base_asset(contract: str) -> str:
    return contract.split("_")[0].upper() if "_" in contract else contract.replace("USDT", "").replace("USD", "").upper()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _interval_seconds(timeframe: str) -> int:
    return {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}.get(timeframe, 900)


def _dt_epoch(value: Any) -> datetime | None:
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
