"""Migration-safe post-analysis performance evaluator."""
from __future__ import annotations

from typing import Any


PERFORMANCE_DEFAULTS = {
    "return_30m": None,
    "return_1h": None,
    "return_4h": None,
    "MFE": None,
    "MAE": None,
    "invalidation_hit_first": None,
    "target_hit_first": None,
    "signal_result": None,
}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _horizon_close(rows: list[dict], minutes: int, fallback_index: int) -> float | None:
    timed = []
    for row in rows:
        elapsed = _number(row.get("minutes_after"))
        close = _number(row.get("close"))
        if elapsed is not None and close is not None:
            timed.append((elapsed, close))
    if timed:
        for elapsed, close in sorted(timed):
            if elapsed >= minutes:
                return close
        return None
    if len(rows) > fallback_index:
        return _number(rows[fallback_index].get("close"))
    return None


def evaluate_analysis_record(record: dict, candles: list[dict] | None) -> dict:
    """Return performance fields; insufficient future candles remain null."""
    result = dict(PERFORMANCE_DEFAULTS)
    if not record or not candles:
        return result

    levels = record.get("trade_levels") if isinstance(record.get("trade_levels"), dict) else {}
    entry = _number(record.get("price") or levels.get("entry"))
    if entry in (None, 0):
        return result

    rows = [row for row in candles if isinstance(row, dict) and _number(row.get("close")) is not None]
    if not rows:
        return result

    direction = str(record.get("signal") or record.get("market_direction") or "")
    sign = 1 if direction in ("매수", "상방 우위", "bullish") else -1 if direction in ("매도", "하방 우위", "bearish") else 0
    horizons = (
        ("return_30m", 30, 0),
        ("return_1h", 60, 1),
        ("return_4h", 240, 3),
    )
    for label, minutes, fallback_index in horizons:
        close = _horizon_close(rows, minutes, fallback_index)
        if close is not None:
            result[label] = round((close - entry) / entry * 100, 4)

    highs = [_number(row.get("high")) or _number(row.get("close")) for row in rows]
    lows = [_number(row.get("low")) or _number(row.get("close")) for row in rows]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    if highs and lows:
        favorable = max(highs) - entry if sign >= 0 else entry - min(lows)
        adverse = min(lows) - entry if sign >= 0 else entry - max(highs)
        result["MFE"] = round(favorable / entry * 100, 4)
        result["MAE"] = round(adverse / entry * 100, 4)

    stop = _number(levels.get("stop"))
    target = _number(levels.get("target"))
    for row in rows:
        high = _number(row.get("high")) or _number(row.get("close"))
        low = _number(row.get("low")) or _number(row.get("close"))
        if high is None or low is None:
            continue
        if sign < 0:
            hit_stop = stop is not None and high >= stop
            hit_target = target is not None and low <= target
        else:
            hit_stop = stop is not None and low <= stop
            hit_target = target is not None and high >= target
        if hit_stop and hit_target:
            # Intrabar ordering is unknowable without lower-timeframe data.
            break
        if hit_stop or hit_target:
            result["invalidation_hit_first"] = bool(hit_stop)
            result["target_hit_first"] = bool(hit_target)
            break

    if result["return_4h"] is not None and sign:
        signed_return = result["return_4h"] * sign
        result["signal_result"] = "win" if signed_return > 0 else "loss" if signed_return < 0 else "neutral"
    return result
