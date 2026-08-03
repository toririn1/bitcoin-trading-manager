from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import DataQuality, ProductType
from engine_v2.domain.models import Candle, Observation, ProductSpec, TradeExecutionActual

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult
from .http import AsyncJSONClient
from .metadata import canonical_product_id, classify_contract, payload_hash


BYBIT_API = "https://api.bybit.com"
BYBIT_INTERVALS = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}


class BybitPublicProvider(MarketDataProvider):
    name = "bybit"

    def __init__(self, *, timeout: float = 8.0, client: AsyncJSONClient | None = None) -> None:
        self.client = client or AsyncJSONClient(timeout)
        self._capabilities = ProviderCapabilities(
            self.name,
            "bybit_linear",
            {"product_discovery", "candles", "trades", "open_interest"},
            False,
            True,
            ["REST backfill implements only candles, recent trades, and open interest; orderbook/funding/liquidation are not claimed."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        response = await self.client.get(BYBIT_API + "/v5/market/instruments-info", {"category": "linear", "limit": 1000})
        body = response.payload if isinstance(response.payload, dict) else {}
        result = body.get("result", {}) if isinstance(body, dict) else {}
        products: list[ProductSpec] = []
        for item in result.get("list", []) if isinstance(result, dict) else []:
            symbol = str(item.get("symbol") or "")
            base = str(item.get("baseCoin") or "").upper()
            if not symbol or not base or (underlying_ids and base not in underlying_ids):
                continue
            if item.get("status") not in ("Trading", None):
                continue
            lot = item.get("lotSizeFilter") or {}
            price = item.get("priceFilter") or {}
            product_type = ProductType.PERPETUAL
            contract_type, expiry = classify_contract(item, product_type=product_type)
            if contract_type == "unknown":
                # Unknown instruments must never masquerade as a tradable perp.
                continue
            if contract_type != "perpetual":
                product_type = ProductType.FUTURE if contract_type == "dated_future" else ProductType.PERPETUAL
            product_id = canonical_product_id(base, "BYBIT", contract_type, expiry, venue_symbol=symbol)
            products.append(ProductSpec(product_id, base, self.name, "bybit_linear", symbol, product_type, quote_currency=item.get("quoteCoin"), settlement_currency=item.get("settleCoin"), contract_size=_number(item.get("contractSize")), tick_size=_number(price.get("tickSize")), lot_size=_number(lot.get("qtyStep")), min_order_size=_number(lot.get("minOrderQty")), max_leverage=_number((item.get("leverageFilter") or {}).get("maxLeverage")), funding_supported=contract_type == "perpetual", short_supported=contract_type in {"perpetual", "dated_future"}, price_source="bybit_public", is_tradable=contract_type in {"perpetual", "dated_future"}, role="tradable" if contract_type in {"perpetual", "dated_future"} else "reference", capabilities={"instrument": item}, discovered_at=datetime.now(timezone.utc), contract_type=contract_type, expiry=expiry, delivery_time=expiry, settlement_asset=item.get("settleCoin"), underlying_reference=base, discovery_payload_hash=payload_hash(item)))
        return ProviderResult(self.name, products=products, request_count=1)

    async def backfill(self, product: ProductSpec, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        if product.provider != self.name:
            return ProviderResult(self.name, quality=DataQuality.VALIDATION_ERROR, reason="provider_product_mismatch")
        result = ProviderResult(self.name)
        interval = BYBIT_INTERVALS.get(timeframe)
        if interval is None:
            return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason=f"unsupported_timeframe:{timeframe}")
        now = datetime.now(timezone.utc)
        body = (await self.client.get(BYBIT_API + "/v5/market/kline", {"category": "linear", "symbol": product.venue_symbol, "interval": interval, "limit": min(limit, 1000)})).payload
        rows = ((body or {}).get("result", {}) if isinstance(body, dict) else {}).get("list", [])
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                continue
            open_time = _dt_ms(row[0])
            open_ms = int(row[0])
            close_ms = open_ms + _interval_seconds(timeframe) * 1000
            close_time = _dt_ms(close_ms)
            candle = Candle(product.product_id, timeframe, open_time or now, close_time, _number(row[1]), _number(row[2]), _number(row[3]), _number(row[4]), _number(row[5]), _number(row[6]), None, bool(close_time and close_time <= now), self.name, now, now, DataQuality.OK if open_time else DataQuality.TIMESTAMP_UNKNOWN)
            result.data.append(Observation(str(uuid4()), self.name, "bybit_linear", product.product_id, f"candle_{timeframe}", open_time, None, now, now, now, None, candle.quality, "2.0", candle.to_dict(), None if open_time else "kline_time_missing"))
        trades = (await self.client.get(BYBIT_API + "/v5/market/recent-trade", {"category": "linear", "symbol": product.venue_symbol, "limit": min(limit, 1000)})).payload
        trade_rows = ((trades or {}).get("result", {}) if isinstance(trades, dict) else {}).get("list", [])
        for row in trade_rows:
            event_time = _dt_ms(row.get("time")) if isinstance(row, dict) else None
            trade = TradeExecutionActual(product.product_id, str(row.get("execId")) if isinstance(row, dict) and row.get("execId") else None, _number(row.get("price")) or 0.0, _number(row.get("size")) or 0.0, str(row.get("side") or "").lower() or None, event_time, self.name, DataQuality.OK if event_time else DataQuality.TIMESTAMP_UNKNOWN)
            result.data.append(Observation(str(uuid4()), self.name, "bybit_linear", product.product_id, "trade", event_time, None, now, now, now, None, trade.quality, "2.0", trade.to_dict(), None if event_time else "trade_time_missing"))
        oi = (await self.client.get(BYBIT_API + "/v5/market/open-interest", {"category": "linear", "symbol": product.venue_symbol, "intervalTime": "1h", "limit": min(limit, 200)})).payload
        oi_rows = ((oi or {}).get("result", {}) if isinstance(oi, dict) else {}).get("list", [])
        for row in oi_rows:
            event_time = _dt_ms(row.get("timestamp")) if isinstance(row, dict) else None
            result.data.append(Observation(str(uuid4()), self.name, "bybit_linear", product.product_id, "open_interest", event_time, None, now, now, now, None, DataQuality.OK if event_time else DataQuality.TIMESTAMP_UNKNOWN, "2.0", {"open_interest": _number(row.get("openInterest")) if isinstance(row, dict) else None}, None if event_time else "oi_time_missing"))
        result.request_count = 3
        return result


def _dt_ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _interval_seconds(timeframe: str) -> int:
    return {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}.get(timeframe, 900)
