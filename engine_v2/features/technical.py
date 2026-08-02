from __future__ import annotations

import math
from typing import Any, Iterable


def _rows(candles: Iterable[Any], *, closed_only: bool = True) -> list[dict[str, Any]]:
    rows = []
    for candle in candles:
        row = candle.to_dict() if hasattr(candle, "to_dict") else dict(candle)
        if closed_only and row.get("is_final") is not True:
            continue
        if row.get("close") is None:
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("open_time") or row.get("source_event_time") or ""))


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for item in values[period:]:
        value = alpha * item + (1 - alpha) * value
    return value


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((period - 1) * avg_gain + gain) / period
        avg_loss = ((period - 1) * avg_loss + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _atr(rows: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(rows) <= period:
        return None
    true_ranges: list[float] = []
    previous_close = None
    for row in rows:
        high, low, close = row.get("high"), row.get("low"), row.get("close")
        if None in (high, low, close):
            continue
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)) if previous_close is not None else high - low)
        previous_close = close
    return sum(true_ranges[-period:]) / period if len(true_ranges) >= period else None


def closed_candle_features(candles: Iterable[Any], *, minimum_samples: int = 30) -> dict[str, Any]:
    rows = _rows(candles, closed_only=True)
    values = [float(row["close"]) for row in rows if isinstance(row.get("close"), (int, float)) and math.isfinite(float(row["close"]))]
    latest = values[-1] if values else None
    output: dict[str, Any] = {
        "closed_candle_count": len(rows),
        "forming_candle_count": sum(1 for row in _rows(candles, closed_only=False) if row.get("is_final") is False),
        "latest_close": latest,
        "latest_close_quality": "ok" if latest is not None and len(values) >= minimum_samples else "insufficient_data",
    }
    if len(values) < minimum_samples:
        for name in (
            "return_1", "return_4", "return_24", "return_1h", "return_4h",
            "return_1d", "rsi_14_closed", "ema_20_closed", "ema_50_closed",
            "ema_100_closed", "ema_200_closed", "atr_14_pct_closed",
            "volatility_20", "adx_14", "plus_di_14", "minus_di_14",
            "donchian_position_20", "bollinger_percent_b", "vwap_distance_pct",
        ):
            output[name] = None
        output["quality"] = "partial"
        output["reason"] = f"closed_candles<{minimum_samples}"
        return output
    for horizon in (1, 4, 24):
        previous = values[-(horizon + 1)] if len(values) > horizon else None
        output[f"return_{horizon}"] = (latest / previous - 1) if latest is not None and previous not in (None, 0) else None
    output["rsi_14_closed"] = _rsi(values, 14)
    output["ema_20_closed"] = _ema(values, 20)
    output["ema_50_closed"] = _ema(values, 50)
    output["ema_100_closed"] = _ema(values, 100)
    output["ema_200_closed"] = _ema(values, 200)
    output["return_1h"] = output["return_1"]
    output["return_4h"] = output["return_4"]
    output["return_1d"] = output["return_24"]
    atr = _atr(rows, 14)
    output["atr_14_pct_closed"] = atr / latest if atr is not None and latest else None
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1]]
    if len(returns) >= 20:
        window = returns[-20:]
        mean = sum(window) / len(window)
        output["volatility_20"] = (sum((value - mean) ** 2 for value in window) / (len(window) - 1)) ** 0.5
    else:
        output["volatility_20"] = None
    output["trend_state"] = "bullish" if output["ema_20_closed"] and latest and latest > output["ema_20_closed"] else "bearish" if output["ema_20_closed"] and latest and latest < output["ema_20_closed"] else "unknown"
    output.update(_advanced_features(rows, values, latest, atr))
    output["quality"] = "ok"
    return output


def _ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    alpha = 2 / (period + 1)
    current = sum(values[:period]) / period
    output = [current]
    for item in values[period:]:
        current = alpha * item + (1 - alpha) * current
        output.append(current)
    return output


def _slope(values: list[float], window: int = 5) -> float | None:
    if len(values) <= window:
        return None
    return (values[-1] - values[-1 - window]) / max(abs(values[-1 - window]), 1e-12)


def _advanced_features(rows: list[dict[str, Any]], values: list[float], latest: float | None, atr: float | None) -> dict[str, Any]:
    ema20 = _ema_series(values, 20)
    ema50 = _ema_series(values, 50)
    ema100 = _ema_series(values, 100)
    ema200 = _ema_series(values, 200)
    highs = [float(row["high"]) for row in rows if row.get("high") is not None]
    lows = [float(row["low"]) for row in rows if row.get("low") is not None]
    volumes = [float(row.get("volume") or 0) for row in rows]
    output: dict[str, Any] = {
        "ema_slope_20": _slope(ema20),
        "ema_slope_50": _slope(ema50),
        "ema_slope_100": _slope(ema100),
        "ema_slope_200": _slope(ema200),
        "ema_separation_20_50_atr": (
            (ema20[-1] - ema50[-1]) / atr if ema20 and ema50 and atr else None
        ),
        "ema_separation_50_200_atr": (
            (ema50[-1] - ema200[-1]) / atr if ema50 and ema200 and atr else None
        ),
        "donchian_position_20": None,
        "bollinger_percent_b": None,
        "compression": None,
        "expansion": None,
        "vwap_distance_pct": None,
        "previous_day_high": None,
        "previous_day_low": None,
        "previous_week_high": None,
        "previous_week_low": None,
    }
    if latest is not None and highs and lows:
        high20, low20 = max(highs[-20:]), min(lows[-20:])
        output["donchian_position_20"] = (latest - low20) / max(high20 - low20, 1e-12)
        window = values[-20:]
        mean = sum(window) / len(window)
        std = (sum((item - mean) ** 2 for item in window) / len(window)) ** 0.5
        output["bollinger_percent_b"] = (latest - (mean - 2 * std)) / max(4 * std, 1e-12)
        output["compression"] = bool(atr and (high20 - low20) / max(latest, 1e-12) < 0.02)
        output["expansion"] = bool(atr and (highs[-1] - lows[-1]) > atr * 1.8)
        if sum(volumes):
            vwap = sum(row["close"] * float(row.get("volume") or 0) for row in rows) / sum(volumes)
            output["vwap_distance_pct"] = latest / vwap - 1 if vwap else None
        output["previous_day_high"] = max(highs[-96:-1]) if len(highs) > 96 else max(highs[:-1] or highs)
        output["previous_day_low"] = min(lows[-96:-1]) if len(lows) > 96 else min(lows[:-1] or lows)
        output["previous_week_high"] = max(highs[-672:-1]) if len(highs) > 672 else max(highs[:-1] or highs)
        output["previous_week_low"] = min(lows[-672:-1]) if len(lows) > 672 else min(lows[:-1] or lows)
    output.update(_directional_movement(rows))
    output["structure"] = adaptive_market_structure(rows, atr=atr)
    return output


def _directional_movement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 15:
        return {"adx_14": None, "plus_di_14": None, "minus_di_14": None}
    true_ranges = []
    plus = []
    minus = []
    for previous, current in zip(rows, rows[1:]):
        high = float(current.get("high") or current.get("close"))
        low = float(current.get("low") or current.get("close"))
        prev_close = float(previous.get("close"))
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        up = high - float(previous.get("high") or previous.get("close"))
        down = float(previous.get("low") or previous.get("close")) - low
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    tr = sum(true_ranges[-14:])
    plus_sum = sum(plus[-14:])
    minus_sum = sum(minus[-14:])
    plus_di = plus_sum / max(tr, 1e-12) * 100
    minus_di = minus_sum / max(tr, 1e-12) * 100
    dx = abs(plus_di - minus_di) / max(plus_di + minus_di, 1e-12) * 100
    return {"adx_14": dx, "plus_di_14": plus_di, "minus_di_14": minus_di}


def adaptive_market_structure(rows: Iterable[Any], *, atr: float | None = None) -> dict[str, Any]:
    data = _rows(rows, closed_only=True)
    if len(data) < 7:
        return {
            "state": "insufficient_data",
            "swings": [],
            "labels": [],
            "bos": None,
            "choch_proxy": None,
            "range_high": None,
            "range_low": None,
            "failed_breakout": False,
            "sweep_reclaim": False,
            "pivot_confirmation_time": None,
        }
    closes = [float(row["close"]) for row in data]
    window = max(2, min(5, int(len(data) ** 0.5)))
    pivots = []
    for index in range(window, len(data) - window):
        high = float(data[index].get("high") or closes[index])
        low = float(data[index].get("low") or closes[index])
        prior_highs = [float(item.get("high") or item.get("close")) for item in data[index-window:index]]
        next_highs = [float(item.get("high") or item.get("close")) for item in data[index+1:index+window+1]]
        prior_lows = [float(item.get("low") or item.get("close")) for item in data[index-window:index]]
        next_lows = [float(item.get("low") or item.get("close")) for item in data[index+1:index+window+1]]
        if high >= max(prior_highs + next_highs):
            pivots.append({"kind": "high", "price": high, "index": index, "confirmed_at": data[index + window].get("open_time")})
        if low <= min(prior_lows + next_lows):
            pivots.append({"kind": "low", "price": low, "index": index, "confirmed_at": data[index + window].get("open_time")})
    pivots.sort(key=lambda item: item["index"])
    highs = [item for item in pivots if item["kind"] == "high"]
    lows = [item for item in pivots if item["kind"] == "low"]
    labels = []
    for values, kind in ((highs, "high"), (lows, "low")):
        for previous, current in zip(values, values[1:]):
            labels.append({
                "label": ("HH" if current["price"] > previous["price"] else "LH") if kind == "high" else ("HL" if current["price"] > previous["price"] else "LL"),
                "kind": kind,
                "price": current["price"],
                "confirmation_time": current["confirmed_at"],
            })
    range_high = max(float(row.get("high") or row.get("close")) for row in data[-20:])
    range_low = min(float(row.get("low") or row.get("close")) for row in data[-20:])
    latest = closes[-1]
    bos = "bullish" if latest > range_high * 1.001 else "bearish" if latest < range_low * 0.999 else None
    prior_range_high = max(float(row.get("high") or row.get("close")) for row in data[-21:-1])
    prior_range_low = min(float(row.get("low") or row.get("close")) for row in data[-21:-1])
    failed_breakout = bool((float(data[-1].get("high") or latest) > prior_range_high and latest < prior_range_high) or (float(data[-1].get("low") or latest) < prior_range_low and latest > prior_range_low))
    sweep_reclaim = failed_breakout
    trend = "bullish" if labels and labels[-1]["label"] in {"HH", "HL"} else "bearish" if labels and labels[-1]["label"] in {"LH", "LL"} else "range"
    return {
        "state": trend,
        "swings": pivots[-20:],
        "labels": labels[-20:],
        "bos": bos,
        "choch_proxy": "bullish" if failed_breakout and latest > prior_range_low else "bearish" if failed_breakout else None,
        "range_high": range_high,
        "range_low": range_low,
        "range_touch_count": sum(1 for row in data[-50:] if abs(float(row.get("high") or latest) - range_high) <= max(atr or 0, latest * 0.002) or abs(float(row.get("low") or latest) - range_low) <= max(atr or 0, latest * 0.002)),
        "failed_breakout": failed_breakout,
        "sweep_reclaim": sweep_reclaim,
        "breakout_hold": bool(bos),
        "retest": bool(bos and abs(latest - (range_high if bos == "bullish" else range_low)) <= max(atr or 0, latest * 0.003)),
        "pullback_depth_atr": None,
        "pivot_confirmation_time": pivots[-1]["confirmed_at"] if pivots else None,
    }


def analyze_horizons(
    candles_by_timeframe: dict[str, Iterable[Any]],
    *,
    minimum_samples: int = 30,
) -> dict[str, dict[str, Any]]:
    mapping = {
        "ultra_short": ("15m", "5m", "1m"),
        "short": ("1h", "15m", "5m"),
        "medium": ("1d", "4h", "1h"),
        "long": ("1w", "1d", "4h"),
    }
    result = {}
    for horizon, (context_tf, setup_tf, trigger_tf) in mapping.items():
        trigger_rows = _rows(candles_by_timeframe.get(trigger_tf, []), closed_only=True)
        setup_rows = _rows(candles_by_timeframe.get(setup_tf, []), closed_only=True)
        context_rows = _rows(candles_by_timeframe.get(context_tf, []), closed_only=True)
        selected = context_rows or setup_rows or trigger_rows
        technical = closed_candle_features(selected, minimum_samples=minimum_samples)
        structure = technical.get("structure") or adaptive_market_structure(selected, atr=None)
        readiness = all(len(_rows(candles_by_timeframe.get(tf, []), closed_only=True)) >= minimum_samples for tf in (context_tf, setup_tf, trigger_tf))
        regime = "insufficient_data"
        if readiness:
            if technical.get("expansion") and structure.get("failed_breakout"):
                regime = "failed_breakout"
            elif technical.get("expansion"):
                regime = "breakout_transition"
            elif technical.get("compression"):
                regime = "compression"
            elif technical.get("trend_state") in {"bullish", "bearish"}:
                regime = "trend_up" if technical["trend_state"] == "bullish" else "trend_down"
            else:
                regime = "range"
        bias = "long" if technical.get("trend_state") == "bullish" else "short" if technical.get("trend_state") == "bearish" else "neutral"
        result[horizon] = {
            "horizon": horizon,
            "bias": bias,
            "regime": regime,
            "structure": structure,
            "trend_strength": technical.get("adx_14"),
            "momentum_state": technical.get("rsi_14_closed"),
            "volatility_state": technical.get("volatility_20"),
            "support": structure.get("range_low"),
            "resistance": structure.get("range_high"),
            "current_location": technical.get("donchian_position_20"),
            "continuation_readiness": bool(readiness and bias in {"long", "short"} and regime in {"trend_up", "trend_down", "breakout_transition"}),
            "countertrend_readiness": bool(readiness and regime in {"range", "failed_breakout", "exhaustion"}),
            "invalidation": "structure_break_or_expiry",
            "analysis_readiness": readiness,
            "context_timeframe": context_tf,
            "setup_timeframe": setup_tf,
            "trigger_timeframe": trigger_tf,
            "closed_counts": {
                context_tf: len(context_rows),
                setup_tf: len(setup_rows),
                trigger_tf: len(trigger_rows),
            },
            "technical": technical,
        }
    return result
