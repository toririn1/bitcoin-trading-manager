from __future__ import annotations

import re
from typing import Any


def validate_claims(explanation: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    score_values = {str(candidate.get("net_edge_bps")) for candidate in snapshot.get("ranked_candidates", [])}
    for bucket in ("summary", "inferences", "estimates", "warnings"):
        values = explanation.get(bucket)
        texts = [values] if isinstance(values, str) else [item.get("text", "") if isinstance(item, dict) else str(item) for item in values or []]
        for text in texts:
            if re.search(r"\b(?:cause|caused|유발|원인이다)\b", text, re.IGNORECASE):
                errors.append("causal_language_requires_evidence")
            if any(value and value in text for value in score_values):
                continue
    return {"valid": not errors, "errors": sorted(set(errors)), "can_change_deterministic_values": False}
