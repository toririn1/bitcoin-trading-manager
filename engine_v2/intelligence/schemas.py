from __future__ import annotations

from typing import Any


REQUIRED_EXPLANATION_KEYS = ("summary", "facts_used", "inferences", "estimates", "conflicts", "scenarios", "invalidation_conditions", "missing_data", "warnings")


def empty_explanation() -> dict[str, Any]:
    return {key: [] if key != "summary" else "" for key in REQUIRED_EXPLANATION_KEYS}


def validate_explanation(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = [key for key in REQUIRED_EXPLANATION_KEYS if key not in payload]
    for key in REQUIRED_EXPLANATION_KEYS[1:]:
        if key in payload and not isinstance(payload[key], list):
            errors.append(f"{key}_not_array")
    if "summary" in payload and not isinstance(payload["summary"], str):
        errors.append("summary_not_string")
    return not errors, errors
