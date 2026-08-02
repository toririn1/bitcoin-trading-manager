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
                "aggregated_actual_liquidation_history",
                "modeled_liquidation_heatmap",
                "orderbook_heatmap",
                "futures_cvd",
                "spot_cvd",
                "oi_history",
                "funding_history",
                "basis",
                "etf_flows",
            },
            True,
            True,
            ["Actual liquidation order endpoint is implemented when key and plan allow it; heatmaps remain estimated."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        if not os.getenv("COINGLASS_API_KEY"):
            self._capabilities.notes.append("authentication_required")
        return self.capabilities

    async def discover_products(self, underlying_ids: list[str] | None = None) -> ProviderResult:
        if os.getenv("COINGLASS_ENABLED", "").lower() not in {"1", "true", "yes"}:
            return ProviderResult(self.name, quality=DataQuality.UNSUPPORTED, reason="provider_disabled")
        if not os.getenv("COINGLASS_API_KEY"):
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="api_key_missing")
        return ProviderResult(self.name, quality=DataQuality.PARTIAL, reason="actual_liquidation_order_endpoint_configured")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        api_key = os.getenv("COINGLASS_API_KEY")
        if not api_key:
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="api_key_missing")
        try:
            response = await self.client.get(
                f"{COINGLASS_BASE}/futures/liquidation/order",
                {"symbol": product.underlying_id, "limit": min(limit, 1000)},
                {"CG-API-KEY": api_key},
            )
        except ProviderHTTPError as exc:
            if exc.status == 401:
                return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="api_key_rejected")
            if exc.status in {402, 403}:
                return ProviderResult(self.name, quality=DataQuality.PLAN_NOT_AVAILABLE, reason=f"plan_not_available_http_{exc.status}")
            return ProviderResult(self.name, quality=DataQuality.PROVIDER_ERROR, reason=f"http_{exc.status}")
        body = response.payload if isinstance(response.payload, dict) else {}
        if str(body.get("code")) not in {"0", "200", "None"}:
            return ProviderResult(self.name, quality=DataQuality.PLAN_NOT_AVAILABLE, reason=str(body.get("msg") or "coinglass_api_error"))
        rows = body.get("data") if isinstance(body.get("data"), list) else []
        if not rows:
            return ProviderResult(self.name, quality=DataQuality.PARTIAL, reason="empty_response_not_no_liquidation")
        collected = datetime.now(timezone.utc)
        observations = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            event_time = _epoch(row.get("time"))
            quality = DataQuality.OK if event_time else DataQuality.TIMESTAMP_UNKNOWN
            side_value = row.get("side")
            side = "long" if str(side_value) == "1" else "short" if str(side_value) == "2" else "unknown"
            payload = {
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
            }
            observations.append(Observation(
                str(uuid4()),
                self.name,
                "coinglass",
                product.product_id,
                "liquidation_actual",
                event_time,
                None,
                collected,
                collected,
                collected,
                None,
                quality,
                "2.0",
                payload,
                None if event_time else "liquidation_event_time_missing",
            ))
        return ProviderResult(self.name, data=observations, quality=DataQuality.OK if observations else DataQuality.PARTIAL, reason=None if observations else "empty_response_not_no_liquidation", request_count=1)


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
