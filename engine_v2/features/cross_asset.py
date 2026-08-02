from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def dynamic_relationship(source: list[dict[str, Any]], target: list[dict[str, Any]], *, max_lag: int = 6, minimum_samples: int = 30, minimum_overlap_ratio: float = 0.6) -> dict[str, Any]:
    pairs = _align(source, target)
    if len(pairs) < minimum_samples:
        return _insufficient(len(pairs), "sample_count_below_minimum")
    source_values = [item[0] for item in pairs]
    target_values = [item[1] for item in pairs]
    corr = _corr(source_values, target_values)
    best_lag, best_score = _best_lag(source_values, target_values, max_lag)
    overlap = len(pairs) / max(len(source), len(target), 1)
    stability = _rolling_stability(source_values, target_values)
    usable = overlap >= minimum_overlap_ratio and stability >= 0.25 and abs(corr or 0) >= 0.1
    return {"rolling_corr_20": _corr(source_values[-20:], target_values[-20:]) if len(pairs) >= 20 else None, "rolling_corr_60": _corr(source_values[-60:], target_values[-60:]) if len(pairs) >= 60 else None, "rolling_corr_120": _corr(source_values[-120:], target_values[-120:]) if len(pairs) >= 120 else None, "ew_corr": corr, "rolling_beta": _beta(source_values, target_values), "lead_lag_best_bars": best_lag, "lead_lag_score": best_score, "residual_zscore": _residual_z(source_values, target_values), "relationship_stability": stability, "sample_count": len(pairs), "session_overlap_ratio": overlap, "usable": usable, "state": "confirmed_positive_coupling" if usable and (corr or 0) > 0.25 else "confirmed_negative_coupling" if usable and (corr or 0) < -0.25 else "weak_relationship" if usable else "insufficient_data"}


def _align(source, target):
    s = {str(row.get("timestamp")): row.get("return") for row in source if row.get("timestamp") is not None and row.get("return") is not None}
    t = {str(row.get("timestamp")): row.get("return") for row in target if row.get("timestamp") is not None and row.get("return") is not None}
    return [(float(s[key]), float(t[key])) for key in sorted(set(s) & set(t))]


def _corr(x, y):
    if len(x) < 2 or len(x) != len(y):
        return None
    mx, my = sum(x) / len(x), sum(y) / len(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denom = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
    return numerator / denom if denom else 0.0


def _beta(x, y):
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


def _insufficient(count, reason):
    return {"rolling_corr_20": None, "rolling_corr_60": None, "rolling_corr_120": None, "ew_corr": None, "rolling_beta": None, "lead_lag_best_bars": None, "lead_lag_score": None, "residual_zscore": None, "relationship_stability": 0.0, "sample_count": count, "session_overlap_ratio": 0.0, "usable": False, "state": "insufficient_data", "reason": reason}
