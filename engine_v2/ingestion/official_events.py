from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from engine_v2.domain.enums import DataQuality
from engine_v2.domain.models import Observation

from .base import MarketDataProvider, ProviderCapabilities, ProviderResult
from .http import AsyncJSONClient, ProviderHTTPError


BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data"
BEA_API = "https://apps.bea.gov/api/data"


class OfficialEventsProvider(MarketDataProvider):
    name = "official_events"

    def __init__(self, *, timeout: float = 12.0, client: AsyncJSONClient | None = None) -> None:
        self.client = client or AsyncJSONClient(timeout)
        self._capabilities = ProviderCapabilities(
            self.name,
            "economic_series",
            {"bls_series", "bea_series"},
            False,
            True,
            ["BLS/BEA period observations only; release timestamps are unavailable, so this is not an event/reaction connector."],
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def discover_capabilities(self) -> ProviderCapabilities:
        if not os.getenv("BEA_API_KEY"):
            self._capabilities.notes.append("bea_api_key_missing")
        return self.capabilities

    async def discover_products(self, underlying_ids=None) -> ProviderResult:
        return ProviderResult(self.name, quality=DataQuality.PLAN_NOT_AVAILABLE, reason="economic_series_provider_not_tradable")

    async def backfill(self, product, *, timeframe: str = "15m", limit: int = 300) -> ProviderResult:
        return await self.fetch_series(limit=limit)

    async def fetch_series(self, *, limit: int = 20) -> ProviderResult:
        results = []
        for loader in (self.fetch_bls, self.fetch_bea):
            result = await loader(limit=limit)
            results.extend(result.data)
        return ProviderResult(
            self.name,
            data=results,
            quality=DataQuality.OK if results else DataQuality.PARTIAL,
            reason=None if results else "economic_series_sources_empty_or_unconfigured",
            request_count=2,
        )

    async def fetch_events(self, *, limit: int = 20) -> ProviderResult:
        """Release-event ingestion is intentionally unavailable.

        BLS/BEA observations expose measurement periods, not publication
        timestamps. They must not be consumed as post-release event data.
        """
        return ProviderResult(
            self.name,
            quality=DataQuality.PLAN_NOT_AVAILABLE,
            reason="release_timestamp_not_available",
            request_count=0,
        )

    async def fetch_bls(self, *, series_id: str = "CUSR0000SA0", limit: int = 20) -> ProviderResult:
        now = datetime.now(timezone.utc)
        try:
            response = await self.client.get(
                f"{BLS_API}/{series_id}",
                {"startyear": str(now.year - 2), "endyear": str(now.year)},
            )
        except ProviderHTTPError as exc:
            return ProviderResult(self.name, quality=DataQuality.PROVIDER_ERROR, reason=f"bls_http_{exc.status or 'error'}", request_count=1)
        body = response.payload if isinstance(response.payload, dict) else {}
        if body.get("status") != "REQUEST_SUCCEEDED":
            return ProviderResult(self.name, quality=DataQuality.PROVIDER_ERROR, reason="bls_request_failed", request_count=1)
        rows = []
        series = (body.get("Results") or {}).get("series") if isinstance(body.get("Results"), dict) else []
        for item in series or []:
            for row in (item.get("data") or [])[:limit]:
                period_start = _period_time(row)
                rows.append(_economic_observation(
                    "bls",
                    period_start,
                    {
                        "source": "bls",
                        "series_id": series_id,
                        "year": row.get("year"),
                        "period": row.get("period"),
                        "period_name": row.get("periodName"),
                        "value": row.get("value"),
                        "footnotes": row.get("footnotes"),
                        "actual_endpoint": "api.bls.gov/publicAPI/v2/timeseries/data",
                    },
                ))
        return ProviderResult(self.name, data=rows, quality=DataQuality.OK if rows else DataQuality.PARTIAL, reason=None if rows else "bls_empty", request_count=1)

    async def fetch_bea(self, *, dataset: str = "NIPA", limit: int = 20) -> ProviderResult:
        api_key = os.getenv("BEA_API_KEY")
        if not api_key:
            return ProviderResult(self.name, quality=DataQuality.AUTHENTICATION_REQUIRED, reason="bea_api_key_missing", request_count=0)
        try:
            response = await self.client.get(
                BEA_API,
                {
                    "UserID": api_key,
                    "method": "GetData",
                    "datasetname": dataset,
                    "TableName": "T10101",
                    "Frequency": "Q",
                    "Year": "ALL",
                    "ResultFormat": "JSON",
                },
            )
        except ProviderHTTPError as exc:
            return ProviderResult(self.name, quality=DataQuality.PROVIDER_ERROR, reason=f"bea_http_{exc.status or 'error'}", request_count=1)
        body = response.payload if isinstance(response.payload, dict) else {}
        rows = ((body.get("BEAAPI") or {}).get("Results") or {}).get("Data") if isinstance(body.get("BEAAPI"), dict) else []
        observations = []
        for row in (rows or [])[-limit:]:
            period_start = _bea_time(row.get("TimePeriod"))
            observations.append(_economic_observation(
                "bea",
                period_start,
                {
                    "source": "bea",
                    "dataset": dataset,
                    "table": row,
                    "actual_endpoint": "apps.bea.gov/api/data",
                },
            ))
        return ProviderResult(self.name, data=observations, quality=DataQuality.OK if observations else DataQuality.PARTIAL, reason=None if observations else "bea_empty", request_count=1)


def _economic_observation(
    source: str,
    period_start: datetime | None,
    payload: dict[str, Any],
) -> Observation:
    collected = datetime.now(timezone.utc)
    enriched = {
        **payload,
        "period_start": period_start.isoformat().replace("+00:00", "Z") if period_start else None,
        "timestamp_semantics": "measurement_period_only",
        "release_timestamp_available": False,
    }
    return Observation(
        str(uuid4()),
        "official_events",
        "official_events",
        None,
        "economic_series",
        None,
        None,
        collected,
        collected,
        collected,
        None,
        DataQuality.TIMESTAMP_UNKNOWN,
        "2.0",
        enriched,
        "release_timestamp_unavailable",
    )


def _period_time(row: dict[str, Any]) -> datetime | None:
    try:
        year = int(row.get("year"))
        period = str(row.get("period") or "")
        month = int(period[1:]) if period.startswith("M") else 12 if period == "Q04" else 1
        return datetime(year, month, 1, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _bea_time(value: Any) -> datetime | None:
    text = str(value or "")
    try:
        if "Q" in text:
            year, quarter = text.split("Q", 1)
            return datetime(int(year), int(quarter) * 3, 1, tzinfo=timezone.utc)
        return datetime(int(text[:4]), 1, 1, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
