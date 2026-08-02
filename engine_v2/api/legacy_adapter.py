from __future__ import annotations

from typing import Any


def to_legacy_payload(snapshot: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    return {"engine": "v2", "warning": None, "market_data": snapshot.get("facts", []), "technical": snapshot.get("computed_features", {}).get("technical", {}), "decision_support": decision, "opportunities": snapshot.get("ranked_candidates", []), "data_quality": snapshot.get("data_quality", {}), "factor_state": snapshot.get("factor_state", {}), "events": snapshot.get("event_state", []), "unsupported_data": snapshot.get("unsupported_data", [])}


def explicit_legacy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "engine": "legacy", "warning": "explicit_legacy_fallback"}
