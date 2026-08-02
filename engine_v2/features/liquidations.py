from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from engine_v2.domain.enums import DataQuality
from engine_v2.domain.models import LiquidationAggregateActual, LiquidationClusterEstimate, LiquidationEventActual, LiquidationSnapshotPartial


def aggregate_actual(events: Iterable[LiquidationEventActual], *, windows: tuple[str, ...] = ("1m", "5m", "15m", "1h")) -> dict[str, Any]:
    rows = [event for event in events if event.quality == DataQuality.OK and event.event_time is not None and event.notional_usd is not None]
    if not rows:
        return {"quality": DataQuality.PARTIAL.value, "reason": "no_actual_liquidation_events", "aggregates": []}
    rows.sort(key=lambda item: item.event_time)
    end = rows[-1].event_time
    aggregates = []
    seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
    for window in windows:
        start = end - __import__("datetime").timedelta(seconds=seconds.get(window, 3600))
        scoped = [row for row in rows if row.event_time and row.event_time >= start]
        long_usd = sum(row.notional_usd or 0 for row in scoped if row.side.lower() in {"long", "sell", "long_liquidated"})
        short_usd = sum(row.notional_usd or 0 for row in scoped if row.side.lower() in {"short", "buy", "short_liquidated"})
        aggregates.append(LiquidationAggregateActual(rows[0].product_id, window, long_usd, short_usd, len(scoped), start, end, rows[0].source).to_dict())
    return {"quality": DataQuality.OK.value, "reason": None, "aggregates": aggregates}


def classify_snapshot(snapshot: LiquidationSnapshotPartial | dict[str, Any]) -> dict[str, Any]:
    row = snapshot.to_dict() if hasattr(snapshot, "to_dict") else dict(snapshot)
    return {"type": "public_liquidation_snapshot", "actual_totalization_allowed": False, "quality": DataQuality.PARTIAL.value, "data": row, "reason": "snapshot_is_pulse_only_not_market_total"}


def classify_estimate(cluster: LiquidationClusterEstimate | dict[str, Any]) -> dict[str, Any]:
    row = cluster.to_dict() if hasattr(cluster, "to_dict") else dict(cluster)
    return {"type": "estimated_liquidation_cluster", "actual_totalization_allowed": False, "quality": DataQuality.ESTIMATED.value, "data": row, "reason": "modeled_heatmap_is_not_actual_event"}
