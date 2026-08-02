from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from engine_v2.domain.enums import AssetClass, DataQuality, ProductType
from engine_v2.domain.models import Candle, Observation, ProductSpec

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult


CATALOG: dict[str, dict[str, Any]] = {
    "QQQ": {"symbol": "QQQ", "name": "Invesco QQQ", "asset_class": AssetClass.ETF, "product_type": ProductType.ETF, "timezone": "America/New_York"},
    "SOXX": {"symbol": "SOXX", "name": "iShares Semiconductor ETF", "asset_class": AssetClass.ETF, "product_type": ProductType.ETF, "timezone": "America/New_York"},
    "SMH": {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "asset_class": AssetClass.ETF, "product_type": ProductType.ETF, "timezone": "America/New_York"},
    "SOXL": {"symbol": "SOXL", "name": "Direxion Daily Semiconductor Bull 3X", "asset_class": AssetClass.ETF, "product_type": ProductType.ETF, "timezone": "America/New_York"},
    "NVDA": {"symbol": "NVDA", "name": "NVIDIA", "asset_class": AssetClass.EQUITY, "product_type": ProductType.EQUITY, "timezone": "America/New_York"},
    "MU": {"symbol": "MU", "name": "Micron Technology", "asset_class": AssetClass.EQUITY, "product_type": ProductType.EQUITY, "timezone": "America/New_York"},
    "TSM": {"symbol": "TSM", "name": "Taiwan Semiconductor", "asset_class": AssetClass.EQUITY, "product_type": ProductType.EQUITY, "timezone": "America/New_York"},
    "SPY": {"symbol": "SPY", "name": "SPDR S&P 500", "asset_class": AssetClass.ETF, "product_type": ProductType.ETF, "timezone": "America/New_York"},
    "VIX": {"symbol": "^VIX", "name": "CBOE Volatility Index", "asset_class": AssetClass.INDEX, "product_type": ProductType.INDEX, "timezone": "America/New_York"},
    "SK_HYNIX_KRX": {"symbol": "000660.KS", "name": "SK Hynix", "asset_class": AssetClass.EQUITY, "product_type": ProductType.EQUITY, "timezone": "Asia/Seoul"},
    "SAMSUNG_KRX": {"symbol": "005930.KS", "name": "Samsung Electronics", "asset_class": AssetClass.EQUITY, "product_type": ProductType.EQUITY, "timezone": "Asia/Seoul"},
    "KOSPI": {"symbol": "^KS11", "name": "KOSPI", "asset_class": AssetClass.INDEX, "product_type": ProductType.INDEX, "timezone": "Asia/Seoul"},
    "KOSDAQ": {"symbol": "^KQ11", "name": "KOSDAQ", "asset_class": AssetClass.INDEX, "product_type": ProductType.INDEX, "timezone": "Asia/Seoul"},
    "USD_KRW": {"symbol": "KRW=X", "name": "USD/KRW", "asset_class": AssetClass.FX, "product_type": ProductType.FX, "timezone": "Asia/Seoul"},
}


class YFinanceDelayedProvider(MarketDataProvider):
    name = "yfinance_delayed"

    def __init__(self, *, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self._capabilities = ProviderCapabilities(
            self.name,
            "yfinance",
            {"product_discovery", "delayed_candles", "session", "reference_price"},
            False,
            True,
            ["Delayed fallback only; never labeled live. Catalog symbols are explicit and not guessed."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        products = []
        for underlying, item in CATALOG.items():
            if underlying_ids and underlying not in underlying_ids:
                continue
            products.append(ProductSpec(
                product_id=f"{underlying}_YF",
                underlying_id=underlying,
                provider=self.name,
                venue="yfinance_delayed",
                venue_symbol=item["symbol"],
                product_type=item["product_type"],
                quote_currency="USD" if underlying not in {"SK_HYNIX_KRX", "SAMSUNG_KRX", "KOSPI", "KOSDAQ", "USD_KRW"} else "KRW",
                short_supported=False,
                price_source="yfinance_delayed",
                is_tradable=item["product_type"] in {ProductType.ETF, ProductType.EQUITY},
                trading_session=item["timezone"],
                capabilities={
                    "display_name": item["name"],
                    "asset_class": item["asset_class"].value,
                    "delay_label": "delayed",
                    "source_symbol": item["symbol"],
                },
                discovered_at=datetime.now(timezone.utc),
            ))
        return ProviderResult(self.name, products=products, quality=DataQuality.DELAYED, reason="delayed_market_catalog", request_count=0)

    async def backfill(self, product: ProductSpec, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        spec = next((item for key, item in CATALOG.items() if f"{key}_YF" == product.product_id), None)
        if spec is None:
            return ProviderResult(self.name, quality=DataQuality.VALIDATION_ERROR, reason="unknown_yfinance_product")
        try:
            rows = await asyncio.to_thread(_history, spec["symbol"], timeframe, limit, self.timeout)
        except Exception as exc:
            return ProviderResult(self.name, quality=DataQuality.PROVIDER_ERROR, reason=f"yfinance:{type(exc).__name__}:{exc}")
        now = datetime.now(timezone.utc)
        observations: list[Observation] = []
        interval_seconds = _interval_seconds(timeframe)
        for row in rows:
            event_time = _to_utc(row["timestamp"], spec["timezone"])
            if event_time is None:
                continue
            close_time = event_time + timedelta(seconds=interval_seconds)
            is_final = close_time <= now
            payload = {
                "product_id": product.product_id,
                "venue": "yfinance",
                "market_type": "spot",
                "open_time": event_time,
                "close_time": close_time,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "quote_volume": None,
                "trade_count": None,
                "is_final": is_final,
                "provider": self.name,
                "delay_estimate_seconds": max(0.0, (now - event_time).total_seconds()),
                "session": _session(event_time, spec["timezone"]),
            }
            candle = Candle(
                product.product_id,
                timeframe,
                event_time,
                close_time,
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                None,
                None,
                is_final,
                self.name,
                now,
                now,
                DataQuality.DELAYED,
            )
            observations.append(Observation(
                str(uuid4()),
                self.name,
                "yfinance_delayed",
                product.product_id,
                f"candle_{timeframe}",
                event_time,
                None,
                now,
                now,
                now,
                None,
                DataQuality.DELAYED,
                "2.0",
                {**candle.to_dict(), **payload},
                "delayed_source",
            ))
        return ProviderResult(self.name, data=observations, quality=DataQuality.DELAYED, reason="delayed_source", request_count=1)


def _history(symbol: str, timeframe: str, limit: int, timeout: float) -> list[dict[str, Any]]:
    import yfinance as yf

    interval = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "1h", "1d": "1d"}.get(timeframe)
    if interval is None:
        raise ValueError(f"unsupported_timeframe:{timeframe}")
    period = "60d" if interval in {"1m", "5m", "15m"} else "730d" if interval == "1h" else "5y"
    frame = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False, prepost=True, timeout=timeout)
    if frame is None or frame.empty:
        return []
    if hasattr(frame.columns, "levels"):
        frame.columns = [column[0] if isinstance(column, tuple) else column for column in frame.columns]
    output = []
    for timestamp, row in frame.tail(limit).iterrows():
        output.append({
            "timestamp": timestamp,
            "open": _number(row.get("Open")),
            "high": _number(row.get("High")),
            "low": _number(row.get("Low")),
            "close": _number(row.get("Close")),
            "volume": _number(row.get("Volume")),
        })
    return [row for row in output if row["close"] is not None]


def _to_utc(value: Any, timezone_name: str) -> datetime | None:
    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(timezone_name))
        return value.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _session(value: datetime, timezone_name: str) -> str:
    local = value.astimezone(ZoneInfo(timezone_name))
    if timezone_name == "America/New_York":
        start, end = (9, 30), (16, 0)
    else:
        start, end = (9, 0), (15, 30)
    minute = local.hour * 60 + local.minute
    if start[0] * 60 + start[1] <= minute < end[0] * 60 + end[1]:
        return "regular"
    return "extended_or_closed"


def _interval_seconds(timeframe: str) -> int:
    return {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}.get(timeframe, 900)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None
