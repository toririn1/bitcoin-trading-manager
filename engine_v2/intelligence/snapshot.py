from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def build_snapshot(*, registry: dict[str, Any], observations: list[dict[str, Any]], features: dict[str, Any], data_quality: dict[str, Any], factor_state: dict[str, Any], event_state: list[dict[str, Any]], ranked_candidates: list[dict[str, Any]], portfolio_constraints: dict[str, Any], unsupported_data: list[str] | None = None) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    body = {"registry": registry, "facts": observations, "computed_features": features, "data_quality": data_quality, "factor_state": factor_state, "event_state": event_state, "ranked_candidates": ranked_candidates, "portfolio_constraints": portfolio_constraints, "unsupported_data": unsupported_data or []}
    raw = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    snapshot_id = "v2-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
    return {"schema_version": "2.0", "snapshot_id": snapshot_id, "generated_at": generated_at.isoformat().replace("+00:00", "Z"), **body}
