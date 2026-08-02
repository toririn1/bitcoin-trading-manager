from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from time_utils import format_kst


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def gate_total_assets_equity(entry: dict) -> float | None:
    """Gate Assets Analysis/Total Assets 기준 equity 후보."""
    total_assets = _as_float(entry.get("total_assets"))
    if total_assets is not None and total_assets > 10:
        return total_assets
    return None


def normalized_performance_snapshots(entries: list[dict], provider: str) -> tuple[list[dict], dict]:
    snapshots: list[dict] = []
    diagnostics = {"legacy_excluded_count": 0, "polluted_excluded_count": 0}
    for entry in entries:
        if entry.get("provider") == "gateio" or provider == "gateio":
            equity_num = _as_float(entry.get("account_equity"))
            gate_equity = gate_total_assets_equity(entry)
            if gate_equity is None:
                if equity_num is not None and equity_num <= 10:
                    diagnostics["polluted_excluded_count"] += 1
                else:
                    diagnostics["legacy_excluded_count"] += 1
                continue
            entry = dict(entry)
            entry["account_equity"] = gate_equity
            entry["performance_equity_source"] = "gate_total_assets"
        snapshots.append(entry)
    return snapshots, diagnostics


def build_daily_performance(snapshots: list[dict], provider: str) -> list[dict]:
    daily_map: dict[str, list[dict]] = defaultdict(list)
    for snap in snapshots:
        try:
            dt = datetime.fromtimestamp(float(snap["observed_ts"]), tz=timezone.utc)
            day_key = dt.strftime("%Y-%m-%d") if provider == "gateio" else format_kst(dt, "%Y-%m-%d")
            daily_map[day_key].append(snap)
        except Exception:
            continue

    daily = []
    prev_close_equity = None
    for day_key in sorted(daily_map.keys()):
        day_snaps = daily_map[day_key]
        last = day_snaps[-1]
        first = day_snaps[0]
        day_prev_close_equity = prev_close_equity
        pnl_vals = [s.get("today_total_pnl") for s in day_snaps if s.get("today_total_pnl") is not None]
        eq_vals = [s.get("account_equity") for s in day_snaps if s.get("account_equity") is not None]

        pnl = last.get("today_total_pnl")
        pnl_source = "today_total_pnl" if pnl is not None else None
        partial = False
        if provider == "gateio" and eq_vals:
            day_close = eq_vals[-1]
            if day_prev_close_equity is not None:
                pnl = day_close - day_prev_close_equity
                pnl_source = "gate_total_assets_prev_close"
            elif len(eq_vals) >= 2:
                pnl = eq_vals[-1] - eq_vals[0]
                pnl_source = "gate_total_assets_intraday"
                partial = True
        elif pnl is None and len(eq_vals) >= 2:
            pnl = eq_vals[-1] - eq_vals[0]
            pnl_source = "equity_intraday"

        if eq_vals:
            prev_close_equity = eq_vals[-1]

        pnl_pct = last.get("today_pnl_pct")
        if provider == "gateio" and pnl is not None:
            base = day_prev_close_equity if pnl_source == "gate_total_assets_prev_close" else first.get("account_equity")
            try:
                base_num = float(base)
                pnl_pct = pnl / base_num * 100 if base_num > 0 else None
            except (TypeError, ValueError, ZeroDivisionError):
                pnl_pct = None

        daily.append({
            "date": day_key,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "pnl_source": pnl_source,
            "partial": partial,
            "equity_start": first.get("account_equity"),
            "equity_end": last.get("account_equity"),
            "equity_high": max(eq_vals) if eq_vals else None,
            "equity_low": min(eq_vals) if eq_vals else None,
            "pnl_high": max(pnl_vals) if pnl_vals else None,
            "pnl_low": min(pnl_vals) if pnl_vals else None,
            "risk_status": last.get("risk_status"),
            "snap_count": len(day_snaps),
        })
    return daily


def build_asset_basis(snapshots: list[dict]) -> dict:
    if not snapshots:
        return {
            "status": "no_data",
            "note": "총자산 기준 비교는 정상 자산 스냅샷이 새로 쌓인 뒤 표시됩니다.",
        }
    first = snapshots[0]
    last = snapshots[-1]
    start_eq = _as_float(first.get("account_equity"))
    end_eq = _as_float(last.get("account_equity"))
    if start_eq is None or end_eq is None:
        return {"status": "no_data", "note": "정상 Total Assets 스냅샷이 부족합니다."}
    return {
        "status": "ok",
        "equity_start": round(start_eq, 6),
        "equity_end": round(end_eq, 6),
        "equity_change": round(end_eq - start_eq, 6),
        "observed_from": first.get("observed_label"),
        "observed_to": last.get("observed_label"),
        "sample_count": len(snapshots),
        "source": "gate_total_assets_history",
        "note": "Gate Total Assets/Assets Analysis에 가까운 미실현 포함 자산 변화 기준입니다.",
    }
