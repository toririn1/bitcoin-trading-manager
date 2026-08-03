from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from engine_v2.domain.models import parse_datetime
from engine_v2.storage.point_in_time import filter_available


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    funding_bps_per_bar: float = 0.0
    timeout_bars: int = 96
    ambiguity_policy: str = "conservative"


def replay_decision(
    decision: dict[str, Any],
    observations: Iterable[dict[str, Any]],
    *,
    decision_time: datetime | None = None,
    candles: Iterable[dict[str, Any]] | None = None,
    config: ReplayConfig | None = None,
) -> dict[str, Any]:
    decision_time = decision_time or datetime.now(timezone.utc)
    rows = list(observations)
    usable = filter_available(rows, decision_time)
    result: dict[str, Any] = {
        "snapshot_id": decision.get("snapshot_id"),
        "decision_time": decision_time.isoformat().replace("+00:00", "Z"),
        "observation_count": len(usable),
        "future_observation_excluded": len(rows) - len(usable),
        "candidates": decision.get("ranked_candidates", decision.get("candidate_rank", [])),
        "point_in_time": True,
    }
    if candles is not None:
        candidates = result["candidates"] if isinstance(result["candidates"], list) else []
        result["trade_replays"] = [
            replay_candidate(candidate, candles, config=config)
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("direction") != "no_trade"
        ]
    return result


def replay_candidate(
    candidate: dict[str, Any],
    candles: Iterable[dict[str, Any]],
    *,
    config: ReplayConfig | None = None,
) -> dict[str, Any]:
    cfg = config or ReplayConfig()
    direction = str(candidate.get("direction") or "").lower()
    if direction not in {"long", "short"}:
        return {"status": "not_applicable", "reason": "not_directional_candidate"}

    rows = sorted(
        (_normalize_candle(row) for row in candles),
        key=lambda row: row["time"] or datetime.min.replace(tzinfo=timezone.utc),
    )
    rows = [row for row in rows if row["close"] is not None]
    if not rows:
        return _result(candidate, "not_filled", "candles_missing")

    plan = candidate.get("entry") if isinstance(candidate.get("entry"), dict) else {}
    entry_plan = str(candidate.get("entry_plan") or plan.get("plan") or "").lower()
    trigger = _number(
        candidate.get("trigger_price")
        or candidate.get("entry_price")
        or plan.get("trigger_price")
        or plan.get("entry_price")
    )
    if trigger is None and entry_plan in {"market", "market_entry"}:
        trigger = rows[0]["open"] or rows[0]["close"]
    if trigger is None:
        return _result(candidate, "not_filled", "trigger_missing")

    entry_index = None
    for index, row in enumerate(rows):
        touched = (
            row["low"] is not None and row["low"] <= trigger
            if direction == "long"
            else row["high"] is not None and row["high"] >= trigger
        )
        if touched:
            entry_index = index
            break
    if entry_index is None:
        if candidate.get("trigger_fired") is True:
            # The trigger was already confirmed in the source snapshot. Future
            # candles therefore begin after the entry; do not mislabel this as
            # an untriggered candidate and discard its gross outcome.
            entry_index = 0
        else:
            return _result(candidate, "not_triggered", "trigger_not_touched", trigger_price=trigger)

    sign = 1 if direction == "long" else -1
    fill_price = trigger * (1 + sign * cfg.slippage_bps / 10000)
    stop = _number(
        candidate.get("stop_price")
        or candidate.get("stop_loss")
        or candidate.get("invalidation_price")
        or plan.get("stop_price")
    )
    target = _number(
        candidate.get("target_price")
        or candidate.get("take_profit")
        or plan.get("target_price")
    )
    exit_index = min(len(rows) - 1, entry_index + max(1, cfg.timeout_bars))
    exit_reason = "timeout"
    exit_price = rows[exit_index]["close"]
    ambiguous = False
    mfe_bps = 0.0
    mae_bps = 0.0

    for index in range(entry_index, exit_index + 1):
        row = rows[index]
        high = row["high"] if row["high"] is not None else row["close"]
        low = row["low"] if row["low"] is not None else row["close"]
        if high is not None:
            mfe_bps = max(mfe_bps, sign * (high - fill_price) / fill_price * 10000)
        if low is not None:
            mae_bps = min(mae_bps, sign * (low - fill_price) / fill_price * 10000)
        stop_hit = stop is not None and (
            low <= stop if direction == "long" else high >= stop
        )
        target_hit = target is not None and (
            high >= target if direction == "long" else low <= target
        )
        if not stop_hit and not target_hit:
            continue
        ambiguous = stop_hit and target_hit
        if ambiguous and cfg.ambiguity_policy in {"optimistic", "target_first"}:
            exit_reason, exit_price = "target", target
        elif stop_hit:
            exit_reason, exit_price = "stop", stop
        else:
            exit_reason, exit_price = "target", target
        exit_index = index
        break

    if exit_price is None:
        return _result(candidate, "not_filled", "exit_price_missing", trigger_price=trigger)
    exit_price = exit_price * (1 - sign * cfg.slippage_bps / 10000)
    gross_bps = sign * (exit_price - fill_price) / fill_price * 10000
    bars_held = max(1, exit_index - entry_index + 1)
    fees_bps = abs(cfg.fee_bps) * 2
    funding_bps = abs(cfg.funding_bps_per_bar) * bars_held
    net_bps = gross_bps - fees_bps - funding_bps
    trigger_time = rows[entry_index]["time"]
    exit_time = rows[exit_index]["time"]
    holding = (exit_time - trigger_time).total_seconds() if trigger_time and exit_time else None
    return {
        "status": "filled",
        "reason": None,
        "product_id": candidate.get("product_id"),
        "direction": direction,
        "triggered": True,
        "trigger_time": _iso(trigger_time),
        "fill_price": fill_price,
        "exit_time": _iso(exit_time),
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "fees_bps": fees_bps,
        "funding_bps": funding_bps,
        "slippage_bps": abs(cfg.slippage_bps) * 2,
        "gross_return_bps": gross_bps,
        "net_return_bps": net_bps,
        "net_return": net_bps / 10000,
        "mfe_bps": mfe_bps,
        "mae_bps": mae_bps,
        "holding_bars": bars_held,
        "holding_time": holding,
        "ambiguous_bar": ambiguous,
        "ambiguity_policy": cfg.ambiguity_policy,
        "reason_codes": ["ambiguous_bar"] if ambiguous else [],
        "failure_codes": [],
    }


def outcome_record(decision: dict[str, Any], fill: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": decision.get("snapshot_id"),
        "decision_time": decision.get("generated_at"),
        "product_id": result.get("product_id"),
        "direction": result.get("direction"),
        "entry_plan": result.get("entry_plan"),
        "triggered": fill.get("status") in {"filled", "partial"},
        "trigger_time": fill.get("trigger_time"),
        "fill_price": fill.get("fill_price"),
        "fees": result.get("fees") or result.get("fees_bps"),
        "slippage": result.get("slippage") or result.get("slippage_bps"),
        "funding": result.get("funding") or result.get("funding_bps"),
        "MFE": result.get("mfe") or result.get("mfe_bps"),
        "MAE": result.get("mae") or result.get("mae_bps"),
        "exit_reason": result.get("exit_reason"),
        "net_return": result.get("net_return") or result.get("net_return_bps"),
        "holding_time": result.get("holding_time"),
        "regime": result.get("regime"),
        "reason_codes": result.get("reason_codes", []),
        "failure_codes": result.get("failure_codes", []),
    }


def _normalize_candle(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
    return {
        "time": parse_datetime(payload.get("close_time") or payload.get("open_time") or row.get("source_event_time")),
        "open": _number(payload.get("open")),
        "high": _number(payload.get("high")),
        "low": _number(payload.get("low")),
        "close": _number(payload.get("close")),
    }


def _result(candidate: dict[str, Any], status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "product_id": candidate.get("product_id"),
        "direction": candidate.get("direction"),
        "triggered": False,
        "failure_codes": [reason],
        **extra,
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None
