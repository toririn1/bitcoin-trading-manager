from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable


def trade_cvd(trades: Iterable[Any]) -> dict[str, Any]:
    buy = sell = 0.0
    points: list[dict[str, Any]] = []
    for item in trades:
        row = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        side = str(row.get("aggressor_side") or row.get("side") or "").lower()
        quantity = _number(row.get("quantity") or row.get("qty"))
        if quantity is None or side not in {"buy", "sell"}:
            continue
        signed = quantity if side == "buy" else -quantity
        buy += quantity if side == "buy" else 0
        sell += quantity if side == "sell" else 0
        points.append({"event_time": row.get("event_time"), "delta": signed, "product_id": row.get("product_id")})
    cumulative = 0.0
    for point in points:
        cumulative += point["delta"]
        point["cumulative"] = cumulative
    return {"trade_cvd": cumulative if points else None, "buy_volume": buy if points else None, "sell_volume": sell if points else None, "trade_count": len(points), "series": points, "quality": "ok" if points else "partial", "reason": None if points else "no_timestamped_trade_stream"}


def orderbook_features(snapshot: dict[str, Any], *, mid_price: float | None = None) -> dict[str, Any]:
    bids = _levels(snapshot.get("bids", []))
    asks = _levels(snapshot.get("asks", []))
    if not bids or not asks:
        return {"quality": "partial", "reason": "orderbook_levels_missing"}
    midpoint = mid_price or (bids[0][0] + asks[0][0]) / 2
    spread_bps = (asks[0][0] - bids[0][0]) / midpoint * 10000 if midpoint else None
    output: dict[str, Any] = {"spread_bps": spread_bps, "mid_price": midpoint, "quality": "ok"}
    for band in (0.001, 0.0025, 0.005, 0.01):
        bid_depth = sum(price * quantity for price, quantity in bids if price >= midpoint * (1 - band))
        ask_depth = sum(price * quantity for price, quantity in asks if price <= midpoint * (1 + band))
        total = bid_depth + ask_depth
        output[f"depth_usd_{band:.2%}"] = total if total else None
        output[f"distance_weighted_imbalance_{band:.2%}"] = (bid_depth - ask_depth) / total if total else None
    output["book_slope"] = _book_slope(bids, asks, midpoint)
    output["liquidity_vacuum_score"] = _vacuum(output)
    return output


def wall_states(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, str]:
    if not previous:
        return {"bid": "new", "ask": "new"}
    output = {}
    for side in ("bid", "ask"):
        old = _number(previous.get(side)) or 0
        new = _number(current.get(side)) or 0
        if old == 0 and new > 0:
            state = "new"
        elif new == 0 and old > 0:
            state = "cancelled"
        elif new > old * 1.1:
            state = "replenishing"
        elif new < old * 0.7:
            state = "consumed"
        else:
            state = "persistent"
        output[side] = state
    return output


def _levels(raw: Iterable[Any]) -> list[tuple[float, float]]:
    result = []
    for row in raw:
        try:
            price, quantity = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if price > 0 and quantity >= 0 and math.isfinite(price) and math.isfinite(quantity):
            result.append((price, quantity))
    return sorted(result, reverse=True)


def _number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _book_slope(bids, asks, midpoint):
    points = []
    for price, quantity in bids[:20] + asks[:20]:
        distance = abs(price - midpoint) / midpoint if midpoint else 0
        if distance:
            points.append(quantity / distance)
    return sum(points) / len(points) if points else None


def _vacuum(features: dict[str, Any]) -> float | None:
    depths = [features.get(f"depth_usd_{band:.2%}") for band in (0.001, 0.0025, 0.005)]
    depths = [value for value in depths if value is not None]
    if len(depths) < 2 or depths[-1] <= 0:
        return None
    return max(0.0, min(1.0, 1 - depths[0] / depths[-1]))
