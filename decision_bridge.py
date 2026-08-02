"""Decision policy shared by the LLM context, API response, and UI.

The module intentionally keeps four layers separate:

* ``market_direction``: market-only directional opinion.
* ``setup_action_verdict`` / ``setup_quality``: price-location and R:R quality.
* ``account_overlay``: account, fee, position, and data constraints.
* ``final_action``: a transparent composition of setup and account overlay.

No function in this module can place or mutate an order.
"""
from __future__ import annotations

import math
import re
from typing import Any

import config
from decision_support import SOURCE_LABELS


PERMISSION_ORDER = {
    "allow": 0,
    "reduce_size_only": 1,
    "reduced_size": 1,
    "manual_confirm_required": 2,
    "cooldown_required": 3,
    "blocked": 4,
    "hard_block": 5,
}

TRIGGER_PHRASES = (
    "지지 확인 시", "지지 확인", "돌파 후", "돌파 확인", "리테스트", "되밟기",
    "종가 돌파", "종가 상회", "종가 하회", "15m 종가", "1h 종가", "눌림 시",
    "확인 후", "재돌파", "이탈 후", "이탈 확인", "지켜주면", "안착하면",
    "회복하면", "유지하면", "실패하면", "트리거", "미완성봉 추격 금지", "추격 금지",
    "after breakout", "breakout confirmation", "close above", "close below", "retest",
    "retest hold", "support confirmation", "resistance break", "wait for trigger", "pullback",
    "do not chase", "unfinished candle", "confirmation required",
)

CHASE_PHRASES = ("미완성봉 추격 금지", "추격 금지", "do not chase", "unfinished candle")
IMMEDIATE_LONG_PHRASES = ("지금 롱 진입", "즉시 롱", "현재가 롱", "enter long now")
IMMEDIATE_SHORT_PHRASES = ("지금 숏 진입", "즉시 숏", "현재가 숏", "enter short now")

NUMBER_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,7}(?:\.\d+)?)")
ZONE_RE = re.compile(
    r"\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,7}(?:\.\d+)?)"
    r"\s*(?:~|〜|–|—|-)\s*\$?\s*"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,7}(?:\.\d+)?)"
)

FORBIDDEN_LABELS = {
    "open_new_position": "신규 진입 금지",
    "open_new_full_size": "풀사이즈 진입 금지",
    "add_to_position": "포지션 추가 금지",
    "add_to_losing_position": "손실 포지션 물타기 금지",
    "revenge_trade": "복구·감정매매 금지",
    "repeated_reentry": "반복 재진입 금지",
    "high_leverage": "고배율 진입 금지",
    "no_stop_entry": "손절 없는 진입 금지",
    "chase_entry": "추격 진입 금지",
    "short_chase": "숏 추격 금지",
    "long_chase": "롱 추격 금지",
    "reverse_position": "즉시 반전 진입 금지",
    "increase_risk": "리스크 확대 금지",
    "new_entry_when_blocked": "계좌 제한 중 신규 진입 금지",
}


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _ratio(value: float | None, equity: float | None) -> float | None:
    if value is None or equity is None or equity <= 0:
        return None
    return value / equity


def _normalize_direction(value: Any) -> str:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("상방", "bull", "매수")):
        return "상방 우위"
    if any(token in text for token in ("하방", "bear", "매도")):
        return "하방 우위"
    return "중립"


def _market_direction(market: dict) -> str:
    explicit = market.get("market_direction") or market.get("market_signal")
    if explicit:
        return _normalize_direction(explicit)
    oi = _as_float(market.get("aggregated_oi_change_4h_pct"))
    delta = _as_float(market.get("taker_delta_4h"))
    if oi is not None and oi > 0 and delta is not None and delta > 0:
        return "상방 우위"
    if oi is not None and oi > 0 and delta is not None and delta < 0:
        return "하방 우위"
    return "중립"


def build_fee_summary(account: dict | None) -> dict:
    """Return transparent gross/net fee values without blocking on missing data."""
    account = account or {}
    realized = account.get("realized_context") or {}
    summary = realized.get("summary") if isinstance(realized, dict) else None
    summary = summary if isinstance(summary, dict) else {}

    gross_raw = summary.get("fees")
    if gross_raw is None:
        gross_raw = account.get("today_fee_paid")
    gross = abs(_as_float(gross_raw) or 0.0) if gross_raw is not None else None

    received_parts = [summary.get("self_rebate"), summary.get("futures_rebates")]
    if all(value is None for value in received_parts):
        received_raw = account.get("today_rebate_received")
        received = max(0.0, _as_float(received_raw) or 0.0) if received_raw is not None else None
    else:
        received = sum(max(0.0, _as_float(value) or 0.0) for value in received_parts)

    equity = _as_float(account.get("account_equity") or account.get("total_assets"))
    rebate_rate = float(getattr(config, "GATE_REBATE_RATE", 0.70))
    expected_total = gross * rebate_rate if gross is not None else None
    pending_raw = account.get("expected_rebate_pending")
    if pending_raw is not None:
        expected_pending = max(0.0, _as_float(pending_raw) or 0.0)
    elif expected_total is not None and received is not None:
        expected_pending = max(0.0, expected_total - received)
    else:
        expected_pending = None

    net_fee = (
        max(0.0, gross - (received or 0.0) - (expected_pending or 0.0))
        if gross is not None else None
    )
    recognition = float(getattr(config, "EXPECTED_REBATE_RECOGNITION", 0.50))
    conservative_net_fee = (
        max(0.0, gross - (received or 0.0) - (expected_pending or 0.0) * recognition)
        if gross is not None else None
    )
    gross_ratio = _ratio(gross, equity)
    net_ratio = _ratio(net_fee, equity)
    conservative_ratio = _ratio(conservative_net_fee, equity)
    reduce_threshold = float(getattr(config, "FEE_TO_EQUITY_REDUCE_THRESHOLD", 0.02))

    if gross is None:
        reason = "fee history unavailable"
    elif gross == 0:
        reason = "no recent fee events found"
    elif received in (None, 0):
        reason = "rebate wallet not matched"
    else:
        reason = "fee history available"

    realized_pnl = _as_float(summary.get("realized_pnl") or account.get("today_realized_pnl"))
    estimated_after_rebate = (
        (realized_pnl or 0.0) - gross + (received or 0.0) + (expected_pending or 0.0)
        if gross is not None else None
    )
    return {
        "today_realized_pnl": realized_pnl,
        "today_fee_paid": gross,
        "today_rebate_received": received,
        "expected_rebate_pending": expected_pending,
        "today_estimated_pnl_after_rebate": estimated_after_rebate,
        "gross_fee": gross,
        "received_rebate": received,
        "expected_rebate": expected_pending,
        "net_fee": net_fee,
        "conservative_net_fee": conservative_net_fee,
        "gross_fee_to_equity_ratio": gross_ratio,
        "net_fee_to_equity_ratio": net_ratio,
        "conservative_net_fee_to_equity_ratio": conservative_ratio,
        # Legacy display field remains gross ratio; policy uses conservative net.
        "fee_to_equity_ratio": gross_ratio,
        "overtrading_fee_warning": bool(gross_ratio is not None and gross_ratio >= reduce_threshold),
        "rebate_rate": rebate_rate,
        "expected_rebate_recognition": recognition,
        "reason": reason,
    }


def _positions(account: dict) -> list[dict]:
    raw = account.get("positions")
    if raw is None:
        raw = account.get("open_positions")
    return [item for item in (raw or []) if isinstance(item, dict)]


def _position_side(position: dict) -> str:
    text = " ".join(
        str(position.get(key) or "")
        for key in ("side", "pos_side", "position_side", "mode")
    ).lower()
    if "숏" in text or "short" in text:
        return "short"
    if "롱" in text or "long" in text:
        return "long"
    size = _as_float(position.get("size") or position.get("position_amt"))
    if size is not None:
        return "long" if size > 0 else "short" if size < 0 else "flat"
    return "unknown"


def _permission_rank(permission: str) -> int:
    return PERMISSION_ORDER.get(permission, 2)


def _more_restrictive(current: str, candidate: str) -> str:
    return candidate if _permission_rank(candidate) > _permission_rank(current) else current


def build_account_overlay(account: dict | None, fee: dict, freshness_warnings: list[str]) -> dict:
    """Classify account restrictions without changing market/setup conclusions."""
    account = account or {}
    permission = "allow"
    reasons: list[str] = []
    data_integrity_error = bool(
        account.get("account_data_error")
        or account.get("balance_mismatch")
        or account.get("position_mismatch")
    )
    if data_integrity_error:
        permission = "hard_block"
        reasons.append("account_data_integrity_error")

    recent_trades = int(account.get("recent_2h_trade_count") or 0)
    leverage_values = [
        _as_float(account.get("max_recent_leverage")),
        *[_as_float(position.get("leverage")) for position in _positions(account)],
    ]
    max_leverage = max((value for value in leverage_values if value is not None), default=0.0)
    if bool(account.get("daily_loss_limit_exceeded")):
        permission = _more_restrictive(permission, "hard_block")
        reasons.append("daily_loss_limit_exceeded")
    if bool(account.get("liquidation_too_close")) or (
        (_as_float(account.get("liquidation_distance_pct")) or 999.0) < 3.0
    ):
        permission = _more_restrictive(permission, "hard_block")
        reasons.append("liquidation_too_close")
    if max_leverage >= 30:
        permission = _more_restrictive(permission, "hard_block")
        reasons.append("high_leverage_detected")
    if int(account.get("recent_flip_count") or 0) >= 3:
        permission = _more_restrictive(permission, "cooldown_required")
        reasons.append("repeated_direction_flips")
    if account.get("loss_then_opposite_entry") or account.get("recovery_trade_attempt"):
        permission = _more_restrictive(permission, "cooldown_required")
        reasons.append("recovery_or_revenge_pattern")

    effective_ratio = fee.get("conservative_net_fee_to_equity_ratio")
    gross_ratio = fee.get("gross_fee_to_equity_ratio")
    reduce_threshold = float(getattr(config, "FEE_TO_EQUITY_REDUCE_THRESHOLD", 0.02))
    block_threshold = float(getattr(config, "FEE_TO_EQUITY_BLOCK_THRESHOLD", 0.05))
    hard_threshold = float(getattr(config, "HARD_BLOCK_THRESHOLD", 0.10))

    if effective_ratio is not None:
        if effective_ratio >= hard_threshold:
            fee_permission = "hard_block" if recent_trades >= 8 else "blocked"
            permission = _more_restrictive(permission, fee_permission)
            reasons.append("net_fee_extreme" if recent_trades < 8 else "net_fee_extreme_with_repeated_trading")
        elif effective_ratio >= block_threshold:
            fee_permission = "blocked" if recent_trades >= 8 else "cooldown_required"
            permission = _more_restrictive(permission, fee_permission)
            reasons.append("net_fee_block_pressure" if recent_trades >= 8 else "net_fee_cooldown")
        elif effective_ratio >= reduce_threshold:
            permission = _more_restrictive(permission, "reduce_size_only")
            reasons.append("net_fee_size_reduction")

    # A high gross ratio still forbids plain allow, but rebate-aware net policy
    # decides whether this is size reduction, confirmation, cooldown, or block.
    if fee.get("overtrading_fee_warning") and permission == "allow":
        permission = "manual_confirm_required" if gross_ratio is not None else "reduce_size_only"
        reasons.append("gross_fee_warning_requires_confirmation")

    if recent_trades >= 8 and permission == "allow":
        permission = "cooldown_required"
        reasons.append("recent_overtrading")
    elif recent_trades >= 4 and permission == "allow":
        permission = "reduce_size_only"
        reasons.append("recent_trade_frequency")

    if freshness_warnings and permission == "allow":
        permission = "manual_confirm_required"
        reasons.append("stale_market_data_manual_confirmation")

    positions = _positions(account)
    return {
        "execution_permission": permission,
        "account_execution_permission": permission,
        "reasons": _dedupe(reasons),
        "fee_pressure_level": (
            "missing" if effective_ratio is None
            else "extreme" if effective_ratio >= hard_threshold
            else "high" if effective_ratio >= block_threshold
            else "elevated" if effective_ratio >= reduce_threshold
            else "warning" if fee.get("overtrading_fee_warning")
            else "normal"
        ),
        "fee_summary": fee,
        "data_integrity_error": data_integrity_error,
        "recent_2h_trade_count": recent_trades,
        "max_recent_leverage": max_leverage,
        "open_position_count": len(positions),
    }


def _freshness_warnings(market: dict) -> list[str]:
    freshness = market.get("freshness") if isinstance(market.get("freshness"), dict) else {}
    return [
        f"{name} stale (age_seconds={item.get('age_seconds')})"
        for name, item in freshness.items()
        if isinstance(item, dict) and item.get("stale")
    ]


def _position_alignment(direction: str, positions: list[dict]) -> tuple[str, list[str]]:
    sides = {_position_side(position) for position in positions}
    if direction == "상방 우위" and "short" in sides:
        return "conflicted", ["bullish breakout confirms: reduce or close conflicting short before adding risk"]
    if direction == "하방 우위" and "long" in sides:
        return "conflicted", ["bearish breakdown confirms: reduce or close conflicting long before adding risk"]
    return ("flat" if not positions else "aligned_or_mixed"), []


def _action_policy(
    direction: str,
    setup_action: str,
    permission: str,
    positions: list[dict],
    position_alignment: str,
    data_integrity_error: bool,
) -> tuple[list[str], list[str], str, str]:
    allowed = ["wait_for_trigger", "hold_existing"]
    if direction == "상방 우위":
        allowed += ["monitor_breakout", "monitor_support"]
    elif direction == "하방 우위":
        allowed += ["monitor_breakdown", "monitor_resistance"]
    if positions and not data_integrity_error:
        allowed += ["reduce_position", "close_position"]
    if data_integrity_error:
        allowed.append("manual_review")

    forbidden = ["no_stop_entry", "revenge_trade"]
    if direction == "상방 우위":
        forbidden.append("short_chase")
    elif direction == "하방 우위":
        forbidden.append("long_chase")
    if position_alignment == "conflicted":
        forbidden += ["add_to_losing_position", "increase_risk"]

    if permission == "allow":
        allowed.append(
            "open_new_position" if setup_action.startswith("enter_")
            else "open_new_position_after_trigger"
        )
    elif permission in ("reduce_size_only", "reduced_size"):
        allowed.append("enter_reduced_size_after_trigger")
        forbidden += ["high_leverage", "open_new_full_size", "repeated_reentry", "chase_entry"]
    elif permission == "manual_confirm_required":
        allowed.append("enter_after_manual_confirm")
        forbidden += ["high_leverage", "open_new_full_size", "repeated_reentry", "chase_entry"]
    elif permission == "cooldown_required":
        allowed += ["no_new_entry"]
        forbidden += ["open_new_full_size", "high_leverage", "repeated_reentry", "chase_entry"]
    elif permission in ("blocked", "hard_block"):
        allowed += ["no_new_entry"]
        forbidden += [
            "new_entry_when_blocked", "open_new_position", "open_new_full_size",
            "add_to_position", "reverse_position", "increase_risk", "high_leverage",
            "repeated_reentry", "chase_entry",
        ]
        if permission == "hard_block":
            allowed.append("manual_review")

    allowed = _dedupe(allowed)
    forbidden = _dedupe(forbidden)
    if permission in ("blocked", "hard_block"):
        final_action = (
            "manage_existing_position_only" if positions
            else "wait_for_trigger_but_no_new_entry_until_fee_cooldown"
        )
    elif permission == "cooldown_required":
        final_action = "manage_existing_position_only" if positions else "wait_for_trigger_with_cooldown"
    elif permission in ("reduce_size_only", "reduced_size"):
        final_action = (
            f"{setup_action}_with_size_limit" if setup_action.startswith("wait_")
            else f"{setup_action}_reduced_size"
        )
    elif permission == "manual_confirm_required":
        final_action = (
            f"{setup_action}_after_manual_confirm"
            if setup_action.startswith("enter_") else f"{setup_action}_with_manual_confirm"
        )
    else:
        final_action = setup_action
    if data_integrity_error:
        final_action = "manual_review_account_data_before_position_change"
    elif positions and position_alignment == "conflicted":
        if permission in ("blocked", "hard_block", "cooldown_required"):
            final_action = "reduce_or_close_conflicting_position_on_market_trigger_no_new_entry"
        else:
            final_action = (
                "reduce_or_close_conflicting_position_on_market_trigger_then_" + final_action
            )
    final_label = final_action.replace("_", " ")
    return allowed, forbidden, final_action, final_label


def build_decision_support(market: dict | None = None, account: dict | None = None) -> dict:
    market, account = market or {}, account or {}
    direction = _market_direction(market)
    warnings = _freshness_warnings(market)
    fee = build_fee_summary(account)
    overlay = build_account_overlay(account, fee, warnings)
    positions = _positions(account)
    alignment, position_guidance = _position_alignment(direction, positions)
    setup_action = "wait_for_trigger" if direction != "중립" else "no_edge"
    setup_quality = "acceptable" if direction != "중립" else "poor"
    allowed, forbidden, final_action, final_label = _action_policy(
        direction,
        setup_action,
        overlay["execution_permission"],
        positions,
        alignment,
        overlay["data_integrity_error"],
    )
    source_labels = {key: market.get(key, value) for key, value in SOURCE_LABELS.items()}
    source_labels["taker_delta_source"] = source_labels.get("taker_bucket_delta_source")
    forbidden_labels = [FORBIDDEN_LABELS.get(code, code) for code in forbidden]
    return {
        "market_direction": direction,
        "market_signal": direction,
        "setup_action_verdict": setup_action,
        "market_action_verdict": setup_action,
        "action_verdict": setup_action,  # legacy UI field follows setup, not account.
        "setup_quality": setup_quality,
        "entry_expectancy": setup_quality,
        "account_execution_permission": overlay["execution_permission"],
        "execution_permission": overlay["execution_permission"],  # legacy API field.
        "account_overlay": overlay,
        "final_action": final_action,
        "final_action_label": final_label,
        "allowed_actions": allowed,
        "forbidden_actions": forbidden,
        "forbidden_action_codes": forbidden,
        "forbidden_action_labels": forbidden_labels,
        "execution_reasons": overlay["reasons"],
        "action_reasons": ["market trigger not evaluated yet"],
        "source_labels": source_labels,
        "freshness_warnings": warnings,
        "oi_changes": {key: market.get(key) for key in market if "oi_change_" in key},
        "taker_delta": {key: market.get(key) for key in market if key.startswith("taker_delta_")},
        "taker_delta_divergence": market.get("divergence_label", "insufficient_data"),
        "fee_rebate_summary": fee,
        "fee_summary": fee,
        "fee_pressure_level": overlay["fee_pressure_level"],
        "position_alignment": alignment,
        "existing_position_guidance": position_guidance,
        "trigger_condition": None,
        "trigger_price": None,
        "trigger_zone": None,
        "entry_zone": None,
        "invalidation": None,
        "raw_trigger_text": None,
        "immediate_entry_allowed": False,
        "setup_immediate_entry_allowed": False,
        "immediate_entry_blockers": ["market trigger not evaluated yet"],
        "required_rr": 1.2,
        "account_adjusted_required_rr": _required_rr(overlay["execution_permission"]),
    }


def _required_rr(permission: str) -> float | None:
    if permission in ("blocked", "hard_block"):
        return None
    if permission == "cooldown_required":
        return 1.8
    if permission in ("reduce_size_only", "reduced_size", "manual_confirm_required"):
        return 1.5
    return 1.2


def _numbers(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in NUMBER_RE.findall(text or "")]


def _zones(text: str) -> list[list[float]]:
    zones = []
    for left, right in ZONE_RE.findall(text or ""):
        a, b = float(left.replace(",", "")), float(right.replace(",", ""))
        zones.append([min(a, b), max(a, b)])
    return zones


def _relevant_lines(text: str, phrases: tuple[str, ...]) -> list[str]:
    lines = []
    for raw in re.split(r"[\n]+", str(text or "")):
        cleaned = re.sub(r"^[\s•*\-]+", "", raw).strip()
        if cleaned and any(phrase.lower() in cleaned.lower() for phrase in phrases):
            lines.append(cleaned)
    return _dedupe(lines)


def extract_trigger_details(report_text: str, levels: dict | None = None) -> dict:
    """Extract condition text and levels conservatively; never invent a price."""
    text = str(report_text or "")
    levels = levels or {}
    trigger_lines = _relevant_lines(text, TRIGGER_PHRASES)
    raw_trigger = " OR ".join(trigger_lines[:4]) or None
    all_trigger_text = " ".join(trigger_lines)
    zones = _zones(all_trigger_text)
    entry_lines = _relevant_lines(text, ("진입", "entry", "롱", "숏"))
    entry_zones = _zones(" ".join(entry_lines))
    support_lines = _relevant_lines(text, ("지지", "support"))
    resistance_lines = _relevant_lines(text, ("저항", "resistance"))
    invalidation_lines = _relevant_lines(text, ("무효", "손절", "이탈", "breakdown", "invalidation"))

    trigger_price = None
    breakout_lines = _relevant_lines(
        text,
        ("돌파", "상회", "하회", "재돌파", "breakout", "close above", "close below", "resistance break"),
    )
    for line in breakout_lines:
        values = _numbers(line)
        if values:
            trigger_price = values[0]
            break
    if trigger_price is None:
        direction = str(levels.get("direction") or "")
        preferred = "bear_trigger" if "하방" in direction or "short" in direction else "bull_trigger"
        trigger_price = _as_float(levels.get(preferred) or levels.get("bull_trigger") or levels.get("bear_trigger"))

    invalidation = _as_float(levels.get("stop"))
    if invalidation is None and invalidation_lines:
        values = _numbers(invalidation_lines[0])
        invalidation = values[-1] if values else None

    support_zone = _zones(" ".join(support_lines))
    resistance_zone = _zones(" ".join(resistance_lines))
    return {
        "conditional": bool(trigger_lines),
        "trigger_condition": raw_trigger,
        "raw_trigger_text": raw_trigger,
        "trigger_price": trigger_price,
        "trigger_zone": zones[0] if zones else None,
        "entry_zone": entry_zones[0] if entry_zones else None,
        "support_zone": support_zone[0] if support_zone else None,
        "resistance_zone": resistance_zone[0] if resistance_zone else None,
        "invalidation": invalidation,
        "do_not_chase": any(phrase.lower() in text.lower() for phrase in CHASE_PHRASES),
        "explicit_immediate_long": any(phrase.lower() in text.lower() for phrase in IMMEDIATE_LONG_PHRASES),
        "explicit_immediate_short": any(phrase.lower() in text.lower() for phrase in IMMEDIATE_SHORT_PHRASES),
    }


def _inside_zone(price: float | None, zone: list[float] | None) -> bool:
    return bool(price is not None and zone and zone[0] <= price <= zone[1])


def _near_level(price: float | None, level: float | None, direction: str) -> bool:
    if price is None or level is None or price <= 0:
        return False
    if direction == "상방 우위" and level > price:
        return (level - price) / price <= float(getattr(config, "NEARBY_LEVEL_PCT", 0.005))
    if direction == "하방 우위" and level < price:
        return (price - level) / price <= float(getattr(config, "NEARBY_LEVEL_PCT", 0.005))
    return False


def _compose_after_setup(decision: dict) -> None:
    overlay = decision.get("account_overlay") if isinstance(decision.get("account_overlay"), dict) else {}
    permission = decision.get("account_execution_permission") or decision.get("execution_permission") or "manual_confirm_required"
    positions_exist = bool(overlay.get("open_position_count"))
    allowed, forbidden, final_action, final_label = _action_policy(
        decision.get("market_direction", "중립"),
        decision.get("setup_action_verdict", "wait_for_trigger"),
        permission,
        [{}] if positions_exist else [],
        decision.get("position_alignment", "flat"),
        bool(overlay.get("data_integrity_error")),
    )
    decision["allowed_actions"] = allowed
    decision["forbidden_actions"] = forbidden
    decision["forbidden_action_codes"] = forbidden
    decision["forbidden_action_labels"] = [FORBIDDEN_LABELS.get(code, code) for code in forbidden]
    decision["final_action"] = final_action
    decision["final_action_label"] = final_label


def apply_trade_quality(
    decision: dict,
    current_price: Any,
    levels: dict | None,
    report_text: str = "",
) -> dict:
    """Finalize setup semantics from current price, report conditions, and R:R."""
    levels = dict(levels or {})
    current = _as_float(current_price)
    if current is None:
        current = _as_float(levels.get("current_price") or levels.get("entry"))
    direction = _normalize_direction(decision.get("market_direction"))
    details = extract_trigger_details(report_text, {**levels, "direction": direction})

    entry = current if current is not None else _as_float(levels.get("entry"))
    stop = _as_float(levels.get("stop"))
    target = _as_float(levels.get("target"))
    risk = abs(entry - stop) if entry is not None and stop is not None else None
    reward = abs(target - entry) if entry is not None and target is not None else None
    rr = reward / risk if risk is not None and risk > 0 and reward is not None else None

    entry_zone = details.get("entry_zone")
    entry_reference = _as_float(levels.get("entry"))
    in_entry_zone = _inside_zone(current, entry_zone)
    if not entry_zone and current is not None and entry_reference is not None and current > 0:
        in_entry_zone = abs(current - entry_reference) / current <= 0.0025

    explicit_immediate = (
        details["explicit_immediate_long"] if direction == "상방 우위"
        else details["explicit_immediate_short"] if direction == "하방 우위"
        else False
    )
    resistance = _as_float(levels.get("resistance"))
    support = _as_float(levels.get("support"))
    nearest_blocking_level = resistance if direction == "상방 우위" else support
    nearby_level = _near_level(current, nearest_blocking_level, direction)

    trigger_price = _as_float(details.get("trigger_price"))
    trigger_unmet = bool(
        current is not None
        and trigger_price is not None
        and ((direction == "상방 우위" and current < trigger_price)
             or (direction == "하방 우위" and current > trigger_price))
    )
    blockers: list[str] = []
    if details["conditional"]:
        blockers.append("confirmation_required")
    if details["do_not_chase"]:
        blockers.append("chase_prohibited")
    if stop is None:
        blockers.append("no_clear_invalidation")
    if target is None:
        blockers.append("no_clear_target")
    if rr is not None and rr < 1.0:
        blockers.append("poor_risk_reward")
    if not in_entry_zone and not explicit_immediate:
        blockers.append("current_price_outside_entry_zone")
    if nearby_level:
        blockers.append("nearby_resistance_or_support")

    if trigger_unmet and not explicit_immediate:
        blockers.append("trigger_not_reached")
    setup_required_rr = float(getattr(config, "SETUP_MIN_RR", 1.2))
    immediate_setup = (
        direction in ("상방 우위", "하방 우위")
        and not blockers
        and rr is not None
        and rr >= setup_required_rr
        and (in_entry_zone or explicit_immediate)
    )

    if direction == "중립":
        setup_action, quality, reason = "no_edge", "poor", "conflicting_signals"
    elif immediate_setup:
        setup_action = "enter_long_now" if direction == "상방 우위" else "enter_short_now"
        quality = "excellent" if rr is not None and rr >= 2.0 else "good"
        reason = "immediate_setup_valid"
    elif details["do_not_chase"]:
        setup_action, quality, reason = "avoid_chase", "acceptable", "chase_prohibited"
    else:
        setup_action = "wait_for_trigger"
        if rr is not None and rr < 1.0:
            quality = "poor"
        elif details["conditional"] and rr is not None and rr >= 1.0:
            quality = "conditional_good"
        elif details["conditional"]:
            quality = "acceptable"
        else:
            quality = "acceptable" if rr is None or rr >= 1.0 else "poor"
        reason = blockers[0] if blockers else "waiting_for_better_location"

    decision.update({
        "setup_action_verdict": setup_action,
        "market_action_verdict": setup_action,
        "action_verdict": setup_action,
        "setup_quality": quality,
        "entry_expectancy": quality,
        "action_reasons": _dedupe([reason, *blockers]),
        "risk_reward": rr,
        "reward": reward,
        "risk": risk,
        "setup_required_rr": setup_required_rr,
        "required_rr": setup_required_rr,
        "account_adjusted_required_rr": _required_rr(decision.get("execution_permission", "allow")),
        "trigger_condition": details.get("trigger_condition"),
        "trigger_price": details.get("trigger_price"),
        "trigger_zone": details.get("trigger_zone"),
        "entry_zone": entry_zone,
        "support_zone": details.get("support_zone"),
        "resistance_zone": details.get("resistance_zone"),
        "invalidation": details.get("invalidation") or stop,
        "raw_trigger_text": details.get("raw_trigger_text"),
        "setup_immediate_entry_allowed": immediate_setup,
        "immediate_entry_blockers": _dedupe(blockers),
    })

    account_required_rr = decision.get("account_adjusted_required_rr")
    permission = decision.get("execution_permission")
    account_allows_now = permission not in ("blocked", "hard_block", "cooldown_required")
    rr_meets_account = bool(rr is not None and account_required_rr is not None and rr >= account_required_rr)
    decision["immediate_entry_allowed"] = bool(immediate_setup and account_allows_now and rr_meets_account)
    _compose_after_setup(decision)
    if details["do_not_chase"]:
        codes = _dedupe([*decision.get("forbidden_action_codes", []), "chase_entry"])
        decision["forbidden_actions"] = decision["forbidden_action_codes"] = codes
        decision["forbidden_action_labels"] = [FORBIDDEN_LABELS.get(code, code) for code in codes]
    account_adjusted_quality = quality
    if permission in ("blocked", "hard_block", "cooldown_required"):
        account_adjusted_quality = "not_executable"
    elif immediate_setup and not rr_meets_account:
        account_adjusted_quality = "acceptable"
        if permission in ("reduce_size_only", "reduced_size"):
            decision["final_action"] = "wait_for_better_rr_with_size_limit"
        elif permission == "manual_confirm_required":
            decision["final_action"] = "wait_for_better_rr_and_manual_confirm"
        decision["final_action_label"] = decision["final_action"].replace("_", " ")
    decision["account_adjusted_entry_expectancy"] = account_adjusted_quality
    return decision


def enforce_report_conditions(decision: dict, report_text: str, levels: dict | None) -> dict:
    """Backward-compatible wrapper; callers should prefer ``apply_trade_quality``."""
    current = (levels or {}).get("current_price") or (levels or {}).get("entry")
    return apply_trade_quality(decision, current, levels, report_text)


def format_decision_context(decision: dict) -> str:
    keys = (
        "market_direction", "setup_action_verdict", "setup_quality", "account_execution_permission",
        "final_action", "allowed_actions", "forbidden_action_codes", "execution_reasons",
        "source_labels", "freshness_warnings", "oi_changes", "taker_delta",
        "taker_delta_divergence", "fee_summary", "position_alignment",
    )
    return (
        "\n[Decision Support — market/setup/account/final layers are independent]\n"
        + "\n".join(f"{key}: {decision.get(key)}" for key in keys)
    )


def ensure_api_compatibility(payload: dict | None) -> dict:
    out = dict(payload or {})
    decision = out.get("decision_support") if isinstance(out.get("decision_support"), dict) else {}
    legacy_permission = decision.get("account_execution_permission") or decision.get("execution_permission")
    legacy_action = decision.get("setup_action_verdict") or decision.get("action_verdict")
    legacy_quality = decision.get("setup_quality") or decision.get("entry_expectancy")
    direction = _normalize_direction(decision.get("market_direction") or out.get("signal"))
    defaults = {
        "market_direction": direction,
        "market_signal": direction,
        "setup_action_verdict": "wait_for_trigger",
        "market_action_verdict": "wait_for_trigger",
        "action_verdict": "wait_for_trigger",
        "setup_quality": "acceptable",
        "entry_expectancy": "acceptable",
        "account_execution_permission": "manual_confirm_required",
        "execution_permission": "manual_confirm_required",
        "account_overlay": {"execution_permission": "manual_confirm_required", "reasons": ["decision support unavailable"]},
        "final_action": "wait_for_trigger_with_manual_confirm",
        "final_action_label": "wait for trigger with manual confirm",
        "allowed_actions": ["wait_for_trigger", "manual_review"],
        "forbidden_actions": [],
        "forbidden_action_codes": [],
        "forbidden_action_labels": [],
        "source_labels": {},
        "freshness_warnings": [],
        "oi_changes": {},
        "taker_delta": {},
        "taker_delta_divergence": "insufficient_data",
        "fee_rebate_summary": {"reason": "decision support unavailable"},
        "fee_summary": {"reason": "decision support unavailable"},
    }
    for key, value in defaults.items():
        decision.setdefault(key, value)
    permission = legacy_permission or "manual_confirm_required"
    setup_action = legacy_action or "wait_for_trigger"
    setup_quality = legacy_quality or "acceptable"
    decision["account_execution_permission"] = decision["execution_permission"] = permission
    decision["setup_action_verdict"] = decision["market_action_verdict"] = decision["action_verdict"] = setup_action
    decision["setup_quality"] = decision["entry_expectancy"] = setup_quality
    for key in ("account_overlay", "source_labels", "oi_changes", "taker_delta", "fee_rebate_summary", "fee_summary"):
        if not isinstance(decision.get(key), dict):
            decision[key] = dict(defaults[key])
    for key in (
        "allowed_actions", "forbidden_actions", "forbidden_action_codes",
        "forbidden_action_labels", "freshness_warnings",
    ):
        if not isinstance(decision.get(key), list):
            decision[key] = list(defaults[key])
    decision["account_overlay"]["execution_permission"] = permission
    decision["account_overlay"].setdefault("reasons", ["decision support unavailable"])
    if decision.get("setup_action_verdict", "").startswith("wait_") and decision.get("entry_expectancy") in ("good", "excellent"):
        decision["entry_expectancy"] = decision["setup_quality"] = "conditional_good"
    out["decision_support"] = decision
    return out
