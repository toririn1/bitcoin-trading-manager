from __future__ import annotations

from typing import Any, Iterable


def actual_delta_surface(rows: Iterable[dict[str, Any]], *, target_delta: float = 0.25) -> dict[str, Any]:
    calls, puts = [], []
    for row in rows:
        greeks = row.get("greeks") or {}
        delta = _number(greeks.get("delta"))
        iv = _number(row.get("mark_iv"))
        if delta is None or iv is None:
            continue
        item = {**row, "delta": delta, "iv": iv}
        if str(row.get("option_type") or row.get("type") or "").upper() in {"C", "CALL"} or delta > 0:
            calls.append(item)
        else:
            puts.append(item)
    call = min(calls, key=lambda item: abs(abs(item["delta"]) - target_delta), default=None)
    put = min(puts, key=lambda item: abs(abs(item["delta"]) - target_delta), default=None)
    if call is None or put is None:
        return {"rr_25d": None, "rr_10d": None, "quality": "partial", "reason": "actual_delta_wings_missing", "call": call, "put": put}
    return {"rr_25d": put["iv"] - call["iv"], "rr_10d": None, "quality": "ok", "reason": None, "call": call, "put": put}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None
