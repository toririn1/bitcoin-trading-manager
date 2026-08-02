# =============================================
# 내 계좌 상태 수집 (Binance Futures / Gate.io Futures)
# 잔고 / 오픈 포지션 / 오늘 실현 손익
# =============================================
from __future__ import annotations

import hmac
import hashlib
import time as _time
from threading import Lock
import requests
from datetime import timezone
from typing import Optional
from urllib.parse import urlsplit
import config as _cfg
from http_client import _session as _http  # 프록시 환경변수 무시 세션
from account_history import attach_account_context_summary
from time_utils import start_of_kst_day

TRACKED_COLLATERAL_ASSETS = ("USDT", "USDC")
INCOME_CACHE_TTL_SECS = 8.0
_INCOME_CACHE_LOCK = Lock()
_INCOME_CACHE: dict[tuple[str, int], dict] = {}


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        response_url = exc.response.url or ""
        try:
            body = exc.response.json()
        except ValueError:
            body = {}

        api_code = body.get("code")
        api_msg = body.get("msg")
        path = urlsplit(response_url).path or response_url

        if status == 401:
            if api_code == -2015:
                return (
                    "Binance 인증 실패 (401 / -2015: API 키, IP 화이트리스트, "
                    "또는 선물 권한 문제)"
                )
            return f"Binance 인증 실패 ({status}: {path})"

        if api_code == -1021:
            return "Binance 시간 오차 오류 (-1021: 서버 시간과 로컬 시간 차이)"
        if api_code == -1022:
            return "Binance 서명 오류 (-1022: API secret 또는 서명 문자열 불일치)"
        if api_msg:
            return f"Binance API 오류 ({status} / {api_code}: {api_msg})"
        return f"Binance HTTP 오류 ({status}: {path})"

    msg = str(exc).strip()
    return msg if msg else exc.__class__.__name__


def _api_key_headers() -> dict:
    if not _cfg.BINANCE_API_KEY:
        raise RuntimeError("BINANCE_API_KEY가 비어 있습니다.")
    return {"X-MBX-APIKEY": _cfg.BINANCE_API_KEY}


def open_user_data_stream() -> str:
    """USDⓈ-M Futures user data stream listenKey 생성/연장."""
    try:
        r = _http.post(
            f"{_cfg.BINANCE_FUTURES_URL}/fapi/v1/listenKey",
            headers=_api_key_headers(),
            timeout=8,
        )
        r.raise_for_status()
        return str(r.json()["listenKey"])
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            raise RuntimeError(
                "Binance user data stream 인증 실패 (401). "
                "선물 API 키 권한, IP 제한, 또는 저장된 키 갱신 여부를 확인하세요."
            ) from exc
        raise


def keepalive_user_data_stream() -> str:
    """활성 user data stream listenKey TTL 연장."""
    r = _http.put(
        f"{_cfg.BINANCE_FUTURES_URL}/fapi/v1/listenKey",
        headers=_api_key_headers(),
        timeout=8,
    )
    r.raise_for_status()
    return str(r.json().get("listenKey") or "")


def close_user_data_stream() -> None:
    """활성 user data stream 종료."""
    r = _http.delete(
        f"{_cfg.BINANCE_FUTURES_URL}/fapi/v1/listenKey",
        headers=_api_key_headers(),
        timeout=8,
    )
    r.raise_for_status()


def _signed_get(endpoint: str, params: dict) -> dict:
    """Binance HMAC-SHA256 서명 GET — market_context.py와 동일한 패턴 사용"""
    if not _cfg.BINANCE_API_KEY or not _cfg.BINANCE_SECRET_KEY:
        raise RuntimeError("BINANCE_API_KEY 또는 BINANCE_SECRET_KEY가 비어 있습니다.")

    params["timestamp"] = int(_time.time() * 1000)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(
        _cfg.BINANCE_SECRET_KEY.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    r = _http.get(
        f"{_cfg.BINANCE_FUTURES_URL}{endpoint}",
        params={**params, "signature": sig},
        headers={"X-MBX-APIKEY": _cfg.BINANCE_API_KEY},
        timeout=8,
    )
    r.raise_for_status()
    return r.json()


def _fetch_balance(ctx: dict) -> None:
    """
    잔고 수집 — 두 엔드포인트 순차 시도로 안정성 확보.
    1순위: /fapi/v2/balance  (자산별 조회, 선물계좌 잔고에 가장 정확)
    2순위: /fapi/v2/account  (계좌 전체 요약)
    """
    # ── 1순위: fapi/v2/balance ─────────────────
    try:
        assets = _signed_get("/fapi/v2/balance", {})
        tracked_assets = []
        for asset in assets:
            name = str(asset.get("asset") or "").upper()
            if name not in TRACKED_COLLATERAL_ASSETS:
                continue

            balance = float(asset.get("balance") or 0)
            available = float(asset.get("availableBalance") or 0)
            upnl = float(asset.get("crossUnPnl") or 0)
            margin = float(asset.get("crossWalletBalance") or asset.get("balance") or 0)
            if max(abs(balance), abs(available), abs(upnl), abs(margin)) <= 1e-9:
                continue

            tracked_assets.append({
                "asset": name,
                "wallet_balance": balance,
                "available_balance": available,
                "unrealized_pnl": upnl,
                "margin_balance": margin,
            })

        if tracked_assets:
            ctx["balance_assets"] = tracked_assets
            ctx["wallet_balance"] = sum(a["wallet_balance"] for a in tracked_assets)
            ctx["available_balance"] = sum(a["available_balance"] for a in tracked_assets)
            ctx["unrealized_pnl"] = sum(a["unrealized_pnl"] for a in tracked_assets)
            ctx["margin_balance"] = sum(a["margin_balance"] for a in tracked_assets)
            ctx["balance_error"] = None
            return
    except Exception as exc:
        first_error = exc
    else:
        first_error = RuntimeError("USDT/USDC 담보 자산을 찾지 못했습니다.")

    # ── 2순위: fapi/v2/account (폴백) ──────────
    try:
        data = _signed_get("/fapi/v2/account", {})
        ctx["wallet_balance"]    = float(data["totalWalletBalance"])
        ctx["unrealized_pnl"]    = float(data["totalUnrealizedProfit"])
        ctx["available_balance"] = float(data["availableBalance"])
        ctx["margin_balance"]    = float(data["totalMarginBalance"])
        ctx["balance_assets"]    = None
        ctx["balance_error"]     = None
    except Exception as exc:
        ctx["wallet_balance"]    = None
        ctx["unrealized_pnl"]    = None
        ctx["available_balance"] = None
        ctx["margin_balance"]    = None
        ctx["balance_assets"]    = None
        ctx["balance_error"]     = (
            f"1차: {_safe_error_message(first_error)} | "
            f"2차: {_safe_error_message(exc)}"
        )


def _account_equity(ctx: dict) -> float | None:
    wallet = ctx.get("wallet_balance")
    upnl = ctx.get("unrealized_pnl")
    try:
        if wallet is not None and upnl is not None:
            return float(wallet) + float(upnl)
        if ctx.get("margin_balance") is not None:
            return float(ctx["margin_balance"])
    except (TypeError, ValueError):
        return None
    return None


def _income_cache_key(symbol: Optional[str], start_ms: int) -> tuple[str, int]:
    return (str(symbol or "").upper(), int(start_ms))


_INCOME_PAGE_LIMIT = 1000  # Binance 단일 요청 최대값


def _fetch_income_all(income_type: str, base_params: dict) -> list:
    """1000건 초과 거래를 페이지네이션으로 전량 수집."""
    records: list = []
    params = {**base_params, "incomeType": income_type, "limit": _INCOME_PAGE_LIMIT}
    while True:
        page = _signed_get("/fapi/v1/income", params)
        if not page:
            break
        records.extend(page)
        if len(page) < _INCOME_PAGE_LIMIT:
            break
        # 마지막 항목의 time + 1ms 를 startTime으로 설정해 다음 페이지 조회
        last_time = int(page[-1].get("time") or 0)
        params = {**params, "startTime": last_time + 1}
    return records


def _fetch_income_summary(symbol: Optional[str], start_ms: int) -> dict:
    cache_key = _income_cache_key(symbol, start_ms)
    now_mono = _time.monotonic()

    with _INCOME_CACHE_LOCK:
        cached = _INCOME_CACHE.get(cache_key)
        if cached and cached.get("expires_at", 0.0) > now_mono:
            return dict(cached["value"])

    base_params: dict = {"startTime": start_ms}
    if symbol:
        base_params["symbol"] = symbol

    pnl_records        = _fetch_income_all("REALIZED_PNL", base_params)
    fee_records        = _fetch_income_all("FUNDING_FEE",  base_params)
    commission_records = _fetch_income_all("COMMISSION",   base_params)

    trade_keys = {
        (item.get("symbol"), item.get("time"), item.get("tranId"))
        for item in [*pnl_records, *commission_records]
    }
    summary = {
        "realized":    sum(float(item["income"]) for item in pnl_records),
        "funding":     sum(float(item["income"]) for item in fee_records),
        "commission":  sum(float(item["income"]) for item in commission_records),
        "trade_count": len(trade_keys),
    }

    with _INCOME_CACHE_LOCK:
        expired_keys = [
            key for key, value in _INCOME_CACHE.items()
            if value.get("expires_at", 0.0) <= now_mono
        ]
        for key in expired_keys:
            _INCOME_CACHE.pop(key, None)
        _INCOME_CACHE[cache_key] = {
            "expires_at": now_mono + INCOME_CACHE_TTL_SECS,
            "value": summary,
        }

    return dict(summary)


def _fetch_binance_account_context(symbol: Optional[str] = None) -> dict:
    """
    Binance Futures API로 계좌 현황을 수집.
    개별 요청 실패 시 None으로 채워 분석 전체를 블로킹하지 않음.
    """
    ctx: dict = {}

    # ── 잔고 ──────────────────────────────────
    _fetch_balance(ctx)
    ctx["account_equity"] = _account_equity(ctx)

    # ── 오픈 포지션 ───────────────────────────
    try:
        params = {"symbol": symbol} if symbol else {}
        positions = _signed_get("/fapi/v2/positionRisk", params)
        open_pos = [p for p in positions if abs(float(p["positionAmt"])) > 0]
        ctx["open_positions"] = []
        for p in open_pos:
            amt      = float(p["positionAmt"])
            entry    = float(p["entryPrice"])
            lev      = int(p["leverage"])
            upnl     = float(p["unRealizedProfit"])
            notional = abs(amt) * entry
            margin   = notional / lev if lev > 0 else 0
            pnl_pct  = (upnl / margin * 100) if margin > 0 else 0
            ctx["open_positions"].append({
                "symbol":             p["symbol"],
                "margin_asset":       p.get("marginAsset"),
                "side":               "롱" if amt > 0 else "숏",
                "size":               abs(amt),
                "entry_price":        entry,
                "mark_price":         float(p["markPrice"]),
                "unrealized_pnl":     upnl,
                "unrealized_pnl_pct": pnl_pct,
                "leverage":           lev,
                "liquidation_price":  float(p["liquidationPrice"]),
                "margin_type":        p["marginType"],
                "notional":           notional,
            })
        ctx["open_positions"].sort(key=lambda p: p["notional"], reverse=True)
        ctx["open_position_count"] = len(ctx["open_positions"])
        ctx["open_position_notional"] = sum(p["notional"] for p in ctx["open_positions"])
        ctx["open_position_upnl"] = sum(p["unrealized_pnl"] for p in ctx["open_positions"])
        leverages = [p["leverage"] for p in ctx["open_positions"] if p.get("leverage") is not None]
        if leverages:
            lev_min = min(leverages)
            lev_max = max(leverages)
            total_notional = ctx["open_position_notional"] or 0
            if total_notional > 0:
                weighted = sum(p["notional"] * p["leverage"] for p in ctx["open_positions"]) / total_notional
            else:
                weighted = sum(leverages) / len(leverages)

            ctx["effective_leverage"] = weighted if lev_min != lev_max else float(lev_min)
            ctx["leverage_min"] = lev_min
            ctx["leverage_max"] = lev_max
            ctx["leverage_weighted"] = weighted
            if lev_min == lev_max:
                ctx["leverage_display"] = f"{lev_min}x (실제 포지션 기준)"
                ctx["leverage_mode"] = "single"
            else:
                ctx["leverage_display"] = (
                    f"혼합 {lev_min}x~{lev_max}x "
                    f"(가중평균 {weighted:.1f}x)"
                )
                ctx["leverage_mode"] = "mixed"
        else:
            ctx["effective_leverage"] = None
            ctx["leverage_min"] = None
            ctx["leverage_max"] = None
            ctx["leverage_weighted"] = None
            ctx["leverage_display"] = f"오픈 포지션 없음 (기본 {_cfg.DEFAULT_LEVERAGE}x)"
            ctx["leverage_mode"] = "default"
        ctx["position_error"] = None
    except Exception as exc:
        ctx["open_positions"] = None
        ctx["open_position_count"] = None
        ctx["open_position_notional"] = None
        ctx["open_position_upnl"] = None
        ctx["effective_leverage"] = None
        ctx["leverage_min"] = None
        ctx["leverage_max"] = None
        ctx["leverage_weighted"] = None
        ctx["leverage_display"] = "조회 실패"
        ctx["leverage_mode"] = "error"
        ctx["position_error"] = _safe_error_message(exc)

    # ── 미체결 주문 (TP / SL / 지정가) ─────────────────
    try:
        raw_orders = _signed_get("/fapi/v1/openOrders", {"timestamp": int(_time.time() * 1000)})
        open_orders = []
        for o in (raw_orders if isinstance(raw_orders, list) else []):
            stop_price = float(o.get("stopPrice", 0) or 0)
            limit_price = float(o.get("price", 0) or 0)
            price = stop_price if stop_price > 0 else limit_price
            if price <= 0:
                continue
            open_orders.append({
                "symbol":      o.get("symbol", ""),
                "order_id":    o.get("orderId"),
                "type":        o.get("type", ""),
                "side":        o.get("side", ""),
                "price":       price,
                "qty":         float(o.get("origQty", 0) or 0),
                "reduce_only": bool(o.get("reduceOnly", False)),
            })

        # 포지션에 TP / SL 매칭
        # TP: TAKE_PROFIT_MARKET, TAKE_PROFIT, 또는 reduce-only LIMIT (롱이면 높은 가격, 숏이면 낮은 가격)
        TP_TYPES = {"TAKE_PROFIT_MARKET", "TAKE_PROFIT"}
        SL_TYPES = {"STOP_MARKET", "STOP"}
        matched_order_ids: set = set()
        if ctx.get("open_positions"):
            for pos in ctx["open_positions"]:
                sym = pos["symbol"]
                entry = pos["entry_price"]
                close_side = "SELL" if pos["side"] == "롱" else "BUY"
                sym_orders = [o for o in open_orders if o["symbol"] == sym
                              and o["reduce_only"] and o["side"] == close_side]

                # 표준 TP/SL 오더 타입 먼저 매칭
                tp_order = next((o for o in sym_orders if o["type"] in TP_TYPES), None)
                sl_order = next((o for o in sym_orders if o["type"] in SL_TYPES), None)

                # reduce-only LIMIT 오더 → 진입가보다 유리한 방향이면 TP, 불리한 방향이면 SL
                if tp_order is None or sl_order is None:
                    for o in sym_orders:
                        if o["type"] != "LIMIT":
                            continue
                        p = o["price"]
                        is_tp_side = (close_side == "SELL" and p > entry) or \
                                     (close_side == "BUY"  and p < entry)
                        if is_tp_side and tp_order is None:
                            tp_order = o
                        elif not is_tp_side and sl_order is None:
                            sl_order = o

                pos["tp_price"] = tp_order["price"] if tp_order else None
                pos["sl_price"] = sl_order["price"] if sl_order else None
                for o in (tp_order, sl_order):
                    if o:
                        matched_order_ids.add(o["order_id"])

        # TP/SL로 이미 매칭된 것 제외한 나머지 오더 전체 노출 (LIMIT, 미체결 진입 등)
        ctx["open_orders"] = [o for o in open_orders if o["order_id"] not in matched_order_ids]
        ctx["order_error"] = None
    except Exception as exc:
        ctx["open_orders"] = []
        ctx["order_error"] = _safe_error_message(exc)

    # ── 오늘 손익: 실현 손익 + 펀딩비 (KST 00:00 기준) ──
    try:
        today_start_ms = int(start_of_kst_day().astimezone(timezone.utc).timestamp() * 1000)
        income_summary = _fetch_income_summary(symbol, today_start_ms)
        today_realized = income_summary["realized"]
        today_funding = income_summary["funding"]
        today_commission = income_summary["commission"]
        ctx["today_trade_count"] = income_summary["trade_count"]

        ctx["today_realized_pnl"] = today_realized
        ctx["today_funding_fee"]  = today_funding
        ctx["today_commission_fee"] = today_commission
        ctx["today_cash_pnl"] = today_realized + today_funding + today_commission
        ctx["today_eval_pnl"] = None
        ctx["today_total_pnl"] = ctx["today_cash_pnl"]
        ctx["today_total_mode"] = "cash"
        ctx["today_total_label"] = "금일 현금손익"
        ctx["day_start_equity"] = None
        ctx["day_anchor_source"] = "cash"
        ctx["carryover_positions"] = []
        ctx["pnl_error"]          = None
    except Exception as exc:
        ctx["today_realized_pnl"] = None
        ctx["today_funding_fee"]  = None
        ctx["today_commission_fee"] = None
        ctx["today_cash_pnl"] = None
        ctx["today_eval_pnl"] = None
        ctx["today_total_pnl"]    = None
        ctx["today_total_mode"] = None
        ctx["today_total_label"] = None
        ctx["day_start_equity"] = None
        ctx["day_anchor_source"] = None
        ctx["carryover_positions"] = []
        ctx["today_trade_count"]  = None
        ctx["pnl_error"]          = _safe_error_message(exc)

    # ── 사용자 설정 ───────────────────────────
    ctx["configured_leverage"]  = _cfg.DEFAULT_LEVERAGE

    # ── UI / 보고용 요약 필드 ──────────────────
    wallet = ctx.get("wallet_balance")
    equity = ctx.get("account_equity")
    total_pnl = ctx.get("today_total_pnl")
    ctx["today_pnl_pct"] = None

    if equity is not None and total_pnl is not None:
        # wallet_balance 기준: unrealized PnL을 분모에서 제외해 수익률 오차 방지
        # equity(=wallet+unrealized)를 쓰면 오픈 포지션 규모만큼 분모가 부풀려짐
        wallet = ctx.get("wallet_balance")
        cash_pnl = ctx.get("today_cash_pnl") or total_pnl
        if wallet is not None:
            start_balance = wallet - cash_pnl
        else:
            start_balance = equity - total_pnl
        if start_balance <= 0:
            start_balance = equity - total_pnl  # fallback
        if ctx.get("day_start_equity") is None and start_balance > 0:
            ctx["day_start_equity"] = start_balance
        today_pct = (total_pnl / start_balance * 100) if start_balance > 0 else 0
        ctx["today_pnl_pct"] = today_pct

    attach_account_context_summary(ctx)
    return ctx


def _format_binance_account_context(ctx: dict) -> str:
    """현재 스냅샷 + 최근 계좌 운영 맥락을 함께 출력 — 판단은 Claude에게 위임"""
    lines = ["[계좌 / 리스크 제약]"]

    # ── 잔고 ──────────────────────────────────
    wallet = ctx.get("wallet_balance")
    balance_assets = ctx.get("balance_assets") or []
    if wallet is not None:
        if balance_assets:
            assets_str = " / ".join(
                f"{a['asset']} ${a['wallet_balance']:,.2f}"
                for a in balance_assets
            )
            lines.append(f"  담보 자산 잔고:  {assets_str}")
            lines.append(f"  추적 자산 합계:  ${wallet:,.2f} (USDT+USDC)")
        else:
            lines.append(f"  계좌 지갑 잔고:  ${wallet:,.2f}")
        if ctx.get("margin_balance") is not None:
            lines.append(f"  마진 잔고:       ${ctx['margin_balance']:,.2f}")
        if ctx.get("unrealized_pnl") is not None:
            lines.append(f"  계좌 미실현:     ${ctx['unrealized_pnl']:+,.2f}")
        if ctx.get("available_balance") is not None:
            lines.append(f"  사용 가능 잔고:  ${ctx['available_balance']:,.2f}")
    else:
        err = ctx.get("balance_error") or "API 키 권한 또는 네트워크 확인 필요"
        lines.append(f"  잔고 조회 실패 — {err}")

    # ── 일일 손익 & 목표/한도 ─────────────────
    total_pnl = ctx.get("today_total_pnl")
    total_label = ctx.get("today_total_label") or "오늘 손익"
    total_mode = ctx.get("today_total_mode") or "cash"
    cash_pnl = ctx.get("today_cash_pnl")
    realized  = ctx.get("today_realized_pnl")
    funding   = ctx.get("today_funding_fee")
    commission = ctx.get("today_commission_fee")
    anchor_source = ctx.get("day_anchor_source") or ""
    lev_display = ctx.get("leverage_display")
    start_equity = ctx.get("day_start_equity")
    current_equity = ctx.get("account_equity")

    if total_pnl is not None and current_equity is not None:
        if start_equity is not None:
            start_balance = start_equity
        else:
            # wallet_balance 기준 역산: unrealized PnL을 분모에서 제외
            _wallet = ctx.get("wallet_balance")
            _cash = cash_pnl if cash_pnl is not None else total_pnl
            start_balance = (_wallet - _cash) if _wallet is not None else (current_equity - total_pnl)
            if start_balance <= 0:
                start_balance = current_equity - total_pnl
        today_pct = (total_pnl / start_balance * 100) if start_balance > 0 else 0

        lines.append(f"  {total_label}(KST): ${total_pnl:+,.2f} ({today_pct:+.2f}%)")
        detail_lines: list[str] = []
        if total_mode == "evaluation" and start_equity is not None:
            detail_lines.append(
                f"자정 기준 평가: ${start_equity:,.2f} → 현재 ${current_equity:,.2f}"
            )
        if total_mode == "evaluation" and anchor_source == "prev_close":
            detail_lines.append("평가 기준:  전일 마지막 표본으로 보정")
        if total_mode == "cash" and anchor_source == "cash_fallback":
            detail_lines.append("평가 기준:  자정 인근 표본 대기 중")
        if cash_pnl is not None and total_mode == "evaluation":
            detail_lines.append(f"현금손익:  ${cash_pnl:+,.2f}")
        if realized is not None:
            detail_lines.append(f"실현 손익:  ${realized:+,.2f}")
        if funding is not None:
            detail_lines.append(f"펀딩비:     ${funding:+,.2f}")
        if commission is not None:
            detail_lines.append(f"거래 수수료: ${commission:+,.2f}")
        if ctx.get("open_position_upnl") is not None:
            detail_lines.append(f"현재 미실현: ${ctx['open_position_upnl']:+,.2f}")
        for idx, detail in enumerate(detail_lines):
            branch = "└" if idx == len(detail_lines) - 1 else "├"
            lines.append(f"    {branch} {detail}")
        if ctx.get("today_trade_count") is not None:
            lines.append(f"  오늘 거래 기록:  {ctx['today_trade_count']}건")
    else:
        err = ctx.get("pnl_error") or "income 조회 실패"
        lines.append(f"  오늘 손익 조회 실패 — {err}")

    if lev_display:
        lines.append(f"  레버리지 상태:   {lev_display}")
    else:
        lines.append(f"  레버리지 상태:   N/A")

    summary = ctx.get("context_summary") or {}
    summary_sections = [section for section in (summary.get("sections") or []) if section]
    summary_lines = [line for line in (summary.get("lines") or []) if line]
    if summary_sections:
        lines.append("  최근 계좌 운영 맥락:")
        for section in summary_sections:
            label = section.get("label") or "계좌 맥락"
            lines.append(f"    [{label}]")
            for line in section.get("lines") or []:
                lines.append(f"      {line}")
    elif summary_lines:
        lines.append("  최근 계좌 운영 맥락:")
        for line in summary_lines:
            lines.append(f"    {line}")

    # ── 오픈 포지션 ───────────────────────────
    positions = ctx.get("open_positions")
    if positions is None:
        err = ctx.get("position_error") or "positionRisk 조회 실패"
        lines.append(f"  포지션 조회 실패 — {err}")
    elif not positions:
        lines.append("  현재 오픈 포지션: 없음")
    else:
        count = ctx.get("open_position_count", len(positions))
        total_notional = ctx.get("open_position_notional")
        total_upnl = ctx.get("open_position_upnl")
        summary = f"  오픈 포지션: {count}개"
        if total_notional is not None:
            summary += f" / 총 명목 ${total_notional:,.2f}"
        if total_upnl is not None:
            summary += f" / 총 미실현 ${total_upnl:+,.2f}"
        lines.append(summary)
        preview = positions[:4]
        for p in preview:
            margin_asset = p.get("margin_asset") or "N/A"
            lines.append(
                f"    [{p['symbol']} {p['side']} / 담보 {margin_asset}]  "
                f"수량 {p['size']}  진입 ${p['entry_price']:,.2f}  "
                f"현재가 ${p['mark_price']:,.2f}  "
                f"미실현 ${p['unrealized_pnl']:+,.2f} ({p['unrealized_pnl_pct']:+.2f}%)  "
                f"레버리지 {p['leverage']}x  청산가 ${p['liquidation_price']:,.2f}"
            )
        remaining = len(positions) - len(preview)
        if remaining > 0:
            lines.append(f"    외 {remaining}개 포지션은 노이즈 방지를 위해 생략")

    return "\n".join(lines)


# =============================================
# Gate.io provider
# =============================================

def _fetch_gate_account_context() -> dict:
    """Gate.io Futures read-only 계좌/포지션 수집.

    - key/secret 미설정 → 비활성 상태 반환 (앱 종료 없음)
    - 조회 실패 → error 필드만 채워 반환
    """
    if not _cfg.gate_key_configured():
        return {
            "provider": "gateio",
            "disabled": True,
            "disabled_reason": "Gate.io API 키 또는 시크릿이 설정되지 않았습니다.",
            "wallet": None,
            "account": None,
            "positions": None,
            "wallet_error": None,
            "account_error": None,
            "position_error": None,
        }

    try:
        from account_providers.gateio import fetch_gate_account_context as _gate_fetch
        ctx = _gate_fetch(
            base_url=_cfg.GATE_BASE_URL,
            api_key=_cfg.GATE_API_KEY,
            api_secret=_cfg.GATE_API_SECRET,
            settle=_cfg.GATE_SETTLE,
        )
    except Exception:
        ctx = {
            "provider": "gateio",
            "wallet": None,
            "account": None,
            "positions": None,
            "wallet_error": "Gate.io 전체 잔고 조회 실패",
            "account_error": "Gate.io 계좌 조회 실패",
            "position_error": "Gate.io 포지션 조회 실패",
        }

    # ── account_equity 계산 (Gate Assets Analysis 기준) ─────────
    # Gate /wallet/total_balance 의 total.amount 는 현재 화면상 지갑/담보 잔고에 가깝고,
    # Assets Analysis 의 Total Assets 는 여기에 미실현 손익을 더한 값으로 보인다.
    # 그래서 의미를 분리한다:
    # - wallet_balance: 지갑/담보 잔고
    # - account_equity/total_assets: 성과 차트용 총자산(지갑/담보 + 미실현)
    # - futures_account_equity: futures accounts total/추산값
    wallet  = ctx.get("wallet") or {}
    account = ctx.get("account") or {}
    positions = ctx.get("positions") or []

    wallet_total_balance = None
    try:
        wallet_total_balance = float(wallet.get("total_amount") or 0) or None
    except (TypeError, ValueError):
        pass

    futures_total = None
    try:
        futures_total = float(account.get("futures_total") or 0) or None
    except (TypeError, ValueError):
        pass

    # futures_total이 0 또는 None이면 추산값 사용
    # (dual/isolated 모드에서 API total=0으로 오는 경우 대응)
    if not futures_total:
        futures_total = None  # account_providers/gateio.py _extract_account_fields에서 이미 역산

    upnl = None
    try:
        upnl = float(account.get("unrealised_pnl") or 0)
    except (TypeError, ValueError):
        pass

    total_assets = None
    if wallet_total_balance is not None:
        total_assets = wallet_total_balance + (upnl or 0.0)

    ctx["account_equity"] = total_assets if total_assets is not None else futures_total
    ctx["total_assets"] = total_assets
    ctx["wallet_total_balance"] = wallet_total_balance
    ctx["futures_account_equity"] = futures_total

    # open_position_count / open_position_upnl — account_history 호환 필드
    ctx["open_position_count"] = len(positions)
    ctx["open_position_upnl"]  = upnl
    ctx["unrealised_pnl"]      = upnl

    gross_notional = 0.0
    long_notional = 0.0
    short_notional = 0.0
    weighted_actual_num = 0.0
    weighted_actual_den = 0.0
    for pos in positions:
        notional = pos.get("notional")
        if notional is None:
            try:
                notional = float(pos.get("size") or 0.0) * float(pos.get("mark_price") or 0.0)
            except (TypeError, ValueError):
                notional = None
        try:
            notional_f = abs(float(notional)) if notional is not None else 0.0
        except (TypeError, ValueError):
            notional_f = 0.0
        if notional_f > 0:
            gross_notional += notional_f
            if pos.get("side") == "숏":
                short_notional += notional_f
            else:
                long_notional += notional_f
            pos["notional"] = round(notional_f, 6)

        actual_lev = pos.get("actual_leverage")
        if actual_lev is None:
            margin_basis = pos.get("leverage_margin") or pos.get("margin") or pos.get("initial_margin")
            try:
                margin_basis_f = float(margin_basis)
            except (TypeError, ValueError):
                margin_basis_f = 0.0
            if notional_f > 0 and margin_basis_f > 0:
                actual_lev = notional_f / margin_basis_f
                pos["actual_leverage"] = round(actual_lev, 4)
                pos["leverage_margin"] = margin_basis_f

        try:
            actual_lev_f = float(actual_lev)
        except (TypeError, ValueError):
            actual_lev_f = 0.0
        if notional_f > 0 and actual_lev_f > 0:
            weighted_actual_num += actual_lev_f * notional_f
            weighted_actual_den += notional_f

    gross_actual_leverage = None
    net_actual_leverage = None
    net_notional = abs(long_notional - short_notional)
    hedge_offset_ratio = None
    if gross_notional > 0:
        hedge_offset_ratio = 1.0 - (net_notional / gross_notional)
    if ctx["account_equity"] and gross_notional > 0:
        try:
            equity_f = float(ctx["account_equity"])
            gross_actual_leverage = gross_notional / equity_f
            net_actual_leverage = net_notional / equity_f
        except (TypeError, ValueError, ZeroDivisionError):
            gross_actual_leverage = None
            net_actual_leverage = None
    ctx["open_position_notional"] = round(gross_notional, 6)
    ctx["gross_position_notional"] = round(gross_notional, 6)
    ctx["net_position_notional"] = round(net_notional, 6)
    ctx["long_notional"] = round(long_notional, 6)
    ctx["short_notional"] = round(short_notional, 6)
    ctx["hedge_offset_ratio"] = round(hedge_offset_ratio, 4) if hedge_offset_ratio is not None else None
    ctx["account_gross_leverage"] = round(gross_actual_leverage, 4) if gross_actual_leverage is not None else None
    ctx["account_net_leverage"] = round(net_actual_leverage, 4) if net_actual_leverage is not None else None
    ctx["account_actual_leverage"] = ctx["account_gross_leverage"]
    ctx["position_actual_leverage"] = (
        round(weighted_actual_num / weighted_actual_den, 4)
        if weighted_actual_den > 0 else None
    )
    ctx["effective_leverage"] = ctx["account_net_leverage"]
    ctx["leverage_display"] = (
        f"순 {ctx['account_net_leverage']:.2f}x / 총 {ctx['account_gross_leverage']:.2f}x"
        if ctx["account_net_leverage"] is not None and ctx["account_gross_leverage"] is not None
        else "오픈 포지션 없음"
    )

    # wallet_balance, available_balance — account_history 호환
    available = None
    try:
        available = float(account.get("available") or 0) or None
    except (TypeError, ValueError):
        pass
    ctx["wallet_balance"]    = wallet_total_balance if wallet_total_balance is not None else ctx["account_equity"]
    ctx["available_balance"] = available

    open_orders = ctx.get("open_orders") if isinstance(ctx.get("open_orders"), list) else []
    order_notional = 0.0
    for order in open_orders:
        try:
            order_notional += float(order.get("notional") or 0.0)
        except (TypeError, ValueError):
            pass
    order_margin = None
    try:
        order_margin = float(account.get("order_margin") or 0.0)
    except (TypeError, ValueError):
        pass
    ctx["open_orders"] = open_orders
    ctx["open_order_count"] = len(open_orders)
    ctx["open_order_notional"] = round(order_notional, 6)
    ctx["order_margin"] = order_margin

    # 최근 확정손익/청산 이력: 멀티에이전트가 현재 포지션의 미실현뿐 아니라
    # 직전 청산 성과와 청산 위치를 같이 볼 수 있게 요약한다.
    try:
        from account_providers.gateio import get_recent_realized_context as _gate_realized
        ctx["realized_context"] = _gate_realized(
            base_url=_cfg.GATE_BASE_URL,
            api_key=_cfg.GATE_API_KEY,
            api_secret=_cfg.GATE_API_SECRET,
            settle=_cfg.GATE_SETTLE,
            days=7,
            spot_api_key=_cfg.GATE_SPOT_API_KEY,
            spot_api_secret=_cfg.GATE_SPOT_API_SECRET,
        )
    except Exception:
        ctx["realized_context"] = {
            "status": "error",
            "message": "최근 확정손익 조회 실패",
            "summary": {},
            "position_closes": [],
        }

    # account_history._apply_day_context_locked 에서 설정되지 않은 필드들 초기화
    # → 스냅샷 저장 및 텍스트 포맷에서 KeyError / None 비교 오류 방지
    ctx.setdefault("today_cash_pnl", None)
    ctx.setdefault("today_realized_pnl", None)
    ctx.setdefault("today_funding_fee", None)
    ctx.setdefault("today_commission_fee", None)
    ctx.setdefault("today_total_pnl", None)
    ctx.setdefault("today_total_mode", None)
    ctx.setdefault("today_total_label", None)
    ctx.setdefault("day_start_equity", None)
    ctx.setdefault("day_anchor_source", None)
    ctx.setdefault("today_pnl_pct", None)
    ctx.setdefault("today_eval_pnl", None)
    ctx.setdefault("risk_status", None)
    ctx.setdefault("carryover_positions", [])
    ctx.setdefault("open_orders", [])
    ctx.setdefault("open_order_count", 0)
    ctx.setdefault("open_order_notional", 0.0)
    ctx.setdefault("order_margin", None)
    ctx.setdefault("order_error", None)

    attach_account_context_summary(ctx)
    return ctx


def _format_gate_account_context(ctx: dict) -> str:
    """Gate.io 계좌 컨텍스트를 LLM 프롬프트용 사람 읽는 형태의 텍스트로 변환.

    API key, secret, sign, headers, raw request는 절대 포함하지 않는다.
    LLM에게는 '리스크 평가 및 관점 판단'에 필요한 정보만 전달한다.
    """
    lines = ["[계좌 / 리스크 제약 — Gate.io Futures (read-only)]"]

    if ctx.get("disabled"):
        lines.append(f"  계좌 연동 비활성 — {ctx.get('disabled_reason', '설정 누락')}")
        return "\n".join(lines)

    wallet  = ctx.get("wallet") or {}
    acc     = ctx.get("account") or {}
    acc_err = ctx.get("account_error")
    settle  = (acc.get("currency") or _cfg.GATE_SETTLE.upper())

    # ── 잔고 (라벨 분리) ──────────────────────
    if ctx.get("wallet_error") and ctx.get("account_error"):
        lines.append(f"  잔고 조회 실패 — {acc_err or ctx.get('wallet_error')}")
    else:
        # 전체 계정 총자산 (Gate Assets Analysis 기준)
        total_assets = ctx.get("total_assets")
        if total_assets is None and wallet.get("total_amount") is not None:
            try:
                total_assets = float(wallet.get("total_amount")) + float(acc.get("unrealised_pnl") or 0.0)
            except (TypeError, ValueError):
                total_assets = wallet.get("total_amount")
        if total_assets is not None:
            lines.append(f"  전체 계정 총자산:      {float(total_assets):,.2f} {settle}")

        wallet_total = ctx.get("wallet_total_balance")
        if wallet_total is None:
            wallet_total = wallet.get("total_amount")
        if wallet_total is not None:
            lines.append(f"  지갑/담보 잔고:       {float(wallet_total):,.2f} {settle}")

        # 선물 계정 가치
        fut_total  = acc.get("futures_total")
        fut_source = acc.get("futures_total_source", "estimated")
        if fut_total is not None:
            label = "선물 계정 가치" if fut_source == "api" else "선물 계정 가치(추산)"
            lines.append(f"  {label}:    {float(fut_total):,.2f} {settle}")

        # 사용 가능 여유 증거금
        available = acc.get("available")
        if available is not None:
            lines.append(f"  사용 가능 여유 증거금: {float(available):,.2f} {settle}")

        # 포지션 증거금
        iso_margin = acc.get("isolated_position_margin")
        if iso_margin is not None:
            lines.append(f"  포지션 증거금(격리):   {float(iso_margin):,.2f} {settle}")

        # 주문/동결 증거금
        order_margin = ctx.get("order_margin")
        if order_margin is None:
            order_margin = acc.get("order_margin")
        if order_margin is not None and float(order_margin) > 0:
            lines.append(f"  주문 동결 증거금:      {float(order_margin):,.2f} {settle}")

        order_count = ctx.get("open_order_count")
        order_notional = ctx.get("open_order_notional")
        if order_count is not None:
            if int(order_count or 0) > 0:
                lines.append(
                    f"  미체결 주문:           {int(order_count)}개 / 명목 ${float(order_notional or 0):,.2f}"
                )
            else:
                lines.append("  미체결 주문:           없음")

        # 미실현 손익
        upnl = acc.get("unrealised_pnl")
        if upnl is not None:
            lines.append(f"  미실현 손익:           {float(upnl):+,.2f} {settle}")

        gross_lev = ctx.get("account_gross_leverage")
        net_lev = ctx.get("account_net_leverage")
        hedge_ratio = ctx.get("hedge_offset_ratio")
        if gross_lev is not None and net_lev is not None:
            hedge_text = (
                f", 헤지 상쇄 {float(hedge_ratio) * 100:.1f}%"
                if hedge_ratio is not None else ""
            )
            lines.append(
                f"  계좌 실배율:           순노출 {float(net_lev):.2f}x / "
                f"총노출 {float(gross_lev):.2f}x{hedge_text}"
            )
            lines.append(
                f"    - 총노출은 수수료/청산거리 부담, 순노출은 롱숏 상쇄 후 방향성 위험 기준"
            )

    order_err = ctx.get("order_error")
    if order_err:
        lines.append(f"  미체결 주문 조회 실패 — {order_err}")

    # ── 포지션 ────────────────────────────────
    positions = ctx.get("positions")
    pos_err   = ctx.get("position_error")

    if positions is None:
        lines.append(f"  포지션 조회 실패 — {pos_err or '알 수 없는 오류'}")
    elif not positions:
        lines.append("  현재 오픈 포지션: 없음")
    else:
        lines.append(f"  오픈 포지션: {len(positions)}개")
        for p in positions[:4]:
            entry          = p.get("entry_price")
            mark           = p.get("mark_price")
            liq            = p.get("liq_price")
            upnl           = p.get("unrealised_pnl")
            lev            = p.get("leverage")
            size_qty       = p.get("size")
            size_contracts = p.get("size_contracts")
            cs             = p.get("contract_size")
            margin_mode    = p.get("margin_mode", "")
            side           = p.get("side", "")
            actual_lev     = p.get("actual_leverage")
            lev_margin     = p.get("leverage_margin")
            lev_basis      = p.get("actual_leverage_basis")
            notional       = p.get("notional")

            # 사람 읽는 형태: "isolated long 3x, 0.0173 BTC (173계약)"
            actual_str = f"실배율 {float(actual_lev):.2f}x" if actual_lev is not None else "실배율 N/A"
            mode_str = (
                f"{margin_mode} {side.lower()} 설정 {lev}x / {actual_str}"
                if margin_mode and lev else f"{side} 설정 {lev}x / {actual_str}"
            )
            if cs is not None and size_contracts is not None:
                size_str = f"{size_qty} BTC ({size_contracts}계약)"
            else:
                size_str = str(size_qty)

            entry_str = f"진입 ${float(entry):,.2f}" if entry else ""
            mark_str  = f"현재 ${float(mark):,.2f}"  if mark  else ""
            liq_str   = f"청산가 ${float(liq):,.2f}" if liq   else ""
            upnl_str  = f"미실현 ${float(upnl):+,.2f}" if upnl is not None else ""
            notional_str = f"명목 ${float(notional):,.2f}" if notional is not None else ""
            if lev_basis == "isolated_margin":
                margin_label = "격리마진(추가증거금 반영)"
            elif lev_basis == "position_margin":
                margin_label = "포지션마진"
            elif lev_basis == "initial_margin":
                margin_label = "초기마진"
            else:
                margin_label = "기준마진"
            margin_str = f"{margin_label} ${float(lev_margin):,.2f}" if lev_margin is not None else ""
            detail = "  ".join(filter(None, [entry_str, mark_str, liq_str, upnl_str, notional_str, margin_str]))
            lines.append(
                f"    [{p['contract']} {mode_str}]  {size_str}  {detail}"
            )
        if len(positions) > 4:
            lines.append(f"    외 {len(positions) - 4}개 포지션 생략")

    realized_ctx = ctx.get("realized_context") or {}
    realized_summary = realized_ctx.get("summary") or {}
    recent_closes = realized_ctx.get("position_closes") or []
    if realized_summary:
        net = realized_summary.get("net_trading_pnl")
        pnl = realized_summary.get("realized_pnl")
        fees = realized_summary.get("fees")
        self_rebate = realized_summary.get("self_rebate")
        net_fees = realized_summary.get("net_fees")
        funding = realized_summary.get("funding")
        try:
            lines.append(
                "  최근 7일 확정손익: "
                f"순손익 {float(net):+,.2f} {settle} "
                f"(실현 {float(pnl):+,.2f}, 수수료 {float(fees):+,.2f}, "
                f"페이백 {float(self_rebate):+,.2f}, 실질수수료 {float(net_fees):+,.2f}, "
                f"펀딩 {float(funding):+,.2f})"
            )
            if realized_ctx.get("needs_spot_permission"):
                lines.append("  ※ 페이백 조회 권한/응답 문제로 최근 확정손익의 self_rebate가 누락됐을 수 있음")
        except (TypeError, ValueError):
            pass
    if recent_closes:
        lines.append("  최근 청산 이력:")
        for close in recent_closes[:5]:
            contract = close.get("contract") or "N/A"
            side = close.get("side") or "?"
            pnl = close.get("pnl")
            close_price = close.get("close_price")
            entry_price = close.get("entry_price")
            time_label = close.get("time_label") or ""
            parts = [f"{time_label} {contract} {side}"]
            try:
                parts.append(f"확정손익 {float(pnl):+,.2f} {settle}")
            except (TypeError, ValueError):
                pass
            if entry_price is not None:
                try:
                    parts.append(f"진입 ${float(entry_price):,.2f}")
                except (TypeError, ValueError):
                    pass
            if close_price is not None:
                try:
                    parts.append(f"청산 ${float(close_price):,.2f}")
                except (TypeError, ValueError):
                    pass
            lines.append("    - " + " / ".join(parts))

    return "\n".join(lines)


# =============================================
# 공통 진입점 (provider 분기)
# =============================================

def fetch_account_context(symbol: Optional[str] = None) -> dict:
    """ACCOUNT_PROVIDER 설정에 따라 Binance 또는 Gate.io 계좌 컨텍스트를 반환.

    - ACCOUNT_FEATURES_ENABLED=0 또는 ACCOUNT_PROVIDER=none → 빈 컨텍스트
    - ACCOUNT_PROVIDER=gateio → Gate.io read-only 조회
    - 그 외 → Binance (기존 동작)
    """
    if not _cfg.ACCOUNT_FEATURES_ENABLED:
        return {"provider": "disabled", "disabled": True}

    provider = _cfg.ACCOUNT_PROVIDER
    if provider == "none":
        return {"provider": "none", "disabled": True}
    if provider == "gateio":
        return _fetch_gate_account_context()
    # 기본: binance
    return _fetch_binance_account_context(symbol)


def format_account_context(ctx: dict) -> str:
    """계좌 컨텍스트 dict를 LLM 프롬프트용 텍스트로 변환.

    API key, secret, sign, headers는 절대 포함하지 않는다.
    """
    if ctx.get("disabled"):
        reason = ctx.get("disabled_reason", "")
        if reason:
            return f"[계좌 / 리스크 제약]\n  계좌 연동 비활성 — {reason}"
        provider = ctx.get("provider", "")
        if provider == "none":
            return "[계좌 / 리스크 제약]\n  계좌 연동 비활성 (ACCOUNT_PROVIDER=none)"
        return "[계좌 / 리스크 제약]\n  계좌 연동 비활성"

    provider = ctx.get("provider", "")
    if provider == "gateio":
        return _format_gate_account_context(ctx)
    return _format_binance_account_context(ctx)
