from __future__ import annotations

from typing import Any

from .schemas import empty_explanation, validate_explanation


def explain_snapshot(snapshot: dict[str, Any], *, llm_text: str | None = None) -> dict[str, Any]:
    """Create an explanation without allowing an LLM to change deterministic facts."""
    result = empty_explanation()
    candidates = snapshot.get("ranked_candidates") or []
    quality = snapshot.get("data_quality") or {}
    result["summary"] = _summary(candidates, quality)
    result["facts_used"] = _facts(snapshot)
    result["missing_data"] = list(snapshot.get("unsupported_data") or quality.get("missing") or [])
    result["warnings"] = ["LLM explanation is optional; deterministic score and permission are authoritative."]
    if llm_text:
        result["inferences"].append({"type": "inference", "text": llm_text, "source": "llm", "deterministic_values_unchanged": True})
    ok, errors = validate_explanation(result)
    if not ok:
        result["warnings"].extend(errors)
    return result


def _summary(candidates: list[dict[str, Any]], quality: dict[str, Any]) -> str:
    if not candidates:
        return "현재 사용 가능한 후보가 없습니다."
    best = candidates[0]
    if best.get("direction") == "no_trade" or not best.get("valid"):
        return "데이터 품질·비용·상품 guard를 통과한 진입 후보가 없어 no_trade가 우선입니다."
    return f"현재 최고 후보는 {best.get('product_id')} {best.get('direction')}이며 net edge와 데이터 품질 gate를 함께 확인해야 합니다."


def _facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    quality = snapshot.get("data_quality") or {}
    return [{"type": "computed", "name": "data_quality_score", "value": quality.get("score")}, {"type": "computed", "name": "candidate_count", "value": len(snapshot.get("ranked_candidates") or [])}]
