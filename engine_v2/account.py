from __future__ import annotations

import os
from typing import Any


def read_account_overlay() -> dict[str, Any]:
    """Read the existing account_context through its read-only entry point.

    No account call is attempted when credentials/configuration are absent.
    The returned payload is safe for decision snapshots and contains no secrets.
    """
    try:
        import config
        provider = str(getattr(config, "ACCOUNT_PROVIDER", "none") or "none")
        enabled = bool(getattr(config, "ACCOUNT_FEATURES_ENABLED", False))
        configured = bool(
            (provider == "gateio" and getattr(config, "GATE_API_KEY", "") and getattr(config, "GATE_API_SECRET", ""))
            or (provider != "gateio" and getattr(config, "BINANCE_API_KEY", "") and getattr(config, "BINANCE_SECRET_KEY", ""))
        )
    except Exception as exc:
        return {"status": "data_unavailable", "reason": f"account_config:{type(exc).__name__}", "positions": None}

    if os.getenv("V2_ACCOUNT_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
        return {"status": "disabled", "reason": "V2_ACCOUNT_ENABLED=false", "positions": None}
    if not enabled or provider in {"none", ""}:
        return {"status": "data_unavailable", "reason": "account_features_disabled", "positions": None}
    if not configured:
        return {"status": "data_unavailable", "reason": "read_only_credentials_missing", "positions": None}

    try:
        from account_context import fetch_account_context
        context = fetch_account_context()
    except Exception as exc:
        return {"status": "provider_error", "reason": f"account_fetch:{type(exc).__name__}", "positions": None}

    positions_raw = context.get("positions")
    if positions_raw is None:
        positions_raw = context.get("open_positions")
    positions = [_normalize_position(row) for row in positions_raw] if isinstance(positions_raw, list) else None
    positions = [row for row in positions if row.get("notional") is not None or row.get("symbol")] if positions is not None else None
    equity = _number(context.get("account_equity") or context.get("total_assets") or context.get("wallet_balance"))
    available = _number(context.get("available_balance") or context.get("available_margin"))
    errors = [context.get(key) for key in ("balance_error", "account_error", "position_error") if context.get(key)]
    status = "ok" if equity is not None and positions is not None and not errors else "partial" if equity is not None or positions is not None else "provider_error"
    return {
        "status": status,
        "provider": provider,
        "equity": equity,
        "available_margin": available,
        "unrealized_pnl": _number(context.get("unrealized_pnl") or context.get("open_position_upnl")),
        "positions": positions,
        "position_count": len(positions) if positions is not None else None,
        "errors": errors,
        "execution_permission": "data_unavailable" if status != "ok" else "read_only_analysis_only",
    }


def _normalize_position(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or row.get("contract") or row.get("instrument") or "").upper()
    side = str(row.get("side") or row.get("positionSide") or "").lower()
    if side in {"long", "buy", "cross_long", "1"}:
        direction = "long"
    elif side in {"short", "sell", "cross_short", "-1"}:
        direction = "short"
    else:
        direction = side or "unknown"
    return {
        "symbol": symbol,
        "asset_id": _asset_id(symbol),
        "side": direction,
        "notional": _number(row.get("notional") or row.get("position_notional") or row.get("positionAmt") or row.get("size")),
        "entry_price": _number(row.get("entry_price") or row.get("entryPrice")),
        "mark_price": _number(row.get("mark_price") or row.get("markPrice")),
        "liquidation_price": _number(row.get("liquidation_price") or row.get("liquidationPrice")),
        "margin_mode": row.get("margin_mode") or row.get("marginMode"),
        "leverage": _number(row.get("leverage")),
        "unrealized_pnl": _number(row.get("unrealized_pnl") or row.get("unrealizedProfit")),
        "venue": row.get("venue") or row.get("provider"),
    }


def _asset_id(symbol: str) -> str:
    if "BTC" in symbol:
        return "BTC"
    if "SOXL" in symbol:
        return "SOXL"
    if "660" in symbol or "SKHYNIX" in symbol or "SK_HYNIX" in symbol:
        return "SK_HYNIX_KRX"
    if "5930" in symbol or "SAMSUNG" in symbol:
        return "SAMSUNG_KRX"
    return symbol


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None
