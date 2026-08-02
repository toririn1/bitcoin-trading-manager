from __future__ import annotations

import bisect
import math
from datetime import datetime, timezone
from typing import Any

from engine_v2.domain.models import parse_datetime


def dynamic_relationship(
    source: list[dict[str, Any]],
    target: list[dict[str, Any]],
    *,
    max_lag: int = 6,
    minimum_samples: int = 30,
    minimum_overlap_ratio: float = 0.6,
    timeframe: str = "15m",
    tolerance_seconds: int | None = None,
    session_filter: str = "regular",
) -> dict[str, Any]:
    source_rows = _normalize_rows(source, timeframe=timeframe)
    target_rows = _normalize_rows(target, timeframe=timeframe)
    pairs, alignment = _align(
        source_rows,
        target_rows,
        tolerance_seconds=tolerance_seconds or _timeframe_seconds(timeframe) // 2,
        session_filter=session_filter,
    )
    overlap = len(pairs) / max(
        int(alignment.get("source_count") or len(source_rows)),
        int(alignment.get("target_count") or len(target_rows)),
        1,
    )
    if len(pairs) < minimum_samples:
        return _insufficient(
            len(pairs),
            "sample_count_below_minimum",
            alignment=alignment,
            timeframe=timeframe,
            overlap=overlap,
            current_confirmation=_current_confirmation(
                source_rows, target_rows, pairs, alignment, timeframe,
            ),
        )
    source_values = [item[0] for item in pairs]
    target_values = [item[1] for item in pairs]
    corr = _corr(source_values, target_values)
    best_lag, best_score = _best_lag(source_values, target_values, max_lag)
    overlap = len(pairs) / max(
        int(alignment.get("source_count") or len(source_rows)),
        int(alignment.get("target_count") or len(target_rows)),
        1,
    )
    stability = _rolling_stability(source_values, target_values)
    usable = overlap >= minimum_overlap_ratio and stability >= 0.25 and abs(corr or 0) >= 0.1
    return {
        "rolling_corr_20": _corr(source_values[-20:], target_values[-20:]) if len(pairs) >= 20 else None,
        "rolling_corr_60": _corr(source_values[-60:], target_values[-60:]) if len(pairs) >= 60 else None,
        "rolling_corr_120": _corr(source_values[-120:], target_values[-120:]) if len(pairs) >= 120 else None,
        "ew_corr": corr,
        "rolling_beta": _beta(source_values, target_values),
        "lead_lag_best_bars": best_lag,
        "lead_lag_score": best_score,
        "residual_zscore": _residual_z(source_values, target_values),
        "relationship_stability": stability,
        "sample_count": len(pairs),
        "session_overlap_ratio": overlap,
        "usable": usable,
        "state": "confirmed_positive_coupling" if usable and (corr or 0) > 0.25 else "confirmed_negative_coupling" if usable and (corr or 0) < -0.25 else "weak_relationship" if usable else "insufficient_data",
        "alignment": alignment,
        "timeframe": timeframe,
        "historical_usable": usable,
        "current_confirmation": _current_confirmation(source_rows, target_rows, pairs, alignment, timeframe),
    }


def _normalize_rows(rows: list[dict[str, Any]], *, timeframe: str) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        value = row.get("return")
        if value is None or row.get("timestamp") is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        timestamp = parse_datetime(str(row.get("timestamp")))
        if timestamp is None and isinstance(row.get("timestamp"), datetime):
            timestamp = row["timestamp"].astimezone(timezone.utc)
        if timestamp is None:
            # Legacy fixture keys are intentionally retained for deterministic tests.
            output.append({"legacy_key": str(row.get("timestamp")), "return": value, "session": row.get("session")})
            continue
        output.append({
            "timestamp": _floor(timestamp, timeframe),
            "return": value,
            "session": row.get("session"),
        })
    dedup: dict[Any, dict[str, Any]] = {}
    for row in output:
        key = row.get("timestamp") or ("legacy", row.get("legacy_key"))
        dedup[key] = row
    return [dedup[key] for key in sorted(dedup, key=lambda value: str(value))]


def _align(
    source: list[dict[str, Any]],
    target: list[dict[str, Any]],
    *,
    tolerance_seconds: int,
    session_filter: str,
) -> tuple[list[tuple[float, float]], dict[str, Any]]:
    source_regular = [row for row in source if row.get("session") == session_filter]
    target_regular = [row for row in target if row.get("session") == session_filter]
    use_regular = bool(target_regular)
    usable_source = source_regular if use_regular else source
    usable_target = target_regular if use_regular else target
    numeric_source = [row for row in usable_source if isinstance(row.get("timestamp"), datetime)]
    numeric_target = [row for row in usable_target if isinstance(row.get("timestamp"), datetime)]
    if not numeric_source or not numeric_target:
        s = {str(row.get("legacy_key")): row["return"] for row in usable_source if row.get("legacy_key") is not None}
        t = {str(row.get("legacy_key")): row["return"] for row in usable_target if row.get("legacy_key") is not None}
        keys = sorted(set(s) & set(t))
        return (
            [(float(s[key]), float(t[key])) for key in keys],
            {
                "method": "exact_legacy",
                "matched": len(keys),
                "source_count": len(usable_source),
                "target_count": len(usable_target),
                "tolerance_seconds": tolerance_seconds,
                "session_filter": session_filter if use_regular else None,
            },
        )
    target_times = [row["timestamp"] for row in numeric_target]
    pairs = []
    matched_times = []
    matched = 0
    for row in numeric_source:
        index = bisect.bisect_left(target_times, row["timestamp"])
        choices = []
        if index < len(target_times):
            choices.append(index)
        if index:
            choices.append(index - 1)
        if not choices:
            continue
        best = min(choices, key=lambda item: abs((target_times[item] - row["timestamp"]).total_seconds()))
        distance = abs((target_times[best] - row["timestamp"]).total_seconds())
        if distance <= tolerance_seconds:
            pairs.append((float(row["return"]), float(numeric_target[best]["return"])))
            matched_times.append(max(row["timestamp"], numeric_target[best]["timestamp"]))
            matched += 1
    return (
        pairs,
        {
            "method": "utc_floor_nearest",
            "matched": matched,
            "source_count": len(usable_source),
            "target_count": len(usable_target),
            "latest_matched_time": (
                max(matched_times).isoformat().replace("+00:00", "Z")
                if matched_times else None
            ),
            "tolerance_seconds": tolerance_seconds,
            "session_filter": session_filter if use_regular else None,
        },
    )


def _floor(value: datetime, timeframe: str) -> datetime:
    seconds = _timeframe_seconds(timeframe)
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def _timeframe_seconds(value: str) -> int:
    return {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}.get(value, 900)


def _current_confirmation(source, target, pairs, alignment, timeframe):
    if not pairs or not source or not target:
        return {"status": "unavailable"}
    latest = parse_datetime(alignment.get("latest_matched_time"))
    if latest is None:
        return {"status": "historical_only"}
    age = (datetime.now(timezone.utc) - latest).total_seconds()
    return {
        "status": "confirmed" if age <= _timeframe_seconds(timeframe) * 2 else "delayed",
        "latest_aligned_time": latest.isoformat().replace("+00:00", "Z"),
        "age_seconds": max(0.0, age),
    }


def _corr(x, y):
    if len(x) < 2 or len(x) != len(y):
        return None
    mx, my = sum(x) / len(x), sum(y) / len(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denom = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
    return numerator / denom if denom else 0.0


def _beta(x, y):
    if not x:
        return None
    mx, my = sum(x) / len(x), sum(y) / len(y)
    variance = sum((a - mx) ** 2 for a in x)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / variance if variance else None


def _best_lag(x, y, max_lag):
    candidates = []
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            score = _corr(x[-lag:], y[:lag])
        elif lag > 0:
            score = _corr(x[:-lag], y[lag:])
        else:
            score = _corr(x, y)
        candidates.append((abs(score or 0), lag, score))
    _, lag, score = max(candidates, default=(0, 0, None))
    return lag, score


def _rolling_stability(x, y):
    if len(x) < 20:
        return 0.0
    chunks = [(_corr(x[i:i + 20], y[i:i + 20]) or 0) for i in range(0, len(x) - 19, 20)]
    return max(0.0, 1 - (max(chunks) - min(chunks))) if chunks else 0.0


def _residual_z(x, y):
    beta = _beta(x, y)
    if beta is None:
        return None
    residuals = [b - beta * a for a, b in zip(x, y)]
    if len(residuals) < 2:
        return None
    mean = sum(residuals) / len(residuals)
    std = (sum((v - mean) ** 2 for v in residuals) / (len(residuals) - 1)) ** 0.5
    return (residuals[-1] - mean) / std if std else 0.0


def _insufficient(
    count,
    reason,
    *,
    alignment=None,
    timeframe="15m",
    overlap=0.0,
    current_confirmation=None,
):
    return {
        "rolling_corr_20": None,
        "rolling_corr_60": None,
        "rolling_corr_120": None,
        "ew_corr": None,
        "rolling_beta": None,
        "lead_lag_best_bars": None,
        "lead_lag_score": None,
        "residual_zscore": None,
        "relationship_stability": 0.0,
        "sample_count": count,
        "session_overlap_ratio": overlap,
        "usable": False,
        "state": "insufficient_data",
        "reason": reason,
        "alignment": alignment or {},
        "timeframe": timeframe,
        "historical_usable": False,
        "current_confirmation": current_confirmation or {"status": "unavailable"},
    }


def insufficient_relationship(count: int, reason: str) -> dict[str, Any]:
    return _insufficient(count, reason)
