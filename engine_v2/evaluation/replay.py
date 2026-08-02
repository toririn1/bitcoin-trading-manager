from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from engine_v2.storage.point_in_time import filter_available


def replay_decision(decision: dict[str, Any], observations: Iterable[dict[str, Any]], *, decision_time: datetime | None = None) -> dict[str, Any]:
    decision_time = decision_time or datetime.now(timezone.utc)
    usable = filter_available(observations, decision_time)
    return {"snapshot_id": decision.get("snapshot_id"), "decision_time": decision_time.isoformat().replace("+00:00", "Z"), "observation_count": len(usable), "future_observation_excluded": len(list(observations)) - len(usable), "candidates": decision.get("ranked_candidates", []), "point_in_time": True}


def outcome_record(decision: dict[str, Any], fill: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {"snapshot_id": decision.get("snapshot_id"), "decision_time": decision.get("generated_at"), "product_id": result.get("product_id"), "direction": result.get("direction"), "entry_plan": result.get("entry_plan"), "triggered": fill.get("status") in {"filled", "partial"}, "trigger_time": fill.get("trigger_time"), "fill_price": fill.get("fill_price"), "fees": result.get("fees"), "slippage": result.get("slippage"), "funding": result.get("funding"), "MFE": result.get("mfe"), "MAE": result.get("mae"), "exit_reason": result.get("exit_reason"), "net_return": result.get("net_return"), "holding_time": result.get("holding_time"), "regime": result.get("regime"), "reason_codes": result.get("reason_codes", []), "failure_codes": result.get("failure_codes", [])}
