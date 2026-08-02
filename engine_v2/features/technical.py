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
        for name in ("return_1", "return_4", "return_24", "rsi_14_closed", "ema_20_closed", "ema_50_closed", "atr_14_pct_closed", "volatility_20"):
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
    output["quality"] = "ok"
    return output
