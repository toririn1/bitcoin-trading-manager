from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Any, Iterable

from engine_v2.domain.models import parse_datetime


def trade_cvd(trades: Iterable[Any]) -> dict[str, Any]:
    """Compute timestamped, deduplicated CVD without mixing contract units."""
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    duplicate_count = 0
    out_of_order_count = 0
    unknown_timestamp_count = 0
    invalid_count = 0
    previous_input_time = None

    for item in trades:
        row = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        product_id = str(row.get("product_id") or "unknown")
        venue = str(row.get("venue") or row.get("source") or "unknown")
        trade_id = row.get("trade_id") or row.get("id") or row.get("execId")
        if trade_id is not None:
            dedup_key = (product_id, venue, str(trade_id))
        else:
            fingerprint = repr(sorted((key, str(value)) for key, value in row.items() if key not in {"quality"}))
            dedup_key = (product_id, venue, hashlib.sha256(fingerprint.encode()).hexdigest())
        if dedup_key in seen:
            duplicate_count += 1
            continue
        seen.add(dedup_key)

        timestamp = parse_datetime(row.get("event_time") or row.get("source_event_time"))
        if timestamp is None:
            unknown_timestamp_count += 1
            continue
        if previous_input_time is not None and timestamp < previous_input_time:
            out_of_order_count += 1
        previous_input_time = timestamp

        side = str(row.get("aggressor_side") or row.get("side") or "").lower()
        quantity = _number(row.get("quantity") or row.get("qty"))
        price = _number(row.get("price"))
        if quantity is None or quantity < 0 or side not in {"buy", "sell"}:
            invalid_count += 1
            continue
        contract_size = _number(row.get("contract_size")) or 1.0
        market_type = str(row.get("market_type") or row.get("product_type") or "").lower()
        if market_type not in {"spot", "perp", "perpetual", "future", "futures"}:
            market_type = "perp" if "PERP" in product_id.upper() else "spot"
        market_type = "perp" if market_type in {"perpetual", "future", "futures"} else market_type
        accepted.append({
            **row,
            "product_id": product_id,
            "venue": venue,
            "event_time": timestamp,
            "quantity": quantity,
            "price": price,
            "contract_size": contract_size,
            "market_type": market_type,
            "signed_quantity": quantity if side == "buy" else -quantity,
            "signed_notional": (price * quantity * contract_size) * (1 if side == "buy" else -1) if price is not None else None,
        })

    accepted.sort(key=lambda row: (row["product_id"], row["venue"], row["event_time"], str(row.get("trade_id") or "")))
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        grouped[(row["product_id"], row["venue"], row["market_type"])].append(row)

    quantity_by_group: dict[str, float] = {}
    notional_by_group: dict[str, float | None] = {}
    series: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        group_name = "|".join(key)
        quantity_cumulative = 0.0
        notional_cumulative = 0.0
        notional_available = False
        for row in rows:
            quantity_cumulative += row["signed_quantity"]
            if row["signed_notional"] is not None:
                notional_cumulative += row["signed_notional"]
                notional_available = True
            series.append({
                "product_id": row["product_id"],
                "venue": row["venue"],
                "market_type": row["market_type"],
                "trade_id": row.get("trade_id") or row.get("id") or row.get("execId"),
                "event_time": row["event_time"].isoformat().replace("+00:00", "Z"),
                "quantity_delta": row["signed_quantity"],
                "notional_delta_usd": row["signed_notional"],
                "quantity_cumulative": quantity_cumulative,
                "notional_cumulative_usd": notional_cumulative if notional_available else None,
            })
        quantity_by_group[group_name] = quantity_cumulative
        notional_by_group[group_name] = notional_cumulative if notional_available else None

    total_notional = sum(value for value in notional_by_group.values() if value is not None) if notional_by_group else None
    notional_volume = sum(abs(row["signed_notional"]) for row in accepted if row["signed_notional"] is not None)
    quantity_values = list(quantity_by_group.values())
    quantity_cvd = quantity_values[0] if len(quantity_values) == 1 else None
    by_market: dict[str, float] = defaultdict(float)
    venue_by_product: dict[str, set[str]] = defaultdict(set)
    for (product_id, venue, market_type), value in zip(grouped.keys(), notional_by_group.values()):
        venue_by_product[product_id].add(venue)
        if value is not None:
            by_market[market_type] += value
    multi_venue = any(len(venues) > 1 for venues in venue_by_product.values())
    reasons = []
    if unknown_timestamp_count:
        reasons.append("timestamp_unknown_trade_excluded")
    if invalid_count:
        reasons.append("invalid_trade_excluded")
    if duplicate_count:
        reasons.append("duplicate_trade_deduplicated")
    if not accepted:
        reasons.append("no_timestamped_trade_stream")
    quality = "ok" if accepted and not unknown_timestamp_count and not invalid_count else "partial"
    return {
        "trade_cvd": quantity_cvd,
        "quantity_cvd": quantity_cvd,
        "quantity_cvd_by_group": quantity_by_group,
        "notional_cvd_usd": total_notional if total_notional is not None else None,
        "notional_volume_usd": notional_volume or None,
        "notional_cvd_ratio": total_notional / notional_volume if total_notional is not None and notional_volume else None,
        "notional_cvd_usd_by_group": notional_by_group,
        "spot_cvd_usd": by_market.get("spot") if "spot" in by_market else None,
        "perp_cvd_usd": by_market.get("perp") if "perp" in by_market else None,
        "cross_exchange_cvd_usd": total_notional if multi_venue and total_notional is not None else None,
        "trade_count": len(accepted),
        "duplicate_count": duplicate_count,
        "out_of_order_count": out_of_order_count,
        "unknown_timestamp_count": unknown_timestamp_count,
        "invalid_count": invalid_count,
        "series": series,
        "quality": quality,
        "reason": ";".join(reasons) if reasons else None,
    }


def orderbook_features(snapshot: dict[str, Any], *, mid_price: float | None = None) -> dict[str, Any]:
    bids = _levels(snapshot.get("bids", []), descending=True)
    asks = _levels(snapshot.get("asks", []), descending=False)
    if not bids or not asks:
        return {"quality": "partial", "reason": "orderbook_levels_missing", "bids": bids, "asks": asks}
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    if best_bid >= best_ask:
        return {
            "quality": "invalid_semantics",
            "reason": "crossed_book",
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bids": bids,
            "asks": asks,
        }
    midpoint = mid_price or (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / midpoint * 10000 if midpoint else None
    output: dict[str, Any] = {
        "spread_bps": spread_bps,
        "mid_price": midpoint,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "quality": "ok",
        "bids": bids,
        "asks": asks,
    }
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


def _levels(raw: Iterable[Any], *, descending: bool) -> list[tuple[float, float]]:
    result = []
    for row in raw:
        try:
            price, quantity = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if price > 0 and quantity >= 0 and math.isfinite(price) and math.isfinite(quantity):
            result.append((price, quantity))
    return sorted(result, key=lambda item: item[0], reverse=descending)


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


def _number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None
