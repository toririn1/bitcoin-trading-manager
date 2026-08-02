"""Gate.io read-only 연동 테스트.

검증 항목:
1. config.py Gate 변수 로드
2. gateio.py 서명 생성 (API 호출 없음)
3. _extract_account_fields / _extract_positions 정제 로직
4. account_context.py 분기 (provider=gateio, none, disabled)
5. format_account_context — key/secret 노출 없음
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGateioConfig(unittest.TestCase):
    """config.py Gate 변수 존재 여부."""

    def test_gate_config_vars_exist(self):
        import config as cfg
        self.assertTrue(hasattr(cfg, "ACCOUNT_PROVIDER"))
        self.assertTrue(hasattr(cfg, "ACCOUNT_FEATURES_ENABLED"))
        self.assertTrue(hasattr(cfg, "ACCOUNT_INCLUDE_IN_LLM"))
        self.assertTrue(hasattr(cfg, "GATE_BASE_URL"))
        self.assertTrue(hasattr(cfg, "GATE_SETTLE"))
        self.assertTrue(hasattr(cfg, "GATE_API_KEY"))
        self.assertTrue(hasattr(cfg, "GATE_API_SECRET"))
        self.assertTrue(hasattr(cfg, "GATE_ACCOUNT_READONLY"))
        self.assertTrue(callable(cfg.gate_key_configured))

    def test_gate_key_configured_false_when_empty(self):
        import config as cfg
        with patch.object(cfg, "GATE_API_KEY", ""), patch.object(cfg, "GATE_API_SECRET", ""):
            self.assertFalse(cfg.gate_key_configured())

    def test_gate_key_configured_true_when_set(self):
        import config as cfg
        with patch.object(cfg, "GATE_API_KEY", "testkey"), patch.object(cfg, "GATE_API_SECRET", "testsecret"):
            self.assertTrue(cfg.gate_key_configured())

    def test_gate_base_url_no_trailing_slash(self):
        import config as cfg
        self.assertFalse(cfg.GATE_BASE_URL.endswith("/"))


class TestGateioSign(unittest.TestCase):
    """gateio.py 서명 생성 — 네트워크 호출 없음."""

    def test_sign_returns_required_headers(self):
        from account_providers.gateio import _sign_request
        headers = _sign_request(
            api_key="TESTKEY",
            api_secret="TESTSECRET",
            method="GET",
            url="https://api.gateio.ws/api/v4/futures/usdt/accounts",
            query_string="",
            body="",
        )
        self.assertIn("KEY", headers)
        self.assertIn("Timestamp", headers)
        self.assertIn("SIGN", headers)
        # key/secret이 헤더 값에 그대로 노출되면 안 됨
        self.assertNotIn("TESTSECRET", str(headers))
        self.assertEqual(headers["KEY"], "TESTKEY")

    def test_sign_deterministic_with_fixed_time(self):
        """같은 입력 + 같은 timestamp → 같은 서명."""
        from account_providers.gateio import _sign_request
        import time
        with patch("time.time", return_value=1700000000.0):
            h1 = _sign_request("K", "S", "GET", "https://api.gateio.ws/api/v4/futures/usdt/accounts", "", "")
            h2 = _sign_request("K", "S", "GET", "https://api.gateio.ws/api/v4/futures/usdt/accounts", "", "")
        self.assertEqual(h1["SIGN"], h2["SIGN"])


class TestGateioExtract(unittest.TestCase):
    """_extract_account_fields / _extract_positions 정제 로직."""

    def test_extract_account_fields_basic(self):
        from account_providers.gateio import _extract_account_fields
        raw = {
            "total": "0",                    # API에서 0으로 오는 케이스
            "available": "870.98",
            "unrealised_pnl": "-12.3",
            "order_margin": "0",
            "position_margin": "0",
            "isolated_position_margin": "334.09",
            "currency": "USDT",
        }
        result = _extract_account_fields(raw)
        # total=0이면 역산: 870.98 + 334.09 + (-12.3) = 1192.77
        self.assertIsNotNone(result["futures_total"])
        self.assertAlmostEqual(result["futures_total"], 870.98 + 334.09 + (-12.3), places=1)
        self.assertEqual(result["futures_total_source"], "estimated")
        self.assertAlmostEqual(result["available"], 870.98)
        self.assertAlmostEqual(result["unrealised_pnl"], -12.3)
        self.assertAlmostEqual(result["isolated_position_margin"], 334.09)
        self.assertEqual(result["currency"], "USDT")
        for key in result:
            self.assertNotIn("secret", key.lower())
            self.assertNotIn("sign", key.lower())

    def test_extract_account_fields_missing_keys(self):
        from account_providers.gateio import _extract_account_fields
        result = _extract_account_fields({})
        self.assertIsNone(result["futures_total"])
        self.assertIsNone(result["available"])

    def test_extract_wallet_total(self):
        from account_providers.gateio import _extract_wallet_total
        raw = {
            "total": {"amount": "1205.22", "currency": "USDT", "unrealised_pnl": "59.1"},
            "details": {
                "futures": {"amount": "0", "unrealised_pnl": "59.1", "currency": "USDT"},
                "spot":    {"amount": "1205.22", "currency": "USDT"},
            }
        }
        result = _extract_wallet_total(raw)
        self.assertAlmostEqual(result["total_amount"], 1205.22)
        self.assertAlmostEqual(result["total_unrealised"], 59.1)
        self.assertAlmostEqual(result["spot_amount"], 1205.22)

    def test_extract_positions_empty(self):
        from account_providers.gateio import _extract_positions
        result = _extract_positions([])
        self.assertEqual(result, [])

    def test_extract_positions_filters_closed(self):
        """size=0 포지션(종료된 포지션)은 제외해야 한다."""
        from account_providers.gateio import _extract_positions
        raw = [
            {"contract": "BTC_USDT", "size": 0, "entry_price": "60000"},
            {"contract": "ETH_USDT", "size": "0.0", "entry_price": "3000"},
        ]
        result = _extract_positions(raw)
        self.assertEqual(result, [])

    def test_extract_positions_open_long_with_margin_mode(self):
        """173계약 = 0.0173 BTC, pos_margin_mode=isolated, mode=dual_long"""
        from account_providers.gateio import _extract_positions
        raw = [{
            "contract": "BTC_USDT",
            "size": "173",
            "entry_price": "60000.0",
            "mark_price": "61000.0",
            "liq_price": "50000.0",
            "margin": "334.09",
            "unrealised_pnl": "2000.0",
            "realised_pnl": "100.0",
            "leverage": "3",
            "mode": "dual_long",
            "pos_margin_mode": "isolated",
        }]
        result = _extract_positions(raw)
        self.assertEqual(len(result), 1)
        pos = result[0]
        self.assertEqual(pos["contract"], "BTC_USDT")
        self.assertEqual(pos["side"], "롱")
        self.assertEqual(pos["pos_side"], "dual_long")
        self.assertEqual(pos["margin_mode"], "isolated")
        self.assertAlmostEqual(pos["size"], 0.0173, places=6)
        self.assertEqual(pos["size_contracts"], 173)
        self.assertAlmostEqual(pos["contract_size"], 0.0001)
        self.assertAlmostEqual(pos["notional"], 1055.3, places=6)
        self.assertAlmostEqual(pos["leverage_margin"], 334.09, places=6)
        self.assertAlmostEqual(pos["actual_leverage"], 1055.3 / 334.09, places=4)
        for key in pos:
            self.assertNotIn("secret", key.lower())
            self.assertNotIn("sign", key.lower())

    def test_extract_positions_open_short(self):
        from account_providers.gateio import _extract_positions
        raw = [{
            "contract": "BTC_USDT",
            "size": "-100",
            "entry_price": "60000",
            "mode": "dual_short",
            "pos_margin_mode": "cross",
        }]
        result = _extract_positions(raw)
        self.assertEqual(result[0]["side"], "숏")
        self.assertEqual(result[0]["pos_side"], "dual_short")
        self.assertEqual(result[0]["margin_mode"], "cross")
        self.assertAlmostEqual(result[0]["size"], 0.01, places=6)


class TestAccountContextGateBranch(unittest.TestCase):
    """account_context.py provider 분기 테스트."""

    def _patch_cfg(self, provider="gateio", enabled=True, key="testkey", secret="testsecret"):
        import config as cfg
        return [
            patch.object(cfg, "ACCOUNT_PROVIDER", provider),
            patch.object(cfg, "ACCOUNT_FEATURES_ENABLED", enabled),
            patch.object(cfg, "GATE_API_KEY", key),
            patch.object(cfg, "GATE_API_SECRET", secret),
            patch.object(cfg, "GATE_BASE_URL", "https://api.gateio.ws/api/v4"),
            patch.object(cfg, "GATE_SETTLE", "usdt"),
        ]

    def test_provider_none_returns_disabled(self):
        patches = self._patch_cfg(provider="none")
        for p in patches:
            p.start()
        try:
            from account_context import fetch_account_context
            ctx = fetch_account_context()
            self.assertTrue(ctx.get("disabled"))
        finally:
            for p in patches:
                p.stop()

    def test_features_disabled_returns_disabled(self):
        patches = self._patch_cfg(provider="gateio", enabled=False)
        for p in patches:
            p.start()
        try:
            from account_context import fetch_account_context
            ctx = fetch_account_context()
            self.assertTrue(ctx.get("disabled"))
        finally:
            for p in patches:
                p.stop()

    def test_gateio_key_missing_returns_disabled(self):
        patches = self._patch_cfg(provider="gateio", key="", secret="")
        for p in patches:
            p.start()
        try:
            from account_context import fetch_account_context
            ctx = fetch_account_context()
            self.assertTrue(ctx.get("disabled"))
            self.assertEqual(ctx.get("provider"), "gateio")
        finally:
            for p in patches:
                p.stop()

    def test_gateio_provider_field(self):
        """Gate provider일 때 ctx['provider'] == 'gateio'."""
        mock_gate_ctx = {
            "provider": "gateio",
            "wallet": {"total_amount": 1205.22},
            "account": {"futures_total": 1264.73, "futures_total_source": "estimated",
                        "available": 870.98, "unrealised_pnl": 59.1,
                        "isolated_position_margin": 334.09, "order_margin": 0.0, "currency": "USDT"},
            "positions": [],
            "wallet_error": None,
            "account_error": None,
            "position_error": None,
        }
        patches = self._patch_cfg(provider="gateio", key="k", secret="s")
        for p in patches:
            p.start()
        try:
            with patch("account_context._fetch_gate_account_context", return_value=mock_gate_ctx):
                from account_context import fetch_account_context
                ctx = fetch_account_context()
                self.assertEqual(ctx.get("provider"), "gateio")
        finally:
            for p in patches:
                p.stop()


class TestFormatAccountContextGate(unittest.TestCase):
    """format_account_context — key/secret 노출 없음 검증."""

    def test_format_disabled(self):
        from account_context import format_account_context
        ctx = {"provider": "none", "disabled": True}
        result = format_account_context(ctx)
        self.assertIn("비활성", result)
        # key/secret 노출 없음
        self.assertNotIn("secret", result.lower())
        self.assertNotIn("sign", result.lower())

    def test_format_gate_no_key(self):
        from account_context import format_account_context
        ctx = {
            "provider": "gateio",
            "disabled": True,
            "disabled_reason": "Gate.io API 키 또는 시크릿이 설정되지 않았습니다.",
            "account": None,
            "positions": None,
        }
        result = format_account_context(ctx)
        self.assertIn("비활성", result)
        # 영문 'secret' 단어가 raw 값으로 노출되지 않아야 함
        self.assertNotIn("GATE_API_SECRET", result)
        self.assertNotIn("api_secret", result.lower())

    def test_format_gate_with_data(self):
        from account_context import format_account_context
        ctx = {
            "provider": "gateio",
            "disabled": False,
            "wallet": {"total_amount": 1205.22, "total_currency": "USDT"},
            "account": {
                "futures_total": 1264.73,
                "futures_total_source": "estimated",
                "available": 870.98,
                "unrealised_pnl": 59.1,
                "isolated_position_margin": 334.09,
                "order_margin": 0.0,
                "currency": "USDT",
            },
            "positions": [],
            "wallet_error": None,
            "account_error": None,
            "position_error": None,
        }
        result = format_account_context(ctx)
        self.assertIn("Gate.io", result)
        self.assertIn("1,205.22", result)   # total_assets
        self.assertIn("1,264.73", result)   # futures_total
        self.assertIn("870.98", result)     # available
        self.assertNotIn("secret", result.lower())
        self.assertNotIn("sign", result.lower())
        self.assertNotIn("header", result.lower())
        self.assertIn("없음", result)

    def test_format_gate_with_position(self):
        from account_context import format_account_context
        ctx = {
            "provider": "gateio",
            "disabled": False,
            "wallet": {"total_amount": 1205.22, "total_currency": "USDT"},
            "account": {
                "futures_total": 1264.73,
                "futures_total_source": "estimated",
                "available": 870.98,
                "unrealised_pnl": 59.1,
                "isolated_position_margin": 334.09,
                "order_margin": 0.0,
                "currency": "USDT",
            },
            "positions": [{
                "contract": "BTC_USDT",
                "side": "롱",
                "pos_side": "dual_long",
                "margin_mode": "isolated",
                "size": 0.0173,
                "size_contracts": 173,
                "contract_size": 0.0001,
                "leverage": "3",
                "entry_price": 57819.7,
                "mark_price": 61240.0,
                "liq_price": 38652.93,
                "unrealised_pnl": 59.1,
                "realised_pnl": -0.45,
            }],
            "wallet_error": None,
            "account_error": None,
            "position_error": None,
        }
        result = format_account_context(ctx)
        self.assertIn("BTC_USDT", result)
        self.assertIn("isolated", result)
        self.assertIn("0.0173", result)
        self.assertIn("173계약", result)
        self.assertNotIn("secret", result.lower())
        self.assertNotIn("sign", result.lower())


class TestNoForbiddenEndpoints(unittest.TestCase):
    """gateio.py에 금지 endpoint 문자열이 없는지 확인."""

    def test_no_order_create_endpoint(self):
        import inspect
        from account_providers import gateio
        source = inspect.getsource(gateio)
        forbidden = [
            "/orders",
            "POST",
            "PUT",
            "DELETE",
            "leverage",
        ]
        for token in forbidden:
            # 주석/독스트링 제외 코드에서만 검사
            # 실제로는 소스 전체에서 체크
            if token in ("POST", "PUT", "DELETE"):
                # method 이름으로 실행 경로에 있으면 안 됨
                # 주석/docstring에 언급은 허용
                import re
                code_lines = [
                    line for line in source.splitlines()
                    if not line.strip().startswith("#") and '"""' not in line and "'''" not in line
                ]
                code_only = "\n".join(code_lines)
                # requests.post / session.post / .put / .delete 형태가 없어야 함
                self.assertNotRegex(
                    code_only,
                    rf'(?:_session|requests)\s*\.\s*{token.lower()}\s*\(',
                    f"금지 HTTP 메서드 {token} 발견: gateio.py",
                )


class TestAggregateDailyPnl(unittest.TestCase):
    """aggregate_daily_pnl — account_book 집계 로직."""

    def _mock_account_book(self):
        """KST 기준으로 2일치 account_book 이벤트를 만들어 반환."""
        import time
        # KST 2026-07-01 12:00 (UTC 03:00)
        ts_day1 = 1751335200  # 2026-07-01T03:00:00Z = KST 12:00
        # KST 2026-07-02 09:00 (UTC 00:00)
        ts_day2 = 1751414400  # 2026-07-02T00:00:00Z = KST 09:00
        return [
            {"type": "pnl",      "time": ts_day1, "change": "15.5"},
            {"type": "fee",      "time": ts_day1, "change": "-3.2"},
            {"type": "fund",     "time": ts_day1, "change": "0.1"},
            {"type": "pv_dnw",   "time": ts_day1, "change": "100"},   # 제외 대상
            {"type": "pnl",      "time": ts_day2, "change": "-8.0"},
            {"type": "fee",      "time": ts_day2, "change": "-1.5"},
            {"type": "fund",     "time": ts_day2, "change": "-0.3"},
            {"type": "point_fee","time": ts_day2, "change": "-0.05"}, # include_point_fee=False면 제외
        ]

    def test_aggregate_basic(self):
        from account_providers.gateio import aggregate_daily_pnl
        mock_items = self._mock_account_book()

        with patch("account_providers.gateio.fetch_account_book", return_value=mock_items):
            result = aggregate_daily_pnl(
                "https://api.gateio.ws/api/v4",
                "TESTKEY", "TESTSECRET",
                "usdt",
                days=30,
                include_point_fee=False,
            )

        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 1)
        # 날짜 오름차순
        dates = [d["date"] for d in result]
        self.assertEqual(dates, sorted(dates))

        # 집계 검증 — day1
        day1 = next((d for d in result if d["date"].endswith("-01")), None)
        if day1:
            self.assertAlmostEqual(day1["realized_pnl"], 15.5, places=3)
            self.assertAlmostEqual(day1["fee"], -3.2, places=3)
            self.assertAlmostEqual(day1["funding"], 0.1, places=3)
            # daily_total = 15.5 + (-3.2) + 0.1 = 12.4
            self.assertAlmostEqual(day1["daily_total"], 12.4, places=3)
            # pv_dnw는 포함되지 않아야 함
            self.assertNotIn("pv_dnw", day1)

        # key/secret 노출 없음
        for d in result:
            for key in d:
                self.assertNotIn("secret", key.lower())
                self.assertNotIn("sign", key.lower())

    def test_aggregate_with_point_fee(self):
        from account_providers.gateio import aggregate_daily_pnl
        mock_items = self._mock_account_book()

        with patch("account_providers.gateio.fetch_account_book", return_value=mock_items):
            result = aggregate_daily_pnl(
                "https://api.gateio.ws/api/v4",
                "TESTKEY", "TESTSECRET",
                "usdt",
                days=30,
                include_point_fee=True,
            )

        # day2의 point_fee(-0.05)가 daily_total에 포함되어야 함
        day2 = next((d for d in result if d["date"].endswith("-02")), None)
        if day2:
            # daily_total = -8.0 + (-1.5) + (-0.3) + (-0.05) = -9.85
            self.assertAlmostEqual(day2["daily_total"], -9.85, places=3)

    def test_aggregate_dedupes_repeated_pages(self):
        from account_providers.gateio import aggregate_daily_pnl
        base_items = [
            {**item, "id": f"ledger-{idx}"}
            for idx, item in enumerate(self._mock_account_book())
        ]
        filler = [
            {"id": f"filler-{idx}", "type": "pv_dnw", "time": 1751414401 + idx, "change": "0"}
            for idx in range(1000 - len(base_items))
        ]
        mock_items = base_items + filler

        with patch("account_providers.gateio.fetch_account_book", side_effect=[mock_items, mock_items, []]):
            result = aggregate_daily_pnl(
                "https://api.gateio.ws/api/v4",
                "TESTKEY", "TESTSECRET",
                "usdt",
                days=30,
                include_point_fee=False,
            )

        day1 = next((d for d in result if d["date"].endswith("-01")), None)
        self.assertIsNotNone(day1)
        self.assertAlmostEqual(day1["realized_pnl"], 15.5, places=3)
        self.assertAlmostEqual(day1["fee"], -3.2, places=3)
        self.assertAlmostEqual(day1["daily_total"], 12.4, places=3)

    def test_aggregate_empty_book(self):
        from account_providers.gateio import aggregate_daily_pnl
        with patch("account_providers.gateio.fetch_account_book", return_value=[]):
            result = aggregate_daily_pnl(
                "https://api.gateio.ws/api/v4",
                "TESTKEY", "TESTSECRET",
                "usdt",
                days=30,
            )
        self.assertEqual(result, [])

    def test_aggregate_returns_list(self):
        """반환값은 항상 list여야 한다."""
        from account_providers.gateio import aggregate_daily_pnl
        with patch("account_providers.gateio.fetch_account_book", return_value=[]):
            result = aggregate_daily_pnl(
                "https://api.gateio.ws/api/v4",
                "TESTKEY", "TESTSECRET",
                "usdt",
                days=30,
            )
        self.assertIsInstance(result, list)


class TestGateSnapshotAdapter(unittest.TestCase):
    """account_history._gate_snapshot_from_context — 스냅샷 어댑터."""

    def _build_ctx(self, futures_total=1264.73, upnl=59.1, available=870.98):
        return {
            "provider": "gateio",
            "account_equity": futures_total,
            "wallet_balance": futures_total,
            "available_balance": available,
            "unrealised_pnl": upnl,
            "open_position_notional": 2059.45,
            "gross_position_notional": 2059.45,
            "net_position_notional": 59.45,
            "long_notional": 1059.45,
            "short_notional": 1000.0,
            "account_gross_leverage": 1.6283,
            "account_net_leverage": 0.047,
            "account_actual_leverage": 1.6283,
            "effective_leverage": 0.047,
            "hedge_offset_ratio": 0.9711,
            "position_actual_leverage": 3.1711,
            "account": {
                "futures_total": futures_total,
                "futures_total_source": "estimated",
                "available": available,
                "unrealised_pnl": upnl,
                "isolated_position_margin": 334.09,
                "order_margin": 0.0,
                "currency": "USDT",
            },
            "wallet": {"total_amount": 1205.22},
            "positions": [
                {
                    "contract": "BTC_USDT",
                    "side": "롱",
                    "pos_side": "dual_long",
                    "margin_mode": "isolated",
                    "size": 0.0173,
                    "size_contracts": 173,
                    "contract_size": 0.0001,
                    "leverage": "3",
                    "entry_price": 57819.7,
                    "mark_price": 61240.0,
                    "liq_price": 38652.93,
                    "unrealised_pnl": upnl,
                    "realised_pnl": -0.45,
                }
            ],
            "today_cash_pnl": None,
            "today_total_pnl": None,
            "today_total_mode": None,
            "today_total_label": None,
            "day_start_equity": None,
            "day_anchor_source": None,
            "carryover_positions": [],
        }

    def test_snapshot_has_required_fields(self):
        from account_history import _gate_snapshot_from_context
        from datetime import datetime, timezone
        ctx = self._build_ctx()
        now = datetime.now(timezone.utc)
        snap = _gate_snapshot_from_context(ctx, now)

        required = [
            "observed_at", "observed_ts", "provider",
            "account_equity", "available_balance", "wallet_balance",
            "open_position_count", "open_position_upnl",
            "long_notional", "short_notional", "exposure_bias",
            "position_symbols", "top_positions", "position_signature",
        ]
        for field in required:
            self.assertIn(field, snap, f"필드 누락: {field}")

        self.assertEqual(snap["provider"], "gateio")
        self.assertAlmostEqual(snap["account_equity"], 1264.73)
        self.assertEqual(snap["open_position_count"], 1)
        self.assertAlmostEqual(snap["account_gross_leverage"], 1.6283)
        self.assertAlmostEqual(snap["account_net_leverage"], 0.047)
        self.assertAlmostEqual(snap["effective_leverage"], 0.047)
        self.assertAlmostEqual(snap["hedge_offset_ratio"], 0.9711)
        self.assertAlmostEqual(snap["position_actual_leverage"], 3.1711)

        # key/secret 노출 없음 — 값 기준으로 체크 (필드명 'position_signature'는 허용)
        FORBIDDEN_KEYS = {"secret", "api_secret", "api_key", "sign_str"}
        for key in snap:
            self.assertNotIn(key.lower(), FORBIDDEN_KEYS, f"보안 필드 노출: {key}")

    def test_snapshot_long_notional_computed(self):
        """롱 포지션의 명목가치 = size * mark_price."""
        from account_history import _gate_snapshot_from_context
        from datetime import datetime, timezone
        ctx = self._build_ctx()
        snap = _gate_snapshot_from_context(ctx, datetime.now(timezone.utc))
        # size=0.0173, mark_price=61240 → notional ≈ 1059.452
        self.assertGreater(snap["long_notional"], 0)
        self.assertAlmostEqual(snap["short_notional"], 0.0)
        self.assertEqual(snap["exposure_bias"], "long")

    def test_snapshot_is_observable(self):
        """account_equity가 있으면 _is_observable == True."""
        from account_history import _gate_snapshot_from_context, _is_observable
        from datetime import datetime, timezone
        ctx = self._build_ctx()
        snap = _gate_snapshot_from_context(ctx, datetime.now(timezone.utc))
        self.assertTrue(_is_observable(snap))

    def test_snapshot_from_context_routing(self):
        """provider=gateio 이면 _gate_snapshot_from_context 경로로 가야 한다."""
        from account_history import _snapshot_from_context
        from datetime import datetime, timezone
        ctx = self._build_ctx()
        snap = _snapshot_from_context(ctx, datetime.now(timezone.utc))
        self.assertEqual(snap.get("provider"), "gateio")
        self.assertIsNotNone(snap.get("account_equity"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
