from __future__ import annotations

from typing import Any

from engine_v2.features.factors import portfolio_concentration, product_factor_exposure


def portfolio_overlay(positions: list[dict[str, Any]], candidate: dict[str, Any], *, max_factor_exposure: float = 2.5, max_correlated_positions: int = 2) -> dict[str, Any]:
    current = portfolio_concentration(positions, max_exposure=max_factor_exposure)
    candidate_factors = product_factor_exposure(str(candidate.get("underlying_id") or ""))
    breaches = list(current.get("breaches", []))
    correlated = sum(1 for position in positions if set(product_factor_exposure(str(position.get("underlying_id") or position.get("asset_id") or ""))) & set(candidate_factors))
    if correlated >= max_correlated_positions:
        breaches.append("max_correlated_positions")
    return {"factor_exposure": current.get("factor_exposure", {}), "candidate_factor_exposure": candidate_factors, "correlated_positions": correlated, "breaches": list(dict.fromkeys(breaches)), "fit": "blocked" if breaches else "ok"}
