from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import DataQuality, ProductType
from engine_v2.domain.models import Observation, ProductSpec

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult
from .http import AsyncJSONClient


DERIBIT_API = "https://www.deribit.com/api/v2"


class DeribitOptionsProvider(MarketDataProvider):
    name = "deribit"

    def __init__(self, *, timeout: float = 8.0, client: AsyncJSONClient | None = None) -> None:
        self.client = client or AsyncJSONClient(timeout)
        self._capabilities = ProviderCapabilities(self.name, "deribit", {"option_instruments", "ticker", "actual_greeks", "delta", "mark_iv", "bid_iv", "ask_iv", "expiration", "strike", "underlying_index", "dvol"}, False, True, ["rr_25d/rr_10d are computed only from actual delta fields."])

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        currencies = [item for item in (underlying_ids or ["BTC", "ETH"]) if item in {"BTC", "ETH"}]
        products: list[ProductSpec] = []
        requests = 0
        for currency in currencies:
            body = await self.client.get(DERIBIT_API + "/public/get_instruments", {"currency": currency, "kind": "option", "expired": "false"})
            requests += 1
            result = body.payload.get("result", []) if isinstance(body.payload, dict) else []
            for item in result:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("instrument_name") or "")
                if not name:
                    continue
                products.append(ProductSpec(f"{currency}_DERIBIT_OPTION_{name}", currency, self.name, "deribit", name, ProductType.OPTION, quote_currency="USD", settlement_currency=item.get("settlement_currency"), contract_size=_number(item.get("contract_size")), tick_size=_number(item.get("tick_size")), short_supported=True, price_source="deribit_public", is_tradable=False, capabilities={"instrument": item}, discovered_at=datetime.now(timezone.utc)))
        return ProviderResult(self.name, products=products, request_count=requests)

    async def backfill(self, product: ProductSpec, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        body = await self.client.get(DERIBIT_API + "/public/ticker", {"instrument_name": product.venue_symbol})
        payload = body.payload.get("result", {}) if isinstance(body.payload, dict) else {}
        collected = datetime.now(timezone.utc)
        event_time = _dt_ms(payload.get("timestamp"))
        quality = DataQuality.OK if event_time and payload.get("greeks") else DataQuality.PARTIAL
        observation = Observation(str(uuid4()), self.name, "deribit", product.product_id, "option", event_time, None, collected, collected, collected, None, quality, "2.0", {"instrument_name": product.venue_symbol, "underlying_price": _number(payload.get("underlying_price")), "mark_price": _number(payload.get("mark_price")), "mark_iv": _number(payload.get("mark_iv")), "bid_iv": _number(payload.get("bid_iv")), "ask_iv": _number(payload.get("ask_iv")), "greeks": payload.get("greeks") or {}, "strike": product.capabilities.get("instrument", {}).get("strike"), "option_type": product.capabilities.get("instrument", {}).get("option_type"), "expiration": product.capabilities.get("instrument", {}).get("expiration_timestamp")}, None if quality == DataQuality.OK else "greeks_or_timestamp_missing")
        return ProviderResult(self.name, data=[observation], request_count=1)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dt_ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
