from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import DataQuality
from engine_v2.domain.models import Observation

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult
from .http import AsyncJSONClient, ProviderHTTPError


COINGLASS_BASE = "https://open-api-v4.coinglass.com/api"


class CoinGlassProvider(MarketDataProvider):
    name = "coinglass"

    def __init__(self, *, timeout: float = 12.0, client: AsyncJSONClient | None = None) -> None:
        self.client = client or AsyncJSONClient(timeout)
        self._capabilities = ProviderCapabilities(
            self.name,
            "coinglass",
            {
                "actual_liquidation_history",
                "liquidation_heatmap",
                "futures_cvd",
                "spot_cvd",
                "oi_history",
                "basis_history",
            },
            True,
            True,
            ["Implemented endpoints are opt-in because CoinGlass plan and request budget vary by account."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        if not os.getenv("COINGLASS_API_KEY"):
            self._capabilities.notes.append("authentication_required")
        self._capabilities.notes.append(
            "endpoint_status:" + str({
                "futures/liquidation/order": "implemented",
                "futures/liquidation/aggregated-heatmap/model1": "implemented_opt_in",
                "futures/cvd/history": "implemented_opt_in",
                "spot/cvd/history": "implemented_opt_in",
                "futures/open-interest/history": "implemented_opt_in",
                "futures/basis/history": "implemented_opt_in",
                "funding_history": "plan_not_available",
                "etf_flows": "plan_not_available",
            })
        )
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        if os.getenv("COINGLASS_ENABLED", "").lower() not in {"1", "true", "yes"}:
            return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="provider_disabled")
        if not os.getenv("COINGLASS_API_KEY"):
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="api_key_missing")
        return ProviderResult(self.name, quality=DataQuality.PARTIAL, reason="actual_endpoints_configured")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        api_key = os.getenv("COINGLASS_API_KEY")
        if not api_key:
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="api_key_missing")
        result = await self.backfill_liquidations(product, limit=limit)
        if os.getenv("COINGLASS_HISTORY_ENABLED", "").lower() in {"1", "true", "yes"}:
            for loader in (
                self.backfill_futures_cvd,
                self.backfill_spot_cvd,
                self.backfill_open_interest,
                self.backfill_basis,
                self.backfill_heatmap,
            ):
                extra = await loader(product, timeframe=timeframe, limit=limit)
                result.data.extend(extra.data)
                result.request_count += extra.request_count
                if extra.quality in {DataQuality.PLAN_NOT_AVAILABLE, DataQuality.PROVIDER_ERROR}:
                    result.reason = extra.reason
        return result

    async def backfill_liquidations(self, product, *, limit: int = 300) -> ProviderResult:
        return await self._orders(product, limit=limit)

    async def backfill_futures_cvd(self, product, *, timeframe: str = "1h", limit: int = 300) -> ProviderResult:
        return await self._history(
            product,
            "/futures/cvd/history",
            "coinglass_futures_cvd",
            {"exchange": "Binance", "symbol": product.venue_symbol, "interval": _interval(timeframe), "limit": min(limit, 1000)},
        )

    async def backfill_spot_cvd(self, product, *, timeframe: str = "1h", limit: int = 300) -> ProviderResult:
        return await self._history(
            product,
            "/spot/cvd/history",
            "coinglass_spot_cvd",
            {"exchange": "Binance", "symbol": product.venue_symbol, "interval": _interval(timeframe), "limit": min(limit, 1000)},
        )

    async def backfill_open_interest(self, product, *, timeframe: str = "1h", limit: int = 300) -> ProviderResult:
        return await self._history(
            product,
            "/futures/open-interest/history",
            "coinglass_open_interest_history",
            {"exchange": "Binance", "symbol": product.venue_symbol, "interval": _interval(timeframe), "limit": min(limit, 1000), "unit": "usd"},
        )

    async def backfill_basis(self, product, *, timeframe: str = "1h", limit: int = 300) -> ProviderResult:
        return await self._history(
            product,
            "/futures/basis/history",
            "coinglass_basis_history",
            {"exchange": "Binance", "symbol": product.venue_symbol, "interval": _interval(timeframe), "limit": min(limit, 1000)},
        )

    async def backfill_heatmap(self, product, *, timeframe: str = "1h", limit: int = 300) -> ProviderResult:
        api_key = os.getenv("COINGLASS_API_KEY")
        if not api_key:
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="api_key_missing")
        try:
            response = await self.client.get(
                f"{COINGLASS_BASE}/futures/liquidation/aggregated-heatmap/model1",
                {"symbol": product.underlying_id, "range": "3d"},
                {"CG-API-KEY": api_key},
            )
        except ProviderHTTPError as exc:
            return ProviderResult(self.name, quality=_http_quality(exc.status), reason=f"http_{exc.status}")
        body = response.payload if isinstance(response.payload, dict) else {}
        if str(body.get("code")) not in {"0", "200", "None"}:
            return ProviderResult(self.name, quality=DataQuality.PLAN_NOT_AVAILABLE, reason=str(body.get("msg") or "coinglass_heatmap_error"), request_count=1)
        data = body.get("data")
        event_time = _latest_heatmap_time(data)
        collected = datetime.now(timezone.utc)
        observation = Observation(
            str(uuid4()),
            self.name,
            "coinglass",
            product.product_id,
            "liquidation_heatmap",
            event_time,
            None,
            collected,
            collected,
            collected,
            None,
            DataQuality.ESTIMATED if data is not None else DataQuality.PARTIAL,
            "2.0",
            {"product_id": product.product_id, "source": self.name, "actual_endpoint": "futures/liquidation/aggregated-heatmap/model1", "data": data},
            None if event_time else "heatmap_time_missing",
        )
        return ProviderResult(self.name, data=[observation], quality=observation.quality, request_count=1)

    async def _orders(self, product, *, limit: int) -> ProviderResult:
        api_key = os.getenv("COINGLASS_API_KEY")
        try:
            response = await self.client.get(
                f"{COINGLASS_BASE}/futures/liquidation/order",
                {"symbol": product.underlying_id, "limit": min(limit, 1000)},
                {"CG-API-KEY": api_key},
            )
        except ProviderHTTPError as exc:
            return ProviderResult(self.name, quality=_http_quality(exc.status), reason=f"http_{exc.status}")
        body = response.payload if isinstance(response.payload, dict) else {}
        if str(body.get("code")) not in {"0", "200", "None"}:
            return ProviderResult(self.name, quality=DataQuality.PLAN_NOT_AVAILABLE, reason=str(body.get("msg") or "coinglass_api_error"), request_count=1)
        rows = body.get("data") if isinstance(body.get("data"), list) else []
        collected = datetime.now(timezone.utc)
        observations = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            event_time = _epoch(row.get("time"))
            side_value = row.get("side")
            side = "long" if str(side_value) == "1" else "short" if str(side_value) == "2" else "unknown"
            quality = DataQuality.OK if event_time else DataQuality.TIMESTAMP_UNKNOWN
            observations.append(Observation(
                str(uuid4()), self.name, "coinglass", product.product_id, "liquidation_actual",
                event_time, None, collected, collected, collected, None, quality, "2.0",
                {
                    "product_id": product.product_id,
                    "source": self.name,
                    "exchange": row.get("exchange_name"),
                    "symbol": row.get("symbol"),
                    "base_asset": row.get("base_asset"),
                    "side": side,
                    "price": _number(row.get("price")),
                    "notional_usd": _number(row.get("usd_value") or row.get("volume_usd")),
                    "event_time": event_time,
                    "actual": True,
                    "actual_endpoint": "futures/liquidation/order",
                },
                None if event_time else "liquidation_event_time_missing",
            ))
        return ProviderResult(self.name, data=observations, quality=DataQuality.OK if observations else DataQuality.PARTIAL, reason=None if observations else "empty_response_not_no_liquidation", request_count=1)

    async def _history(self, product, path: str, data_type: str, params: dict[str, Any]) -> ProviderResult:
        api_key = os.getenv("COINGLASS_API_KEY")
        if not api_key:
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="api_key_missing")
        try:
            response = await self.client.get(f"{COINGLASS_BASE}{path}", params, {"CG-API-KEY": api_key})
        except ProviderHTTPError as exc:
            return ProviderResult(self.name, quality=_http_quality(exc.status), reason=f"http_{exc.status}")
        body = response.payload if isinstance(response.payload, dict) else {}
        if str(body.get("code")) not in {"0", "200", "None"}:
            return ProviderResult(self.name, quality=DataQuality.PLAN_NOT_AVAILABLE, reason=str(body.get("msg") or f"endpoint_not_available:{path}"), request_count=1)
        rows = body.get("data") if isinstance(body.get("data"), list) else []
        collected = datetime.now(timezone.utc)
        observations = []
        for row in rows:
            payload = dict(row) if isinstance(row, dict) else {"value": row}
            event_time = _epoch(payload.get("time"))
            observations.append(Observation(
                str(uuid4()), self.name, "coinglass", product.product_id, data_type,
                event_time, None, collected, collected, collected, None,
                DataQuality.OK if event_time else DataQuality.TIMESTAMP_UNKNOWN,
                "2.0", {
                    "product_id": product.product_id,
                    "source": self.name,
                    "actual_endpoint": path.removeprefix("/"),
                    **payload,
                },
                None if event_time else "history_time_missing",
            ))
        return ProviderResult(self.name, data=observations, quality=DataQuality.OK if observations else DataQuality.PARTIAL, reason=None if observations else "empty_history", request_count=1)


def _interval(value: str) -> str:
    return value if value in {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "6h", "8h", "12h", "1d", "1w"} else "1h"


def _http_quality(status: int) -> DataQuality:
    return DataQuality.AUTHENTICATION_REQUIRED if status == 401 else DataQuality.PLAN_NOT_AVAILABLE if status in {402, 403} else DataQuality.PROVIDER_ERROR


def _latest_heatmap_time(data: Any) -> datetime | None:
    if not isinstance(data, dict):
        return None
    rows = data.get("price_candlesticks")
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[-1]
    return _epoch(row[0]) if isinstance(row, list) and row else None


def _epoch(value: Any) -> datetime | None:
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None
