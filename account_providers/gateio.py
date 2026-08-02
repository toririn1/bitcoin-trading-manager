# =============================================
# Gate.io Futures — Read-Only 계좌/포지션 조회
# =============================================
# 지원 메서드: GET 전용
# 허용 엔드포인트:
#   GET /wallet/total_balance          전체 계정 총자산
#   GET /futures/{settle}/accounts     선물 계정 잔고
#   GET /futures/{settle}/positions    선물 포지션
#   GET /futures/{settle}/orders       미체결 주문
#
# ⛔ POST / PUT / DELETE 구현 없음
# ⛔ 주문 생성 / 취소 / 레버리지 변경 endpoint 없음
# =============================================
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any

import requests

_logger = logging.getLogger(__name__)

# DEBUG_ACCOUNT_FIELDS=1 이면 필드명+타입만 로깅 (값 전체 출력 금지)
_DEBUG_FIELDS = os.getenv("DEBUG_ACCOUNT_FIELDS", "0") not in ("0", "false", "no")

_session = requests.Session()
_session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})


def _account_book_dedup_key(item: dict) -> tuple:
    item_id = item.get("id")
    if item_id not in (None, ""):
        return ("id", str(item_id))
    return (
        "fallback",
        str(item.get("time") or ""),
        str(item.get("type") or ""),
        str(item.get("change") or ""),
        str(item.get("balance") or ""),
        str(item.get("text") or ""),
    )


def _dedupe_account_book_items(items: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    result: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _account_book_dedup_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


# ─────────────────────────────────────────────
# 서명
# ─────────────────────────────────────────────
def _sign_request(
    api_key: str, api_secret: str,
    method: str, url: str,
    query_string: str = "", body: str = "",
) -> dict:
    """Gate API v4 HMAC-SHA512 서명 헤더 생성.
    api_key / api_secret 값은 반환 헤더에 포함되지 않는다.
    """
    from urllib.parse import urlparse
    ts = str(int(time.time()))
    path = urlparse(url).path
    body_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
    sign_str = "\n".join([method.upper(), path, query_string, body_hash, ts])
    signature = hmac.new(
        api_secret.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    return {"KEY": api_key, "Timestamp": ts, "SIGN": signature}


# ─────────────────────────────────────────────
# GET 요청 (유일한 HTTP 메서드)
# ─────────────────────────────────────────────
def _get(base_url: str, api_key: str, api_secret: str, path: str, params: dict | None = None) -> Any:
    """Gate API v4 GET 요청. POST/PUT/DELETE는 구현하지 않는다."""
    from urllib.parse import urlencode
    query_string = urlencode(params or {})
    url = f"{base_url.rstrip('/')}{path}"
    full_url = f"{url}?{query_string}" if query_string else url
    headers = _sign_request(api_key, api_secret, "GET", url, query_string, "")
    resp = _session.get(full_url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if _DEBUG_FIELDS:
        _log_fields_only(path, data)
    return data


def _log_fields_only(path: str, data: Any) -> None:
    """DEBUG_ACCOUNT_FIELDS=1 일 때 필드명+타입만 로깅. 값은 절대 출력하지 않는다."""
    if isinstance(data, dict):
        field_info = {k: type(v).__name__ for k in data}
        _logger.debug("[gate-fields] %s -> %s", path, field_info)
    elif isinstance(data, list) and data:
        field_info = {k: type(v).__name__ for k in data[0]}
        _logger.debug("[gate-fields] %s (list[0]) -> %s", path, field_info)


# ─────────────────────────────────────────────
# 개별 endpoint
# ─────────────────────────────────────────────
def fetch_wallet_total_balance(base_url: str, api_key: str, api_secret: str) -> dict:
    """GET /wallet/total_balance — 전체 계정 총자산."""
    return _get(base_url, api_key, api_secret, "/wallet/total_balance")


def fetch_futures_account(base_url: str, api_key: str, api_secret: str, settle: str) -> dict:
    """GET /futures/{settle}/accounts — 선물 계정 잔고."""
    return _get(base_url, api_key, api_secret, f"/futures/{settle}/accounts")


def fetch_futures_positions(base_url: str, api_key: str, api_secret: str, settle: str) -> list:
    """GET /futures/{settle}/positions — 선물 포지션 전체."""
    return _get(base_url, api_key, api_secret, f"/futures/{settle}/positions")


def fetch_futures_open_orders(base_url: str, api_key: str, api_secret: str, settle: str) -> list:
    """GET /futures/{settle}/orders?status=open — 미체결 선물 주문."""
    return _get(
        base_url,
        api_key,
        api_secret,
        f"/futures/{settle}/orders",
        {"status": "open", "limit": 100},
    )


def fetch_account_book(
    base_url: str, api_key: str, api_secret: str, settle: str,
    from_ts: int | None = None, to_ts: int | None = None,
    limit: int = 1000,
) -> list:
    """GET /futures/{settle}/account_book — 잔고 변경 이벤트 로그.

    type 종류: pnl(실현손익), fee(수수료), fund(펀딩비),
               point_fee, pv_dnw 등
    from_ts / to_ts: Unix timestamp (초 단위)
    """
    params: dict = {"limit": min(limit, 1000)}
    if from_ts is not None:
        params["from"] = int(from_ts)
    if to_ts is not None:
        params["to"] = int(to_ts)
    return _get(base_url, api_key, api_secret, f"/futures/{settle}/account_book", params)


def aggregate_daily_pnl(
    base_url: str, api_key: str, api_secret: str, settle: str,
    days: int = 30,
    include_point_fee: bool = False,
) -> list[dict]:
    """account_book 기반으로 일별 실현손익을 집계해 반환.

    반환 형식:
    [
      {
        "date": "2026-07-01",          # KST 기준 날짜 (UTC+9)
        "realized_pnl":  14.52,        # pnl 합계
        "fee":           -8.63,        # fee 합계
        "funding":        0.08,        # fund 합계
        "point_fee":     -0.01,        # point_fee (include_point_fee=True 시)
        "daily_total":    5.97,        # pnl + fee + fund [+ point_fee]
      },
      ...
    ]
    날짜 오름차순 정렬. key/secret/sign은 포함하지 않는다.
    """
    import time as _time
    from datetime import datetime, timezone, timedelta

    now_ts  = int(_time.time())
    from_ts = now_ts - days * 86400

    # 1000건 한계 → 초과 시 페이지네이션
    all_items: list[dict] = []
    cur_from = from_ts
    while True:
        page = fetch_account_book(base_url, api_key, api_secret, settle,
                                  from_ts=cur_from, to_ts=now_ts, limit=1000)
        if not page:
            break
        all_items.extend(page)
        before_dedupe = len(all_items)
        all_items = _dedupe_account_book_items(all_items)
        if len(page) < 1000:
            break
        if len(all_items) == before_dedupe - len(page):
            break
        # 마지막 항목 time + 1 로 다음 페이지
        try:
            max_page_time = max(int(float(item["time"])) for item in page if item.get("time") is not None)
            next_from = max_page_time + 1
        except (KeyError, TypeError, ValueError):
            break
        if next_from <= cur_from:
            break
        cur_from = next_from

    # KST(UTC+9) 기준 일별 집계
    KST = timezone(timedelta(hours=9))
    daily: dict[str, dict] = {}
    for item in all_items:
        t = item.get("type", "")
        if t not in ("pnl", "fee", "fund") and not (include_point_fee and t == "point_fee"):
            continue
        try:
            ts  = float(item["time"])
            val = float(item.get("change") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        day = datetime.fromtimestamp(ts, tz=KST).strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"date": day, "realized_pnl": 0.0, "fee": 0.0,
                          "funding": 0.0, "point_fee": 0.0, "daily_total": 0.0}
        if t == "pnl":
            daily[day]["realized_pnl"] += val
        elif t == "fee":
            daily[day]["fee"] += val
        elif t == "fund":
            daily[day]["funding"] += val
        elif t == "point_fee":
            daily[day]["point_fee"] += val

    result = []
    for day_key in sorted(daily.keys()):
        d = daily[day_key]
        total = d["realized_pnl"] + d["fee"] + d["funding"]
        if include_point_fee:
            total += d["point_fee"]
        d["daily_total"] = round(total, 6)
        d["realized_pnl"] = round(d["realized_pnl"], 6)
        d["fee"]          = round(d["fee"], 6)
        d["funding"]      = round(d["funding"], 6)
        d["point_fee"]    = round(d["point_fee"], 6)
        result.append(d)
    return result


# ─────────────────────────────────────────────
# 계약 크기 테이블 (1계약 = N 기초자산)
# ─────────────────────────────────────────────
_CONTRACT_SIZE: dict[str, float] = {
    "BTC_USDT": 0.0001,
    "ETH_USDT": 0.001,
    "SOL_USDT": 0.01,
    "BNB_USDT": 0.01,
    "XRP_USDT": 10.0,
    "DOGE_USDT": 100.0,
    "ADA_USDT": 10.0,
    "AVAX_USDT": 0.1,
    "LTC_USDT": 0.1,
}
_DEFAULT_CONTRACT_SIZE = 1.0


def _contract_size(contract: str) -> float:
    return _CONTRACT_SIZE.get(contract, _DEFAULT_CONTRACT_SIZE)


# ─────────────────────────────────────────────
# 정제 함수
# ─────────────────────────────────────────────
def _extract_wallet_total(raw: dict) -> dict:
    """GET /wallet/total_balance 응답에서 전체 계정 총자산만 추출.
    key/secret/sign/header는 포함하지 않는다.
    """
    total_raw = raw.get("total") or {}

    def _f(key: str) -> float | None:
        val = total_raw.get(key)
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    # details.futures: 선물 계정 금액만 따로 보관
    details = raw.get("details") or {}
    futures_raw = details.get("futures") or {}
    spot_raw    = details.get("spot") or {}

    def _fd(d: dict, key: str) -> float | None:
        val = d.get(key)
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "total_amount":       _f("amount"),         # 전체 계정 총자산 (USDT 환산)
        "total_unrealised":   _f("unrealised_pnl"),
        "total_currency":     total_raw.get("currency", "USDT"),
        "futures_amount":     _fd(futures_raw, "amount"),
        "futures_unrealised": _fd(futures_raw, "unrealised_pnl"),
        "spot_amount":        _fd(spot_raw, "amount"),
    }


def _extract_account_fields(raw: dict) -> dict:
    """GET /futures/{settle}/accounts 응답에서 필요한 필드만 추출.

    - available:             사용 가능 여유 증거금
    - isolated_pos_margin:   격리 포지션 증거금
    - order_margin:          주문 동결 증거금
    - unrealised_pnl:        미실현 손익
    total 필드는 dualmodo/isolated에서 0으로 오는 경우 있음 → 역산 처리.
    key/secret/sign/header는 포함하지 않는다.
    """
    def _f(key: str) -> float | None:
        val = raw.get(key)
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    available           = _f("available")
    unrealised_pnl      = _f("unrealised_pnl")
    order_margin        = _f("order_margin")
    position_margin     = _f("position_margin")
    isolated_pos_margin = _f("isolated_position_margin")

    # total이 유효하면 사용, 0이면 역산
    raw_total = _f("total")
    if raw_total:
        futures_total = raw_total
        total_source  = "api"
    else:
        # available + 격리포지션증거금 + 주문증거금 + 미실현손익
        parts = [
            available           or 0.0,
            isolated_pos_margin or (position_margin or 0.0),
            order_margin        or 0.0,
            unrealised_pnl      or 0.0,
        ]
        futures_total = sum(parts) if available is not None else None
        total_source  = "estimated"  # 역산임을 명시

    # 사용 중 마진: 격리 포지션 증거금 우선, 없으면 position_margin
    margin_used = (
        isolated_pos_margin if isolated_pos_margin is not None
        else (position_margin or 0.0) if position_margin is not None
        else None
    )

    return {
        "futures_total":        futures_total,   # 선물 계정 가치 (역산 가능)
        "futures_total_source": total_source,    # "api" | "estimated"
        "available":            available,        # 사용 가능 여유 증거금
        "unrealised_pnl":       unrealised_pnl,
        "order_margin":         order_margin,     # 주문 동결 증거금
        "position_margin":      position_margin,
        "isolated_position_margin": isolated_pos_margin,  # 격리 포지션 증거금
        "margin_used":          margin_used,
        "currency":             raw.get("currency", "USDT"),
        "in_dual_mode":         bool(raw.get("in_dual_mode", False)),
        "margin_mode_name":     raw.get("margin_mode_name", ""),
    }


def _margin_mode_from_position(pos: dict) -> str:
    """포지션 raw에서 마진 모드(isolated/cross)를 추출.
    pos_margin_mode 필드를 우선 사용, 없으면 initial_margin 존재 여부로 추정.
    """
    pmm = str(pos.get("pos_margin_mode") or "").lower()
    if pmm in ("isolated", "cross"):
        return pmm
    # 추정: isolated_position_margin > 0 이면 isolated
    margin = pos.get("margin")
    try:
        if margin and float(margin) > 0:
            return "isolated(추정)"
    except (TypeError, ValueError):
        pass
    return "unknown"


def _extract_positions(raw_list: list) -> list:
    """Gate /futures/{settle}/positions 응답에서 오픈 포지션만 추출·정제.

    size == 0 이면 오픈 포지션 아님 → 제외.
    size(계약수) → 실제 수량(BTC 등) 변환.
    mode(dual_long/dual_short/single)과 margin_mode(isolated/cross) 분리.
    key/secret/sign/header는 포함하지 않는다.
    """
    if not isinstance(raw_list, list):
        return []

    result = []
    for p in raw_list:
        try:
            size_contracts = float(p.get("size") or 0)
        except (TypeError, ValueError):
            size_contracts = 0.0
        if size_contracts == 0:
            continue

        def _f(key: str) -> float | None:
            val = p.get(key)
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        contract  = p.get("contract", "")
        cs        = _contract_size(contract)
        size_qty  = round(abs(size_contracts) * cs, 8)

        # mode: dual_long / dual_short / single → 방향(side)과 포지션사이드(pos_side) 분리
        mode_raw  = str(p.get("mode") or "").lower()
        if mode_raw == "dual_long":
            side     = "롱"
            pos_side = "dual_long"
        elif mode_raw == "dual_short":
            side     = "숏"
            pos_side = "dual_short"
        else:
            side     = "롱" if size_contracts > 0 else "숏"
            pos_side = "single"

        margin_mode = _margin_mode_from_position(p)
        mark_price = _f("mark_price")
        margin = _f("margin")
        initial_margin = _f("initial_margin")
        notional = abs(size_qty * mark_price) if mark_price is not None else None
        if margin_mode.startswith("isolated") and margin and margin > 0:
            # Isolated margin includes manually added/reduced position margin, so this
            # is the best denominator for real leverage after collateral adjustments.
            leverage_margin = margin
            actual_leverage_basis = "isolated_margin"
        elif margin and margin > 0:
            leverage_margin = margin
            actual_leverage_basis = "position_margin"
        else:
            leverage_margin = initial_margin
            actual_leverage_basis = "initial_margin" if initial_margin else None
        actual_leverage = (
            notional / leverage_margin
            if notional is not None and leverage_margin and leverage_margin > 0
            else None
        )

        result.append({
            "contract":          contract,
            "side":              side,           # 롱 / 숏
            "pos_side":          pos_side,       # dual_long / dual_short / single
            "margin_mode":       margin_mode,    # isolated / cross / isolated(추정)
            "size":              size_qty,       # 실제 수량 (BTC 등)
            "size_contracts":    int(abs(size_contracts)),
            "contract_size":     cs,
            "leverage":          p.get("leverage"),
            "entry_price":       _f("entry_price"),
            "mark_price":        mark_price,
            "liq_price":         _f("liq_price"),
            "notional":          round(notional, 6) if notional is not None else None,
            "margin":            margin,
            "initial_margin":    initial_margin,
            "leverage_margin":   leverage_margin,
            "actual_leverage_basis": actual_leverage_basis,
            "actual_leverage":   round(actual_leverage, 4) if actual_leverage is not None else None,
            "maintenance_margin": _f("maintenance_margin"),
            "unrealised_pnl":    _f("unrealised_pnl"),
            "realised_pnl":      _f("realised_pnl"),
        })
    return result


def _extract_open_orders(raw_list: list) -> list:
    """Gate /futures/{settle}/orders 응답에서 미체결 주문 리스크만 정제."""
    if not isinstance(raw_list, list):
        return []

    result = []
    for order in raw_list:
        if not isinstance(order, dict):
            continue

        def _f(key: str) -> float | None:
            val = order.get(key)
            try:
                return float(val) if val not in (None, "") else None
            except (TypeError, ValueError):
                return None

        contract = str(order.get("contract") or "")
        signed_size_contracts = _f("size")
        if signed_size_contracts is None:
            signed_size_contracts = _f("left") or _f("amount") or 0.0
        price = _f("price") or _f("trigger_price") or _f("stop_price") or 0.0
        cs = _contract_size(contract)
        qty = abs(signed_size_contracts) * cs
        notional = abs(qty * price) if price > 0 else None
        raw_side = str(order.get("side") or "").upper()
        if raw_side not in {"BUY", "SELL"}:
            raw_side = "BUY" if signed_size_contracts > 0 else "SELL" if signed_size_contracts < 0 else ""
        order_type = str(order.get("type") or order.get("price_type") or order.get("tif") or "limit")
        result.append({
            "symbol": contract,
            "contract": contract,
            "order_id": order.get("id"),
            "type": order_type.upper(),
            "side": raw_side,
            "price": price if price > 0 else None,
            "qty": round(qty, 8),
            "size_contracts": abs(signed_size_contracts),
            "signed_size_contracts": signed_size_contracts,
            "notional": round(notional, 6) if notional is not None else None,
            "reduce_only": bool(order.get("reduce_only", order.get("is_reduce_only", False))),
            "is_close": bool(order.get("is_close", False)),
            "margin_mode": str(order.get("pos_margin_mode") or ""),
            "leverage": order.get("leverage"),
            "status": str(order.get("status") or "open"),
            "text": str(order.get("text") or ""),
        })
    result.sort(key=lambda item: item.get("notional") or 0.0, reverse=True)
    return result


# ─────────────────────────────────────────────
# 통합 조회
# ─────────────────────────────────────────────
def fetch_gate_account_context(
    base_url: str, api_key: str, api_secret: str, settle: str,
) -> dict:
    """Gate.io 전체 계정 총자산 + 선물 잔고 + 포지션을 수집해 정제된 context dict 반환.

    - 조회 실패 시 앱을 종료하지 않고 error 필드로 반환
    - API key / secret / 서명 / 헤더 / raw response는 반환값에 포함하지 않는다
    """
    ctx: dict = {
        "provider":       "gateio",
        "settle":         settle,
        "wallet":         None,   # /wallet/total_balance 결과
        "account":        None,   # /futures/{settle}/accounts 결과
        "positions":      None,
        "open_orders":    [],
        "wallet_error":   None,
        "account_error":  None,
        "position_error": None,
        "order_error":    None,
    }

    # ── 전체 계정 총자산 (/wallet/total_balance) ──
    try:
        raw_wallet = fetch_wallet_total_balance(base_url, api_key, api_secret)
        ctx["wallet"] = _extract_wallet_total(raw_wallet)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        ctx["wallet_error"] = f"Gate.io 전체 잔고 조회 실패 (HTTP {status})"
        _logger.warning("Gate wallet fetch error: HTTP %s", status)
    except Exception as exc:
        ctx["wallet_error"] = "Gate.io 전체 잔고 조회 실패"
        _logger.warning("Gate wallet fetch error: %s", type(exc).__name__)

    # ── 선물 계정 잔고 ────────────────────────────
    try:
        raw_acc = fetch_futures_account(base_url, api_key, api_secret, settle)
        ctx["account"] = _extract_account_fields(raw_acc)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        ctx["account_error"] = f"Gate.io 선물 계좌 조회 실패 (HTTP {status})"
        _logger.warning("Gate account fetch error: HTTP %s", status)
    except Exception as exc:
        ctx["account_error"] = "Gate.io 선물 계좌 조회 실패"
        _logger.warning("Gate account fetch error: %s", type(exc).__name__)

    # ── 포지션 ────────────────────────────────────
    try:
        raw_positions = fetch_futures_positions(base_url, api_key, api_secret, settle)
        ctx["positions"] = _extract_positions(raw_positions)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        ctx["position_error"] = f"Gate.io 포지션 조회 실패 (HTTP {status})"
        _logger.warning("Gate position fetch error: HTTP %s", status)
    except Exception as exc:
        ctx["position_error"] = "Gate.io 포지션 조회 실패"
        _logger.warning("Gate position fetch error: %s", type(exc).__name__)

    # ── 미체결 주문 ───────────────────────────────
    try:
        raw_orders = fetch_futures_open_orders(base_url, api_key, api_secret, settle)
        ctx["open_orders"] = _extract_open_orders(raw_orders)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        ctx["order_error"] = f"Gate.io 미체결 주문 조회 실패 (HTTP {status})"
        _logger.warning("Gate open orders fetch error: HTTP %s", status)
    except Exception as exc:
        ctx["order_error"] = "Gate.io 미체결 주문 조회 실패"
        _logger.warning("Gate open orders fetch error: %s", type(exc).__name__)

    return ctx


# ─────────────────────────────────────────────
# 실제 손익표용 API
# ─────────────────────────────────────────────

def fetch_position_close(
    base_url: str, api_key: str, api_secret: str, settle: str,
    from_ts: int | None = None, to_ts: int | None = None,
    contract: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list:
    """GET /futures/{settle}/position_close — 포지션 종료 이력.

    참고용 전용: 실현손익 계산은 account_book(pnl) 기준으로 해야 한다.
    이 API는 상세 참고용이므로 중복 합산하지 않는다.
    key/secret/sign/header는 반환값에 포함하지 않는다.
    """
    params: dict = {"limit": min(limit, 1000), "offset": offset}
    if from_ts is not None:
        params["from"] = int(from_ts)
    if to_ts is not None:
        params["to"] = int(to_ts)
    if contract:
        params["contract"] = contract
    return _get(base_url, api_key, api_secret, f"/futures/{settle}/position_close", params)


def get_spot_account_book(
    base_url: str, api_key: str, api_secret: str,
    currency: str = "USDT",
    code: str | None = None,
    from_ts: int | None = None,
    to_ts: int | None = None,
    account: str | None = None,
    limit_per_page: int = 1000,
    max_pages: int = 100,
) -> tuple[list[dict], bool, dict]:
    """GET /spot/account_book — spot 계정 잔고 변경 내역 (from/to + page 방식).

    주의:
    - spot/account_book의 time 필드는 초(sec) 단위 (ms가 아님 — 실제 응답 확인).
    - from/to 파라미터 지원 (API 문서 확인: currency, from, to, page, limit, code 모두 지원).
    - Record query time range cannot exceed 30 days.
    - code=3341 로 Affiliate Ultra Commission Self-Rebate만 조회 가능.
    - account=unified 로 unified 계좌 장부 조회 시도.
    - spot 권한이 없으면 빈 리스트 + needs_spot_permission=True 반환.
    - key/secret/sign/header는 반환값에 포함하지 않는다.

    반환: (items, needs_spot_permission, diag)
    diag: {"pages_fetched", "stopped_reason", "endpoint", "params_safe"}
    """
    params: dict = {"currency": currency, "limit": min(limit_per_page, 1000)}
    if code is not None:
        params["code"] = str(code)
    if from_ts is not None:
        params["from"] = int(from_ts)
    if to_ts is not None:
        params["to"] = int(to_ts)
    if account is not None:
        params["account"] = account

    # 진단용: params에서 민감 정보 없는 것만
    params_safe = {k: v for k, v in params.items()
                   if k in ("currency", "from", "to", "limit", "code", "account", "page")}

    all_items: list[dict] = []
    pages_fetched = 0
    stopped_reason = "empty_page"

    for page in range(1, max_pages + 1):
        params["page"] = page
        params_safe["page"] = page
        try:
            page_items = _get(base_url, api_key, api_secret, "/spot/account_book", dict(params))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (401, 403):
                diag = {"pages_fetched": pages_fetched, "stopped_reason": f"http_{status}_no_permission",
                        "endpoint": "/spot/account_book", "params_safe": dict(params_safe)}
                return all_items, True, diag
            # 400: from/to 파라미터 오류 가능성 → 재시도하지 않고 상위로 전파
            stopped_reason = f"http_error_{status}"
            break
        pages_fetched += 1
        if not page_items:
            stopped_reason = "empty_page"
            break
        all_items.extend(page_items)
        if len(page_items) < params["limit"]:
            stopped_reason = "partial_page_end"
            break
        # limit * (page - 1) <= 100000 제한
        if params["limit"] * page >= 100000:
            stopped_reason = "page_limit_100k"
            break
    else:
        stopped_reason = "max_pages_reached"

    diag = {"pages_fetched": pages_fetched, "stopped_reason": stopped_reason,
            "endpoint": "/spot/account_book", "params_safe": dict(params_safe)}
    return all_items, False, diag


def get_affiliate_self_rebates(
    base_url: str, api_key: str, api_secret: str,
    days: int = 30,
) -> tuple[list[dict], bool, list[dict]]:
    """Affiliate Ultra Commission Self-Rebate (code=3341) 내역 조회.

    조회 전략:
    1. /spot/account_book?code=3341&from=<days_ago>&to=<now> (기본 spot)
    2. 결과가 비거나, 이상하면 account=unified 로도 시도
    3. 두 결과를 id 기준으로 dedup하여 합산

    time 필드는 sec 단위 (ms가 아님 — 실제 API 확인)
    days 범위 필터는 from/to로 서버 측에서 처리.

    반환: (items, needs_spot_permission, diagnostics_list)
    diagnostics_list: 각 시도별 진단 딕셔너리 목록
    items 각 항목:
      id, time_sec, change(float), balance(float), type, code, text, source, account_mode
    """
    import time as _time
    from datetime import datetime, timezone, timedelta

    now_ts   = int(_time.time())
    from_ts  = now_ts - days * 86400

    diagnostics: list[dict] = []
    all_rebates: list[dict] = []
    needs_perm = False
    seen_ids: set[str] = set()

    def _try_fetch(account_mode: str | None) -> tuple[list[dict], bool, dict]:
        """account_mode: None=classic spot, "unified"=unified"""
        raw, no_perm, diag = get_spot_account_book(
            base_url, api_key, api_secret,
            currency="USDT", code="3341",
            from_ts=from_ts, to_ts=now_ts,
            account=account_mode,
            limit_per_page=1000,
        )
        # 진단에 account_mode 추가
        diag["account_mode"] = account_mode or "classic"
        diag["raw_count"] = len(raw)

        if no_perm:
            diag["result"] = "no_permission"
            return [], True, diag

        # 정제
        result: list[dict] = []
        total_sum = 0.0
        min_t = max_t = None
        for item in raw:
            try:
                t_sec = int(float(item.get("time") or 0))
                # time이 ms 단위인지 sec 단위인지 자동 판별
                # 2020-01-01 = 1577836800 (10자리), ms면 13자리
                if t_sec > 9_999_999_999:
                    t_sec = t_sec // 1000
            except (TypeError, ValueError):
                continue
            try:
                change = float(item.get("change") or 0)
                bal    = float(item.get("balance") or 0)
            except (TypeError, ValueError):
                change, bal = 0.0, 0.0

            item_id = str(item.get("id") or "")
            dedup_key = item_id if item_id else f"{t_sec}_{change}"

            result.append({
                "id":           item_id,
                "_dedup_key":   dedup_key,
                "time_sec":     t_sec,
                "change":       round(change, 6),
                "balance":      round(bal, 6),
                "type":         str(item.get("type") or "pu_rebate"),
                "code":         str(item.get("code") or "3341"),
                "text":         str(item.get("text") or ""),
                "source":       "spot_account_book",
                "account_mode": account_mode or "classic",
            })
            total_sum += change
            if min_t is None or t_sec < min_t:
                min_t = t_sec
            if max_t is None or t_sec > max_t:
                max_t = t_sec

        diag["filtered_count"] = len(result)
        diag["sum"] = round(total_sum, 6)
        diag["min_time_sec"] = min_t
        diag["max_time_sec"] = max_t
        diag["result"] = "ok" if result else "empty"
        return result, False, diag

    # --- 시도 1: classic spot ---
    items_classic, no_perm_classic, diag_classic = _try_fetch(None)
    diag_classic["selected_for_pnl"] = False
    diag_classic["skipped_reason"]   = ""
    diagnostics.append(diag_classic)

    if no_perm_classic:
        needs_perm = True
    else:
        for item in items_classic:
            k = item.pop("_dedup_key")
            if k not in seen_ids:
                seen_ids.add(k)
                all_rebates.append(item)

    # --- 시도 2: unified ---
    items_unified, no_perm_unified, diag_unified = _try_fetch("unified")
    diag_unified["selected_for_pnl"] = False
    diag_unified["skipped_reason"]   = ""
    diagnostics.append(diag_unified)

    # classic/unified 결과가 동일하면 unified는 중복으로 skip
    classic_sum   = diag_classic.get("sum", 0.0)
    classic_count = diag_classic.get("filtered_count", 0)
    unified_sum   = diag_unified.get("sum", 0.0)
    unified_count = diag_unified.get("filtered_count", 0)
    is_duplicate  = (
        not no_perm_unified
        and not no_perm_classic
        and classic_count == unified_count
        and abs(classic_sum - unified_sum) < 0.000001
    )

    if is_duplicate:
        diag_unified["skipped_reason"] = "duplicate_of_classic"
        # classic을 선택
        diag_classic["selected_for_pnl"] = True if classic_count > 0 else False
    elif no_perm_unified:
        diag_unified["skipped_reason"] = "no_permission"
        diag_classic["selected_for_pnl"] = True if classic_count > 0 else False
    else:
        # classic+unified 모두 독립 데이터가 있을 때 합산 (dedup 기준)
        diag_classic["selected_for_pnl"] = True if classic_count > 0 else False
        if not no_perm_unified:
            new_items = 0
            for item in items_unified:
                k = item.pop("_dedup_key")
                if k not in seen_ids:
                    seen_ids.add(k)
                    item["source"] = "spot_account_book_unified"
                    all_rebates.append(item)
                    new_items += 1
            diag_unified["selected_for_pnl"] = new_items > 0
            if new_items == 0:
                diag_unified["skipped_reason"] = "all_deduplicated"

    # selected_self_rebate_source 기록 (진단용)
    selected_sources = [
        d.get("account_mode", "classic")
        for d in diagnostics
        if d.get("selected_for_pnl")
    ]
    duplicate_skipped = [
        d.get("account_mode", "?")
        for d in diagnostics
        if d.get("skipped_reason") in ("duplicate_of_classic", "all_deduplicated")
    ]

    for d in diagnostics:
        d["selected_self_rebate_source"] = selected_sources
        d["duplicate_sources_skipped"]   = duplicate_skipped

    # 오름차순 정렬
    all_rebates.sort(key=lambda x: x["time_sec"])
    return all_rebates, needs_perm, diagnostics


def _paginate_account_book(
    base_url: str, api_key: str, api_secret: str, settle: str,
    from_ts: int, to_ts: int,
    type_filter: str | None = None,
) -> list[dict]:
    """account_book 전체 페이지네이션 수집.

    type_filter: None이면 전체, 값 지정 시 해당 type만 조회.
    """
    all_items: list[dict] = []
    cur_from = from_ts
    params_base: dict = {}
    if type_filter:
        params_base["type"] = type_filter

    while True:
        page = fetch_account_book(
            base_url, api_key, api_secret, settle,
            from_ts=cur_from, to_ts=to_ts, limit=1000,
        )
        if not page:
            break
        # type_filter가 있으면 서버 응답에서도 다시 필터 (API 보장 위해)
        if type_filter:
            page = [item for item in page if item.get("type") == type_filter]
        before_count = len(all_items)
        all_items.extend(page)
        all_items = _dedupe_account_book_items(all_items)
        new_count = len(all_items) - before_count
        if len(page) < 1000:
            break
        if new_count <= 0:
            break
        try:
            max_page_time = max(int(float(item["time"])) for item in page if item.get("time") is not None)
            next_from = max_page_time + 1
        except (KeyError, TypeError, ValueError):
            break
        if next_from <= cur_from:
            break
        cur_from = next_from
        if cur_from >= to_ts:
            break
    return all_items


def _paginate_position_close(
    base_url: str, api_key: str, api_secret: str, settle: str,
    from_ts: int, to_ts: int,
    contract: str | None = None,
) -> list[dict]:
    """position_close 전체 페이지네이션 수집 (최대 1000건)."""
    all_items: list[dict] = []
    offset = 0
    while True:
        page = fetch_position_close(
            base_url, api_key, api_secret, settle,
            from_ts=from_ts, to_ts=to_ts,
            contract=contract,
            limit=100, offset=offset,
        )
        if not page:
            break
        all_items.extend(page)
        if len(page) < 100 or len(all_items) >= 1000:
            break
        offset += len(page)
    return all_items


def _first_float(item: dict, *keys: str) -> float | None:
    for key in keys:
        val = item.get(key)
        try:
            if val is not None and val != "":
                return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _clean_position_close(item: dict) -> dict:
    from datetime import datetime, timezone, timedelta

    KST = timezone(timedelta(hours=9))
    try:
        ts = int(float(item.get("time") or item.get("close_time") or item.get("finish_time") or 0))
    except (TypeError, ValueError):
        ts = 0
    pnl = _first_float(item, "pnl", "realised_pnl", "realized_pnl", "profit")
    side_raw = str(item.get("side") or item.get("text") or "").lower()
    side = "long" if side_raw in ("long", "롱") or "long" in side_raw else (
           "short" if side_raw in ("short", "숏") or "short" in side_raw else side_raw)

    long_price = _first_float(item, "long_price")
    short_price = _first_float(item, "short_price")
    close_price = _first_float(
        item,
        "close_price", "settle_price", "price", "mark_price", "finish_price",
    )
    entry_price = _first_float(
        item,
        "entry_price", "open_price", "avg_entry_price", "avg_open_price",
    )
    if close_price is None and entry_price is None:
        if side == "long":
            entry_price = long_price
            close_price = short_price
        elif side == "short":
            entry_price = short_price
            close_price = long_price

    return {
        "time":        ts,
        "time_label":  datetime.fromtimestamp(ts, tz=KST).strftime("%m-%d %H:%M") if ts else "",
        "contract":    str(item.get("contract") or ""),
        "side":        side,
        "pnl":         round(pnl or 0.0, 6),
        "entry_price": round(entry_price, 6) if entry_price is not None else None,
        "close_price": round(close_price, 6) if close_price is not None else None,
        "size":        _first_float(item, "size", "close_size", "contracts"),
        "text":        str(item.get("text") or ""),
    }


def get_recent_realized_context(
    base_url: str, api_key: str, api_secret: str, settle: str,
    days: int = 7,
    spot_api_key: str = "",
    spot_api_secret: str = "",
) -> dict:
    """최근 확정손익/청산 이력을 에이전트 컨텍스트용으로 요약한다."""
    import time as _time
    from datetime import datetime, timezone, timedelta

    KST = timezone(timedelta(hours=9))
    days = max(1, min(int(days), 30))
    to_ts = int(_time.time())
    from_ts = to_ts - days * 86400

    ledger = _paginate_account_book(base_url, api_key, api_secret, settle, from_ts, to_ts)
    summary = {
        "realized_pnl": 0.0,
        "fees": 0.0,
        "funding": 0.0,
        "futures_rebates": 0.0,
        "self_rebate": 0.0,
        "net_fees": 0.0,
        "net_trading_pnl": 0.0,
    }
    daily_rebate: dict[str, float] = {}
    for item in ledger:
        try:
            val = float(item.get("change") or 0)
        except (TypeError, ValueError):
            continue
        t = str(item.get("type") or "")
        if t == "pnl":
            summary["realized_pnl"] += val
        elif t == "fee":
            summary["fees"] += val
        elif t == "fund":
            summary["funding"] += val
        elif t == "refr":
            summary["futures_rebates"] += val

    needs_spot_permission = False
    try:
        rebate_items, needs_spot_permission, _ = get_affiliate_self_rebates(
            base_url,
            spot_api_key or api_key,
            spot_api_secret or api_secret,
            days=days,
        )
        for item in rebate_items:
            val = float(item.get("change") or 0.0)
            summary["self_rebate"] += val
            t_sec = int(item.get("time_sec") or 0)
            if t_sec:
                day = datetime.fromtimestamp(t_sec, tz=KST).strftime("%Y-%m-%d")
                daily_rebate[day] = daily_rebate.get(day, 0.0) + val
    except Exception:
        needs_spot_permission = True

    summary["net_fees"] = summary["fees"] + summary["self_rebate"]
    summary["net_trading_pnl"] = (
        summary["realized_pnl"] + summary["fees"] +
        summary["funding"] + summary["futures_rebates"] +
        summary["self_rebate"]
    )
    for key in summary:
        summary[key] = round(summary[key], 6)

    try:
        raw_closes = _paginate_position_close(base_url, api_key, api_secret, settle, from_ts, to_ts)
    except Exception:
        raw_closes = []
    closes = [_clean_position_close(item) for item in raw_closes]
    closes.sort(key=lambda x: x["time"], reverse=True)

    return {
        "status": "ok",
        "days": days,
        "summary": summary,
        "daily_self_rebate": {k: round(v, 6) for k, v in sorted(daily_rebate.items())},
        "needs_spot_permission": needs_spot_permission,
        "position_closes": closes[:20],
    }


def get_realized_pnl_report(
    base_url: str, api_key: str, api_secret: str, settle: str,
    days: int = 30,
    ledger_limit: int = 200,
    spot_api_key: str = "",
    spot_api_secret: str = "",
) -> dict:
    """account_book + spot self-rebate + position_close 기반 실제 손익표.

    계산 규칙:
    - 실현손익 기준: futures account_book (pnl/fee/fund/refr)
    - self_rebate: spot/account_book?code=3341 (Affiliate Ultra Commission Self-Rebate)
    - 순손익 = pnl + fee + fund + futures_refr + self_rebate  (dnw 제외)
    - dnw (입출금/전송/구독/리딤): 집계하되 순손익에서 제외
    - position_close: 상세 참고용, 중복 합산하지 않음
    - 미실현손익 포함하지 않음
    - key/secret/sign/header/raw response는 반환값에 포함하지 않음
    """
    import time as _time
    from datetime import datetime, timezone, timedelta

    KST = timezone(timedelta(hours=9))
    now_ts  = int(_time.time())
    from_ts = now_ts - days * 86400
    to_ts   = now_ts

    from_dt = datetime.fromtimestamp(from_ts, tz=KST)
    to_dt   = datetime.fromtimestamp(to_ts,   tz=KST)

    # ── futures account_book ──────────────────────
    all_ledger = _paginate_account_book(
        base_url, api_key, api_secret, settle, from_ts, to_ts
    )

    # futures 진단
    _fut_sum_by_type: dict[str, float] = {}
    _fut_min_t = _fut_max_t = None
    for _item in all_ledger:
        try:
            _ts  = int(float(_item.get("time") or 0))
            _val = float(_item.get("change") or 0)
        except (TypeError, ValueError):
            continue
        _t = _item.get("type", "other")
        _fut_sum_by_type[_t] = round(_fut_sum_by_type.get(_t, 0.0) + _val, 6)
        if _fut_min_t is None or _ts < _fut_min_t:
            _fut_min_t = _ts
        if _fut_max_t is None or _ts > _fut_max_t:
            _fut_max_t = _ts

    futures_diag: dict = {
        "source":        "futures_account_book",
        "endpoint":      f"/futures/{settle}/account_book",
        "params_safe":   {"from": from_ts, "to": to_ts, "limit": 1000},
        "count":         len(all_ledger),
        "min_time_sec":  _fut_min_t,
        "max_time_sec":  _fut_max_t,
        "sum_by_type":   _fut_sum_by_type,
        "pages_fetched": (len(all_ledger) // 1000) + (1 if len(all_ledger) % 1000 else 0) if len(all_ledger) > 0 else 0,
        "stopped_reason": "complete",
    }

    # ── spot self-rebate (code=3341) ──────────────
    _spot_key    = spot_api_key    or api_key
    _spot_secret = spot_api_secret or api_secret
    self_rebate_items: list[dict] = []
    needs_spot_perm = False
    rebate_diagnostics: list[dict] = []
    try:
        self_rebate_items, needs_spot_perm, rebate_diagnostics = get_affiliate_self_rebates(
            base_url, _spot_key, _spot_secret, days=days
        )
    except Exception as _exc:
        needs_spot_perm = True  # 권한 문제 등 — graceful
        rebate_diagnostics = [{"result": "exception", "error_type": type(_exc).__name__}]

    # ── 집계 ──────────────────────────────────────
    # 성과(순손익)에 포함되는 타입
    PNL_TYPES    = {"pnl"}
    FEE_TYPES    = {"fee"}
    FUND_TYPES   = {"fund"}
    REFR_TYPES   = {"refr"}
    POINT_TYPES  = {"point_fee", "point_refr", "point_dnw"}

    # 성과 제외 현금흐름 (순손익에 포함하지 않고 별도 표시)
    NON_PNL_TYPES = {
        "dnw",          # deposit / withdraw
        "pv_dnw",       # perpetual deposit/withdraw
        "point_dnw",    # point deposit/withdraw
        # text 기반 추가 분류는 아래에서 처리
    }
    # Gate API는 Redeem/Subscription/Transfer를 type이 아닌 text로 구분하는 경우가 있음.
    # type=dnw인 항목의 text에 이 단어가 포함되면 NON_PNL로 이미 처리됨.

    summary = {
        "realized_pnl":          0.0,
        "fees":                  0.0,
        "self_rebate":           0.0,   # Affiliate Ultra Commission Self-Rebate
        "net_fees":              0.0,   # fees + self_rebate
        "funding":               0.0,
        "futures_rebates":       0.0,   # futures refr
        "point_fees":            0.0,
        "net_trading_pnl":       0.0,
        "non_pnl_cashflow":      0.0,   # 성과 제외 현금흐름 합계 (구 deposits_withdrawals)
        "deposits_withdrawals":  0.0,   # 하위 호환용 alias
    }

    daily_map: dict[str, dict] = {}

    def _get_or_create_day(day: str) -> dict:
        if day not in daily_map:
            daily_map[day] = {
                "date": day,
                "pnl": 0.0, "fee": 0.0, "fund": 0.0,
                "refr": 0.0, "self_rebate": 0.0,
                "net": 0.0, "dnw": 0.0,
            }
        return daily_map[day]

    # futures ledger 집계
    for item in all_ledger:
        t = item.get("type", "")
        try:
            ts  = float(item["time"])
            val = float(item.get("change") or 0)
        except (KeyError, TypeError, ValueError):
            continue

        day = datetime.fromtimestamp(ts, tz=KST).strftime("%Y-%m-%d")
        d   = _get_or_create_day(day)

        if t in PNL_TYPES:
            summary["realized_pnl"] += val;     d["pnl"]  += val
        elif t in FEE_TYPES:
            summary["fees"] += val;              d["fee"]  += val
        elif t in FUND_TYPES:
            summary["funding"] += val;           d["fund"] += val
        elif t in REFR_TYPES:
            summary["futures_rebates"] += val;   d["refr"] += val
        elif t in NON_PNL_TYPES:
            summary["non_pnl_cashflow"]     += val
            summary["deposits_withdrawals"] += val
            d["dnw"] += val
        elif t in POINT_TYPES:
            summary["point_fees"] += val

    # spot self-rebate 집계
    for item in self_rebate_items:
        val = item.get("change", 0.0)
        t_sec = item.get("time_sec", 0)
        day = datetime.fromtimestamp(t_sec, tz=KST).strftime("%Y-%m-%d")
        d   = _get_or_create_day(day)
        summary["self_rebate"]   += val
        d["self_rebate"]         += val

    # 파생값
    summary["net_fees"]        = round(summary["fees"] + summary["self_rebate"], 6)
    summary["net_trading_pnl"] = round(
        summary["realized_pnl"] + summary["fees"] + summary["funding"] +
        summary["futures_rebates"] + summary["self_rebate"], 6
    )
    for k in summary:
        summary[k] = round(summary[k], 6)

    # daily 정리
    daily_list = []
    for day_key in sorted(daily_map.keys()):
        d = daily_map[day_key]
        d["net"] = round(
            d["pnl"] + d["fee"] + d["fund"] + d["refr"] + d["self_rebate"], 6
        )
        for k in ("pnl", "fee", "fund", "refr", "self_rebate", "dnw"):
            d[k] = round(d[k], 6)
        daily_list.append(d)

    # ── ledger 직렬화: futures + spot self-rebate 합산, source 컬럼 포함 ──
    def _clean_futures_item(item: dict) -> dict:
        try:
            ts  = int(float(item.get("time") or 0))
            val = float(item.get("change") or 0)
            bal = float(item.get("balance") or 0)
        except (TypeError, ValueError):
            ts, val, bal = 0, 0.0, 0.0
        return {
            "time":    ts,
            "type":    str(item.get("type") or ""),
            "change":  round(val, 6),
            "balance": round(bal, 6),
            "text":    str(item.get("text") or ""),
            "source":  "futures_account_book",
        }

    def _clean_rebate_item(item: dict) -> dict:
        acct = item.get("account_mode", "classic")
        src  = "spot_account_book_unified" if acct == "unified" else "spot_account_book"
        return {
            "time":    item.get("time_sec", 0),
            "type":    "self_rebate",
            "change":  item.get("change", 0.0),
            "balance": item.get("balance", 0.0),
            "text":    item.get("text", ""),
            "source":  src,
        }

    combined = (
        [_clean_futures_item(x) for x in all_ledger] +
        [_clean_rebate_item(x) for x in self_rebate_items]
    )
    # 중복 제거: source+id 또는 time+change+type
    seen: set[tuple] = set()
    deduped = []
    for item in combined:
        key = (item["source"], item["time"], item["type"], item["change"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    deduped.sort(key=lambda x: x["time"], reverse=True)
    ledger = deduped[:ledger_limit]

    # ── position_close ────────────────────────────
    try:
        raw_closes = _paginate_position_close(
            base_url, api_key, api_secret, settle, from_ts, to_ts
        )
    except Exception:
        raw_closes = []

    position_closes = [_clean_position_close(c) for c in raw_closes]
    position_closes.sort(key=lambda x: x["time"], reverse=True)

    # ── source_diagnostics 조립 ──────────────────
    rebate_total_count = sum(d.get("filtered_count", 0) for d in rebate_diagnostics)
    rebate_total_sum   = sum(d.get("sum", 0.0) for d in rebate_diagnostics
                             if d.get("account_mode") == "classic")  # classic 기준
    source_diagnostics: list[dict] = [futures_diag]
    for d in rebate_diagnostics:
        source_diagnostics.append({
            "source":       f"spot_account_book_code3341_{d.get('account_mode','?')}",
            "endpoint":     d.get("endpoint", "/spot/account_book"),
            "params_safe":  d.get("params_safe", {}),
            "count":        d.get("filtered_count", d.get("raw_count", 0)),
            "min_time_sec": d.get("min_time_sec"),
            "max_time_sec": d.get("max_time_sec"),
            "sum":          d.get("sum", 0.0),
            "pages_fetched":d.get("pages_fetched", 0),
            "stopped_reason": d.get("stopped_reason", ""),
            "result":       d.get("result", ""),
        })
    # position_close 진단
    source_diagnostics.append({
        "source":       "position_close",
        "endpoint":     f"/futures/{settle}/position_close",
        "params_safe":  {"from": from_ts, "to": to_ts},
        "count":        len(position_closes),
        "min_time_sec": position_closes[-1]["time"] if position_closes else None,
        "max_time_sec": position_closes[0]["time"]  if position_closes else None,
    })

    return {
        "provider":             "gateio",
        "settle":               settle,
        "from":                 from_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "to":                   to_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "days":                 days,
        "summary":              summary,
        "daily":                daily_list,
        "ledger":               ledger,
        "position_closes":      position_closes,
        "needs_spot_permission": needs_spot_perm,
        "source_diagnostics":   source_diagnostics,
        "caveat": (
            "실현손익 기준 · 미실현손익 제외 · 입출금(dnw)은 투자성과에서 제외 · "
            "수수료 페이백(self_rebate)은 약 2시간 지연 반영될 수 있음"
        ),
    }
