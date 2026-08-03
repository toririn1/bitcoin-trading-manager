from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import DataQuality, ProductType
from engine_v2.domain.models import Candle, Observation, ProductSpec, TradeExecutionActual, parse_datetime

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult, observation_time
from .http import AsyncJSONClient, ProviderHTTPError
from .metadata import canonical_product_id, classify_contract, payload_hash


BINANCE_FUTURES = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"
TIMEFRAME_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}


def _dt_ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _supported_contract(item: dict[str, Any], *, futures: bool) -> bool:
    """Accept only contracts this provider can model as real crypto products.

    Binance's futures catalog also contains TradFi perpetual rows such as
    NVDAUSDT and SOXLUSDT. They are not Binance spot/perpetual crypto
    products supported by this engine, so they must never be registered as
    tradable ProductSpecs.
    """
    contract_type = str(item.get("contractType") or "").upper()
    underlying_type = str(item.get("underlyingType") or "").upper()
    underlying_subtypes = {
        str(value).upper()
        for value in (item.get("underlyingSubType") or [])
        if value is not None
    }
    if "TRADIFI" in contract_type or "TRADIFI" in underlying_type or "TRADIFI" in underlying_subtypes:
        return False
    if underlying_type in {"EQUITY", "ETF", "STOCK", "INDEX", "FX", "COMMODITY"}:
        return False
    if futures:
        return contract_type == "PERPETUAL"
    return contract_type in {"", "SPOT"}


class BinancePublicProvider(MarketDataProvider):
    name = "binance"

    def __init__(self, *, timeout: float = 8.0, futures: bool = True, client: AsyncJSONClient | None = None) -> None:
        self.futures = futures
        self.client = client or AsyncJSONClient(timeout)
        self._capabilities = ProviderCapabilities(
            provider_name=self.name,
            venue="binance_futures" if futures else "binance_spot",
            capabilities={"product_discovery", "candles", "trades", "orderbook", "mark_price", "funding", "open_interest"},
            requires_auth=False,
            read_only=True,
            notes=["private liquidation endpoint is intentionally not used; public liquidation data is only a pulse/snapshot."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    @property
    def base_url(self) -> str:
        return BINANCE_FUTURES if self.futures else BINANCE_SPOT

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        response = await self.client.get(f"{self.base_url}/fapi/v1/exchangeInfo" if self.futures else f"{self.base_url}/api/v3/exchangeInfo")
        payload = response.payload if isinstance(response.payload, dict) else {}
        products: list[ProductSpec] = []
        for item in payload.get("symbols", []):
            symbol = str(item.get("symbol") or "")
            base = str(item.get("baseAsset") or "").upper()
            if not symbol or not base:
                continue
            underlying = "BTC" if base == "BTC" else base
            if underlying_ids and underlying not in underlying_ids:
                continue
            if not _supported_contract(item, futures=self.futures):
                continue
            product_type = ProductType.PERPETUAL if self.futures else ProductType.SPOT
            contract_type, expiry = classify_contract(item, product_type=product_type)
            if item.get("status") not in ("TRADING", "1", None):
                continue
            filters = {str(f.get("filterType")): f for f in item.get("filters", []) if isinstance(f, dict)}
            price_filter = filters.get("PRICE_FILTER", {})
            lot_filter = filters.get("LOT_SIZE", {})
            products.append(ProductSpec(
                product_id=canonical_product_id(base, "BINANCE", contract_type, venue_symbol=symbol),
                underlying_id=underlying,
                provider=self.name,
                venue=self.capabilities.venue,
                venue_symbol=symbol,
                product_type=product_type,
                quote_currency=item.get("quoteAsset"),
                settlement_currency=item.get("marginAsset") or item.get("quoteAsset"),
                tick_size=_number(price_filter.get("tickSize")),
                lot_size=_number(lot_filter.get("stepSize")),
                min_order_size=_number(lot_filter.get("minQty")),
                funding_supported=product_type == ProductType.PERPETUAL,
                short_supported=product_type == ProductType.PERPETUAL,
                price_source="binance_public",
                is_tradable=True,
                capabilities={"exchange_info": item, "source_event_time": None},
                discovered_at=datetime.now(timezone.utc),
                contract_type=contract_type,
                expiry=expiry,
                delivery_time=expiry,
                settlement_asset=item.get("marginAsset") or item.get("quoteAsset"),
                underlying_reference=base,
                discovery_payload_hash=payload_hash(item),
            ))
        return ProviderResult(self.name, products=products, request_count=1)

    async def backfill(self, product: ProductSpec, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        if product.provider != self.name:
            return ProviderResult(self.name, quality=DataQuality.VALIDATION_ERROR, reason="provider_product_mismatch")
        result = ProviderResult(self.name)
        result.data.extend(await self._candles(product, timeframe, limit))
        result.data.extend(await self._trades(product, min(limit, 1000)))
        result.data.append(await self._depth(product))
        if product.product_type == ProductType.PERPETUAL:
            result.data.extend(await self._premium(product))
            result.data.extend(await self._open_interest(product, min(limit, 500)))
        result.request_count = len(result.data)
        return result

    async def _candles(self, product: ProductSpec, timeframe: str, limit: int) -> list[Observation]:
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            return [self._error_observation(product, f"unsupported_timeframe:{timeframe}", f"candle_{timeframe}")]
        path = "/fapi/v1/klines" if self.futures else "/api/v3/klines"
        response = await self.client.get(self.base_url + path, {"symbol": product.venue_symbol, "interval": interval, "limit": min(max(limit, 1), 1500)})
        rows = response.payload if isinstance(response.payload, list) else []
        collected = datetime.now(timezone.utc)
        observations: list[Observation] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                continue
            open_time, close_time = _dt_ms(row[0]), _dt_ms(row[6])
            candle = Candle(product.product_id, timeframe, open_time or collected, close_time, _number(row[1]), _number(row[2]), _number(row[3]), _number(row[4]), _number(row[5]), _number(row[7]) if len(row) > 7 else None, int(row[8]) if len(row) > 8 and str(row[8]).isdigit() else None, bool(close_time and close_time <= collected), self.name, collected, collected, DataQuality.OK if open_time and close_time else DataQuality.TIMESTAMP_UNKNOWN)
            observations.append(Observation(str(uuid4()), self.name, self.capabilities.venue, product.product_id, f"candle_{timeframe}", open_time, None, collected, collected, collected, None, candle.quality, "2.0", candle.to_dict(), None if candle.quality == DataQuality.OK else "kline_time_missing"))
        return observations

    async def _trades(self, product: ProductSpec, limit: int) -> list[Observation]:
        path = "/fapi/v1/trades" if self.futures else "/api/v3/trades"
        response = await self.client.get(self.base_url + path, {"symbol": product.venue_symbol, "limit": min(limit, 1000)})
        rows = response.payload if isinstance(response.payload, list) else []
        collected = datetime.now(timezone.utc)
        out: list[Observation] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_time = _dt_ms(row.get("time"))
            trade = TradeExecutionActual(product.product_id, str(row.get("id")) if row.get("id") is not None else None, _number(row.get("price")) or 0.0, _number(row.get("qty")) or 0.0, "sell" if row.get("isBuyerMaker") else "buy", event_time, self.name, DataQuality.OK if event_time else DataQuality.TIMESTAMP_UNKNOWN)
            out.append(Observation(str(uuid4()), self.name, self.capabilities.venue, product.product_id, "trade", event_time, None, collected, collected, collected, None, trade.quality, "2.0", trade.to_dict(), None if event_time else "trade_time_missing"))
        return out

    async def _depth(self, product: ProductSpec) -> Observation:
        path = "/fapi/v1/depth" if self.futures else "/api/v3/depth"
        response = await self.client.get(self.base_url + path, {"symbol": product.venue_symbol, "limit": 100})
        payload = response.payload if isinstance(response.payload, dict) else {}
        collected = datetime.now(timezone.utc)
        # Binance REST depth has update IDs but no source event timestamp. Do not
        # pretend collection time is the market event time.
        return Observation(str(uuid4()), self.name, self.capabilities.venue, product.product_id, "orderbook", None, None, collected, collected, collected, None, DataQuality.TIMESTAMP_UNKNOWN, "2.0", {"bids": payload.get("bids", []), "asks": payload.get("asks", []), "last_update_id": payload.get("lastUpdateId")}, "rest_orderbook_event_time_unavailable")

    async def _premium(self, product: ProductSpec) -> list[Observation]:
        response = await self.client.get(self.base_url + "/fapi/v1/premiumIndex", {"symbol": product.venue_symbol})
        payload = response.payload if isinstance(response.payload, dict) else {}
        collected = datetime.now(timezone.utc)
        event_time = _dt_ms(payload.get("time"))
        quality = DataQuality.OK if event_time else DataQuality.TIMESTAMP_UNKNOWN
        return [Observation(str(uuid4()), self.name, self.capabilities.venue, product.product_id, "mark_funding", event_time, None, collected, collected, collected, None, quality, "2.0", {"mark_price": _number(payload.get("markPrice")), "index_price": _number(payload.get("indexPrice")), "funding_rate": _number(payload.get("lastFundingRate")), "next_funding_time": _dt_ms(payload.get("nextFundingTime"))}, None if event_time else "premium_index_time_missing")]

    async def _open_interest(self, product: ProductSpec, limit: int) -> list[Observation]:
        response = await self.client.get(self.base_url + "/futures/data/openInterestHist", {"symbol": product.venue_symbol, "period": "1h", "limit": min(limit, 500)})
        rows = response.payload if isinstance(response.payload, list) else []
        collected = datetime.now(timezone.utc)
        out: list[Observation] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_time = _dt_ms(row.get("timestamp"))
            quality = DataQuality.OK if event_time else DataQuality.TIMESTAMP_UNKNOWN
            out.append(Observation(str(uuid4()), self.name, self.capabilities.venue, product.product_id, "open_interest", event_time, None, collected, collected, collected, None, quality, "2.0", {"open_interest": _number(row.get("sumOpenInterest")), "open_interest_value": _number(row.get("sumOpenInterestValue"))}, None if event_time else "oi_time_missing"))
        return out

    def _error_observation(self, product: ProductSpec, reason: str, data_type: str) -> Observation:
        collected = datetime.now(timezone.utc)
        return Observation(str(uuid4()), self.name, self.capabilities.venue, product.product_id, data_type, None, None, collected, collected, collected, None, DataQuality.PROVIDER_ERROR, "2.0", {}, reason)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
