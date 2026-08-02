"""Deterministic safeguards for the manual BTC analysis dashboard."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

SOURCE_LABELS = {
    "price_source": "Binance Spot BTCUSDT", "spot_volume_source": "Binance Spot",
    "futures_oi_source": "Bybit BTCUSDT Perpetual + Binance Futures optional",
    "funding_source": "Bybit Perpetual",
    "cvd_source": "Binance/Bybit taker buy-sell volume bucket, not footprint CVD",
    "account_source": "Gate.io read-only", "fee_source": "Gate.io futures transaction history",
    "rebate_source": "Gate.io affiliate commission rebate history",
}
STALE_LIMIT_SECONDS = {"price": 120, "candles": 120, "derivatives": 1800, "macro": 14400, "account": 60}

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def freshness(value: Any, kind: str, now: datetime | None = None, timestamp: str | None = None) -> dict:
    current = now or utc_now()
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if timestamp else current
        observed = observed if observed.tzinfo else observed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError): observed = current
    age = max(0, int((current - observed).total_seconds()))
    return {"value": value, "timestamp": observed.isoformat(), "age_seconds": age, "stale": age > STALE_LIMIT_SECONDS.get(kind, 1800)}

def oi_changes(history: list[float | int | None]) -> dict:
    clean = [float(v) if v is not None else None for v in history]; latest = clean[-1] if clean else None
    return {f"{h}h": ((latest - clean[-(h + 1)]) / clean[-(h + 1)] * 100 if len(clean) > h and latest is not None and clean[-(h + 1)] not in (None, 0) else None) for h in (1, 4, 24)}

def price_oi_interpretation(price_change_pct: float | None, oi_change_pct: float | None) -> str:
    if price_change_pct is None or oi_change_pct is None: return "insufficient_data"
    if price_change_pct >= 0 and oi_change_pct >= 0: return "price_up_oi_up_new_positions_trend_continuation_possible"
    if price_change_pct >= 0: return "price_up_oi_down_short_cover_or_position_cleanup_rally"
    if oi_change_pct >= 0: return "price_down_oi_up_new_short_pressure_possible"
    return "price_down_oi_down_long_liquidation_deleveraging_may_be_settling"

def taker_delta_features(taker_history: list[dict], price_history: list[float] | None = None) -> dict:
    deltas = [float(x.get("buy", 0) or 0) - float(x.get("sell", 0) or 0) for x in (taker_history or [])]
    out: dict[str, Any] = {f"taker_delta_{h}h": sum(deltas[-h:]) if deltas else None for h in (1, 4, 24)}
    prices = [float(x) for x in (price_history or []) if x is not None]
    for h in (1, 4, 24): out[f"price_delta_{h}h"] = ((prices[-1] - prices[-(h + 1)]) / prices[-(h + 1)] * 100 if len(prices) > h and prices[-(h + 1)] else None)
    p, d = out["price_delta_4h"], out["taker_delta_4h"]
    out["divergence_label"] = ("price_up_taker_delta_down_short_cover_thin_book_or_sell_absorption" if p is not None and d is not None and p > 0 and d < 0 else "price_down_taker_delta_up_buy_absorption_or_downside_slowing" if p is not None and d is not None and p < 0 and d > 0 else "no_material_divergence" if p is not None and d is not None else "insufficient_data")
    return out

def rebate_metrics(futures_fee_paid: float, rebate_received: float, equity: float | None, rebate_rate: float | None = None, realized_pnl: float = 0.0) -> dict:
    rate = min(1.0, max(0.0, rebate_rate if rebate_rate is not None else float(os.getenv("GATE_REBATE_RATE", "0.70"))))
    fee, received = abs(float(futures_fee_paid or 0)), max(0.0, float(rebate_received or 0)); expected = fee * rate
    return {"today_realized_pnl": float(realized_pnl or 0), "today_fee_paid": fee, "today_rebate_received": received, "expected_rebate_pending": max(0.0, expected - received), "today_estimated_pnl_after_rebate": float(realized_pnl or 0) - fee + expected, "fee_to_equity_ratio": fee / float(equity) if equity and equity > 0 else None, "overtrading_fee_warning": bool(equity and fee / equity > .05), "rebate_rate": rate}

def execution_permission(account: dict | None) -> dict:
    a = account or {}; equity = float(a.get("account_equity") or a.get("total_assets") or 0); trades = int(a.get("recent_2h_trade_count") or 0); recent_fee = abs(float(a.get("recent_2h_fee_paid") or 0)); fee = abs(float(a.get("today_fee_paid") or 0)); leverage = float(a.get("max_recent_leverage") or 0); flips = int(a.get("recent_flip_count") or 0)
    checks = [(trades >= 8, "최근 2시간 과다 거래"), (bool(equity and fee / equity > .05), "오늘 수수료/자산 5% 초과"), (bool(equity and recent_fee / equity > .03), "최근 2시간 수수료/자산 3% 초과"), (leverage >= 30, "30x 이상 고배율 거래 감지"), (bool(a.get("loss_then_opposite_entry")), "손실 직후 반대 포지션 진입"), (flips >= 3, "롱/숏 플립 3회 이상"), (bool(a.get("recovery_trade_attempt")), "목표 복구 후 추가 진입 시도")]
    reasons = [reason for hit, reason in checks if hit]; permission = "blocked" if reasons else ("reduce_size_only" if trades >= 4 else "allow")
    return {"execution_permission": permission, "reasons": reasons, "new_entry_prohibited": permission == "blocked"}
