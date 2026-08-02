from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine_v2.account import read_account_overlay
from engine_v2.config import V2Settings
from engine_v2.domain.registry import AssetRegistry, build_default_registry
from engine_v2.events import classify, deduplicate, normalize_event
from engine_v2.features import (
    aggregate_quality,
    actual_delta_surface,
    classify_regime,
    closed_candle_features,
    factor_state,
    funding_basis,
    orderbook_features,
    portfolio_concentration,
    product_factor_exposure,
    trade_cvd,
    weighted_oi,
)
from engine_v2.ingestion import (
    BinancePublicProvider,
    BybitPublicProvider,
    CoinGlassProvider,
    DeribitOptionsProvider,
    GateCFDProvider,
    GateFuturesProvider,
    GateStockProvider,
    KISProvider,
    MarketDataManager,
    ManualNewsProvider,
    OfficialEventsProvider,
    YFinanceDelayedProvider,
)
from engine_v2.intelligence import build_snapshot, explain_snapshot, validate_claims
from engine_v2.opportunities import scan_opportunities
from engine_v2.storage import V2Storage

from .cross_asset_refs import build_relationships
from .fixture import fixture_observations


TARGET_UNDERLYINGS = [
    "BTC", "ETH", "QQQ", "SOXX", "SMH", "SOXL", "NVDA", "MU", "TSM",
    "SPY", "VIX", "SK_HYNIX_KRX", "SAMSUNG_KRX", "KOSPI", "KOSDAQ", "USD_KRW",
]


class V2Engine:
    def __init__(self, root_dir: str | Path | None = None, settings: V2Settings | None = None) -> None:
        self.root_dir = Path(root_dir or Path.cwd())
        self.settings = settings or V2Settings.from_env()
        self.settings.ensure_dirs(self.root_dir)
        self.registry: AssetRegistry = build_default_registry()
        self.storage = V2Storage(
            self.root_dir / self.settings.data_dir,
            self.root_dir / self.settings.duckdb_path,
            self.root_dir / self.settings.parquet_root,
        )
        self.manager = MarketDataManager(self.registry, self.storage)
        self.manager.register(BinancePublicProvider(timeout=self.settings.request_timeout_seconds, futures=True))
        self.manager.register(BybitPublicProvider(timeout=self.settings.request_timeout_seconds))
        self.manager.register(GateFuturesProvider(timeout=self.settings.request_timeout_seconds))
        self.manager.register(GateStockProvider())
        self.manager.register(GateCFDProvider())
        self.manager.register(CoinGlassProvider())
        self.manager.register(DeribitOptionsProvider(timeout=self.settings.request_timeout_seconds))
        self.manager.register(KISProvider())
        self.manager.register(OfficialEventsProvider())
        self.manager.register(ManualNewsProvider())
        self.manager.register(YFinanceDelayedProvider(timeout=max(20.0, self.settings.request_timeout_seconds)))
        self.events: list[dict[str, Any]] = []
        self.last_snapshot: dict[str, Any] | None = None
        self.last_decision: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "engine": "v2",
            "enabled": self.settings.enabled,
            "mode": self.settings.mode,
            "shadow_mode": self.settings.shadow_mode,
            "legacy_engine_enabled": self.settings.legacy_engine_enabled,
            "legacy_debate_enabled": self.settings.legacy_debate_enabled,
            "storage": self.storage.status(),
            "provider_count": len(self.manager.providers),
            "registered_products": len(self.registry.products),
        }

    async def discover(self) -> dict[str, Any]:
        results = await self.manager.discover(TARGET_UNDERLYINGS)
        return {
            "results": [result.to_dict() for result in results],
            "registry": self.registry.to_dict(),
            "provider_health": self.manager.health(),
        }

    async def build_snapshot(
        self,
        *,
        mode: str | None = None,
        live: bool | None = None,
        decision_time: datetime | None = None,
    ) -> dict[str, Any]:
        selected_mode = self._resolve_mode(mode, live)
        decision_time = decision_time or datetime.now(timezone.utc)
        if selected_mode == "fixture":
            raw_observations = fixture_observations()
        elif selected_mode == "replay":
            raw_observations = self.storage.observations(decision_time=decision_time, limit=10000)
        elif not self.settings.live_enabled:
            raw_observations = []
        else:
            raw_observations = await self._live_observations()
        if selected_mode == "fixture":
            self.storage.append_observations(raw_observations)
        records = [_record(observation) for observation in raw_observations]
        account = await asyncio.to_thread(read_account_overlay) if selected_mode == "live" else {
            "status": "not_loaded",
            "positions": None,
            "reason": "non_live_mode",
        }
        return self._assemble_snapshot(records, selected_mode, decision_time, account)

    def _resolve_mode(self, mode: str | None, live: bool | None) -> str:
        if live is not None:
            return "live" if live else "fixture"
        selected = (mode or self.settings.mode or "live").lower()
        return selected if selected in {"live", "fixture", "replay"} else "live"

    async def _live_observations(self) -> list[Any]:
        await self.manager.discover(TARGET_UNDERLYINGS)
        limit = int(os.getenv("V2_LIVE_LIMIT", "120"))
        products = list(self.registry.tradable_products())
        timeframes = self.settings.default_timeframes
        results = await asyncio.gather(*(
            self.manager.backfill(product.product_id, timeframe=timeframe, limit=limit)
            for product in products
            for timeframe in timeframes
        ))
        observations = [observation for result in results for observation in result.data]
        if os.getenv("COINGLASS_ENABLED", "").lower() in {"1", "true", "yes"} and os.getenv("COINGLASS_API_KEY"):
            btc = self.registry.product("BTC_BINANCE_PERP")
            provider = self.manager.providers.get("coinglass")
            if btc is not None and provider is not None:
                result = await provider.backfill(btc, timeframe="15m", limit=limit)
                self.storage.append_observations(result.data)
                observations.extend(result.data)

        official_events = self.manager.providers.get("official_events")
        if os.getenv("OFFICIAL_EVENTS_ENABLED", "").lower() in {"1", "true", "yes"} and official_events is not None:
            event_result = await official_events.fetch_events(limit=100)
            self.storage.append_observations(event_result.data)
            observations.extend(event_result.data)

        deribit = self.manager.providers.get("deribit")
        option_limit = max(0, int(os.getenv("V2_OPTION_SAMPLE", "12")))
        if deribit is not None and option_limit:
            for underlying in ("BTC", "ETH"):
                option_products = [
                    product for product in self.registry.products.values()
                    if product.provider == "deribit"
                    and product.underlying_id == underlying
                    and product.product_type.value == "option"
                ]
                if not option_products:
                    continue
                price = _latest_price(observations, self.registry, underlying)
                selected = sorted(
                    option_products,
                    key=lambda product: _option_sort_key(product, price),
                )[:option_limit]
                option_results = await asyncio.gather(*(
                    deribit.backfill(product, timeframe="15m", limit=1)
                    for product in selected
                ))
                observations.extend(
                    observation
                    for result in option_results
                    for observation in result.data
                )
        return observations

    def _assemble_snapshot(
        self,
        records: list[dict[str, Any]],
        mode: str,
        decision_time: datetime,
        account: dict[str, Any],
    ) -> dict[str, Any]:
        by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            by_product[str(record.get("product_id") or "")].append(record)
        product_rows = [
            product.to_dict()
            for product in self.registry.products.values()
            if (product.role == "tradable" and product.is_tradable) or product.product_id in by_product
        ]
        candidate_rows = [product.to_dict() for product in self.registry.tradable_products()]
        product_by_id = {row["product_id"]: row for row in product_rows}
        product_snapshots: dict[str, dict[str, Any]] = {}
        series_by_product: dict[str, list[dict[str, Any]]] = {}
        latest_return_by_underlying: dict[str, float | None] = {}

        for product_id, product in product_by_id.items():
            product_records = by_product.get(product_id, [])
            technical = _technical(product_records, self.settings.minimum_sample_count)
            series_by_product[product_id] = _return_series(product_records)
            latest_return_by_underlying[product.get("underlying_id", product_id)] = technical.get("return_24")
            product_snapshots[product_id] = self._product_snapshot(
                product,
                product_records,
                technical,
                mode,
                decision_time,
            )

        option_state = _option_states(records, product_by_id)
        for product_id, state in product_snapshots.items():
            underlying = state.get("product", {}).get("underlying_id")
            state["features"]["options"] = option_state.get(underlying, {"quality": "insufficient_data"})

        relationships = build_relationships(
            product_by_id,
            series_by_product,
            minimum_samples=self.settings.minimum_sample_count,
            minimum_overlap_ratio=self.settings.minimum_overlap_ratio,
        )
        for product_id, state in product_snapshots.items():
            product_relationships = relationships.get(product_id, {})
            usable = [
                value.get("ew_corr")
                for value in product_relationships.values()
                if value.get("usable") and value.get("ew_corr") is not None
            ]
            state["cross_asset_state"] = product_relationships
            state["features"]["cross_asset"] = sum(usable) / len(usable) if usable else None
            state["features"]["cross_asset_state"] = product_relationships
            state["features"]["regime"] = classify_regime({
                **state["features"].get("technical", {}),
                "cross_asset_state": "usable" if usable else "insufficient_data",
                "weighted_funding": state["features"].get("derivatives_state", {}).get("weighted_funding"),
                "liquidity_vacuum_score": state["features"].get("microstructure", {}).get("orderbook", {}).get("liquidity_vacuum_score"),
            })

        ages_by_underlying = {
            str(state.get("product", {}).get("underlying_id") or product_id): state.get("source_age_seconds")
            for product_id, state in product_snapshots.items()
        }
        usd_krw_age = ages_by_underlying.get("USD_KRW")
        for product_id, state in product_snapshots.items():
            underlying_age = ages_by_underlying.get(
                str(state.get("product", {}).get("underlying_id") or product_id)
            )
            context = state.setdefault("product_context", {})
            context["underlying_price_age"] = underlying_age
            context["underlying_price_stale"] = (
                underlying_age is None or underlying_age > self.settings.max_data_age_seconds
            )
            context["underlying_stale"] = context["underlying_price_stale"]
            context["underlying_close_age"] = underlying_age
            context["usd_krw_age"] = usd_krw_age
        global_quality = _quality(records, mode)
        factors = factor_state(latest_return_by_underlying)
        positions = account.get("positions")
        portfolio = portfolio_concentration(
            positions or [],
            max_exposure=self.settings.max_factor_exposure,
        ) if positions is not None else {
            "factor_exposure": None,
            "breaches": ["account_data_unavailable"],
            "quality": "data_unavailable",
        }
        self._apply_portfolio_risk(product_snapshots, product_by_id, account, portfolio)

        candidates = []
        for product in candidate_rows:
            product_id = product["product_id"]
            state = product_snapshots[product_id]
            base = {
                "mode": mode,
                "features": state["features"],
                "data_quality": state["data_quality"],
                "costs": state["costs"],
                "product_context": state["product_context"],
                "calibrated_edges": self.storage.calibrated_edges(
                    min_samples=self.settings.minimum_calibration_samples
                ),
                "min_rr": self.settings.minimum_rr,
                "snapshot_id": None,
            }
            candidates.extend(scan_opportunities([product], base, min_net_edge_bps=self.settings.min_net_edge_bps))
        candidate_dicts = [candidate.to_dict() for candidate in candidates]

        snapshot = build_snapshot(
            registry=self.registry.to_dict(),
            observations=records,
            features={
                "mode": mode,
                "synthetic": mode == "fixture",
                "data_unavailable": not bool(records) and mode in {"live", "replay"},
                "product_count": len(product_snapshots),
                "product_snapshots": product_snapshots,
                "cross_asset_state": relationships,
                "option_state": option_state,
                "regime": {
                    "state": "insufficient_data"
                    if global_quality["quality"] == "data_unavailable"
                    else "computed",
                },
            },
            data_quality=global_quality,
            factor_state=factors,
            event_state=self.events,
            ranked_candidates=candidate_dicts,
            portfolio_constraints=portfolio,
            unsupported_data=self._unsupported_data(),
        )
        for candidate in snapshot["ranked_candidates"]:
            candidate["source_snapshot_id"] = snapshot["snapshot_id"]
        snapshot["account_overlay"] = account
        snapshot["mode"] = mode
        snapshot["synthetic"] = mode == "fixture"
        snapshot["data_unavailable"] = not bool(records) and mode in {"live", "replay"}
        snapshot["explanation"] = explain_snapshot(snapshot)
        snapshot["critic"] = validate_claims(snapshot["explanation"], snapshot)
        self.last_snapshot = snapshot
        self.last_decision = self._decision_from_snapshot(snapshot)
        self.storage.save_decision(snapshot["snapshot_id"], decision_time, self.last_decision)
        self.storage.save_candidates(snapshot["snapshot_id"], decision_time, snapshot["ranked_candidates"])
        return snapshot

    def _apply_portfolio_risk(
        self,
        product_snapshots: dict[str, dict[str, Any]],
        product_by_id: dict[str, dict[str, Any]],
        account: dict[str, Any],
        portfolio: dict[str, Any],
    ) -> None:
        positions = account.get("positions")
        if positions is None:
            return
        breaches = set(portfolio.get("breaches") or [])
        for product_id, state in product_snapshots.items():
            product = product_by_id.get(product_id, {})
            underlying = str(product.get("underlying_id") or "")
            related = [
                position for position in positions
                if str(position.get("asset_id") or "").upper() == underlying.upper()
            ]
            factors = product_factor_exposure(underlying)
            factor_breaches = [factor for factor in factors if factor in breaches]
            state["features"]["portfolio_fit"] = -min(20.0, 4.0 * len(related) + 8.0 * len(factor_breaches))
            state["features"]["product_risk"] = min(20.0, 3.0 * len(related) + 8.0 * len(factor_breaches))
            context = state.setdefault("product_context", {})
            context["portfolio_position_count"] = len(related)
            context["portfolio_position_sides"] = sorted(
                {str(position.get("side") or "unknown") for position in related}
            )
            context["portfolio_factor_breaches"] = factor_breaches
            context["portfolio_warning"] = bool(related or factor_breaches)

    def _product_snapshot(
        self,
        product: dict[str, Any],
        records: list[dict[str, Any]],
        technical: dict[str, Any],
        mode: str,
        decision_time: datetime,
    ) -> dict[str, Any]:
        trade_rows = []
        for record in records:
            if record.get("data_type") != "trade":
                continue
            row = dict(record.get("payload") or {})
            row.setdefault("product_id", record.get("product_id"))
            row.setdefault("venue", record.get("venue"))
            trade_rows.append(row)
        cvd = trade_cvd(trade_rows)
        orderbooks = [record for record in records if record.get("data_type") == "orderbook"]
        orderbook = orderbook_features(
            (orderbooks[-1].get("payload") or {}),
            mid_price=technical.get("latest_close"),
        ) if orderbooks else {"quality": "partial", "reason": "orderbook_missing"}

        derivative_rows = []
        for record in records:
            if record.get("data_type") not in {"open_interest", "mark_funding"}:
                continue
            row = dict(record.get("payload") or {})
            row["venue"] = record.get("venue")
            row["price"] = technical.get("latest_close")
            if row.get("open_interest_usd") is None:
                row["open_interest_usd"] = row.get("open_interest_value")
            if row.get("basis") is None and row.get("mark_price") and row.get("index_price"):
                row["basis"] = float(row["mark_price"]) / float(row["index_price"]) - 1
            derivative_rows.append(row)
        derivatives = {
            **weighted_oi(derivative_rows, price=technical.get("latest_close")),
            **funding_basis(derivative_rows),
        }
        quality = _quality(records, mode)
        features = {
            "technical_structure": _directional(technical.get("return_24")),
            "momentum": _directional(technical.get("return_4")),
            "orderflow": _directional(cvd.get("notional_cvd_ratio")),
            "derivatives": _directional((derivatives.get("weighted_funding") or 0) * 1000)
            if derivatives.get("weighted_funding") is not None else None,
            "cross_asset": None,
            "event": None,
            "liquidity": _directional(orderbook.get("distance_weighted_imbalance_0.25%")),
            "product_risk": 0,
            "portfolio_fit": 0,
            "technical": technical,
            "microstructure": {"trade_cvd": cvd, "orderbook": orderbook},
            "derivatives_state": derivatives,
            "cross_asset_state": {},
            "regime": {},
        }
        latest_event = _latest_event_time(records)
        age = (decision_time - latest_event).total_seconds() if latest_event else None
        product_context = {
            "underlying_price_age": age,
            "underlying_price_stale": age is None or age > self.settings.max_data_age_seconds,
            "underlying_stale": age is None or age > self.settings.max_data_age_seconds,
            "krx_session_state": _latest_session(records),
            "underlying_close_age": age,
            "usd_krw_age": None,
            "funding": derivatives.get("weighted_funding"),
            "basis": derivatives.get("annualized_basis"),
            "oi_concentration": derivatives.get("venue_oi_share"),
            "spot_perp_divergence": None,
        }
        configured_slippage = product.get("estimated_slippage_bps")
        derived_slippage = (
            float(orderbook.get("spread_bps")) / 2
            if orderbook.get("spread_bps") is not None else None
        )
        costs = {
            "spread_bps": orderbook.get("spread_bps"),
            "taker_fee_bps": product.get("taker_fee_bps"),
            "maker_fee_bps": product.get("maker_fee_bps"),
            "estimated_slippage_bps": configured_slippage if configured_slippage is not None else derived_slippage,
            "slippage_source": "configured" if configured_slippage is not None else "derived",
        }
        return {
            "product": product,
            "mode": mode,
            "data_quality": quality,
            "features": features,
            "technical": technical,
            "costs": costs,
            "product_context": product_context,
            "observations": records,
            "as_of": latest_event.isoformat().replace("+00:00", "Z") if latest_event else None,
            "source_age_seconds": age,
            "session": _latest_session(records),
        }

    def _decision_from_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        candidates = snapshot.get("ranked_candidates", [])
        directional = [
            item for item in candidates
            if item.get("direction") in {"long", "short"}
            and (item.get("valid_for_shadow") or item.get("valid_for_user_execution"))
        ]
        top = directional[0] if directional else None
        no_trade = next(
            (item for item in candidates if item.get("direction") == "no_trade"),
            None,
        )
        selected = top or no_trade
        if snapshot.get("data_unavailable"):
            final_action = "data_unavailable"
            permission = "data_unavailable"
        elif selected is None:
            final_action = "no_trade"
            permission = "no_trade"
        else:
            final_action = selected.get("candidate_status") or "no_trade"
            permission = selected.get("execution_permission") or "data_unavailable"
        setup_verdict = (
            "actionable" if top and str(top.get("candidate_status", "")).startswith("actionable_")
            else "research_only" if top else "no_trade"
        )
        return {
            "schema_version": "2.0",
            "generated_at": snapshot.get("generated_at"),
            "snapshot_id": snapshot.get("snapshot_id"),
            "mode": snapshot.get("mode"),
            "synthetic": snapshot.get("synthetic", False),
            "data_unavailable": snapshot.get("data_unavailable", False),
            "data_quality": snapshot.get("data_quality", {}),
            "account_status": (snapshot.get("account_overlay") or {}).get("status", "data_unavailable"),
            "market_view": snapshot.get("computed_features", {}).get("regime", {}),
            "setup_verdict": setup_verdict,
            "setup_quality": selected.get("setup_quality") if selected else "unknown",
            "candidate_rank": candidates,
            "account_overlay": snapshot.get("account_overlay", {}),
            "portfolio_overlay": snapshot.get("portfolio_constraints", {}),
            "product_guard": {},
            "execution_permission": permission,
            "final_action": final_action,
            "warnings": snapshot.get("unsupported_data", []),
        }

    async def decision(self, *, mode: str | None = None, live: bool | None = None) -> dict[str, Any]:
        selected_mode = self._resolve_mode(mode, live)
        if self.last_decision is None or selected_mode != (self.last_decision or {}).get("mode"):
            await self.build_snapshot(mode=selected_mode)
        return self.last_decision or {"final_action": "data_unavailable", "mode": selected_mode}

    def data_health(self) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": self.last_snapshot.get("data_quality") if self.last_snapshot else {
                "quality": "data_unavailable",
                "score": 0,
                "missing": ["snapshot_not_generated"],
            },
        }

    def manual_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = classify(normalize_event(payload))
        self.events = [
            item.to_dict()
            for item in deduplicate([*map(_event_from_dict, self.events), event])
        ]
        return event.to_dict()

    def _unsupported_data(self) -> list[str]:
        return [
            f"{key}:{status.reason or status.status}"
            for key, status in self.registry.statuses.items()
            if status.status in {"unsupported", "temporarily_unavailable", "plan_not_available"}
        ]


def _record(value: Any) -> dict[str, Any]:
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def _technical(records: list[dict[str, Any]], minimum: int) -> dict[str, Any]:
    by_timeframe: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        data_type = str(record.get("data_type", ""))
        if not data_type.startswith("candle_"):
            continue
        timeframe = data_type.removeprefix("candle_")
        by_timeframe[timeframe].append(record.get("payload") or {})
    # The decision timeframe is explicit and never silently mixes 5m/15m/1h.
    selected = by_timeframe.get("15m")
    if not selected:
        selected = next(
            (by_timeframe[key] for key in ("5m", "1h", "4h", "1d") if by_timeframe.get(key)),
            [],
        )
    return closed_candle_features(selected, minimum_samples=min(30, minimum))


def _quality(records: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    if not records:
        return {
            "quality": "data_unavailable",
            "score": 0,
            "missing": ["no_observations"],
            "observation_count": 0,
            "mode": mode,
            "synthetic": mode == "fixture",
        }
    result = aggregate_quality(records)
    result["mode"] = mode
    result["synthetic"] = mode == "fixture"
    return result


def _return_series(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_timeframe: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        data_type = str(record.get("data_type", ""))
        if not data_type.startswith("candle_"):
            continue
        by_timeframe[data_type.removeprefix("candle_")].append(record)
    selected = next(
        (by_timeframe[key] for key in ("15m", "5m", "1h", "4h", "1d") if by_timeframe.get(key)),
        [],
    )
    candles = []
    for record in selected:
        payload = record.get("payload") or {}
        if payload.get("is_final") is not True or payload.get("close") is None:
            continue
        timestamp = payload.get("open_time") or record.get("source_event_time")
        try:
            close = float(payload["close"])
        except (TypeError, ValueError):
            continue
        candles.append((timestamp, close, payload.get("session")))
    candles.sort(key=lambda row: str(row[0]))
    output = []
    previous = None
    for timestamp, close, session in candles:
        if previous not in (None, 0):
            output.append({
                "timestamp": timestamp,
                "return": close / previous - 1,
                "session": session,
                "timeframe": "15m" if by_timeframe.get("15m") else (selected[0].get("data_type", "").removeprefix("candle_") if selected else None),
            })
        previous = close
    return output


def _latest_event_time(records: list[dict[str, Any]]) -> datetime | None:
    from engine_v2.domain.models import parse_datetime
    values = [
        parse_datetime(record.get("source_event_time") or (record.get("payload") or {}).get("open_time"))
        for record in records
    ]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _latest_session(records: list[dict[str, Any]]) -> str | None:
    for record in reversed(records):
        payload = record.get("payload") or {}
        if payload.get("session"):
            return payload.get("session")
    return None


def _directional(value: Any) -> float | None:
    try:
        return max(-1.0, min(1.0, float(value) * 10)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _latest_price(
    observations: list[Any],
    registry: AssetRegistry,
    underlying: str,
) -> float | None:
    latest: tuple[str, float] | None = None
    for observation in observations:
        product = registry.product(getattr(observation, "product_id", None))
        if product is None or product.underlying_id != underlying:
            continue
        payload = getattr(observation, "payload", {}) or {}
        if not str(getattr(observation, "data_type", "")).startswith("candle_"):
            continue
        try:
            close = float(payload.get("close"))
        except (TypeError, ValueError):
            continue
        event_time = str(getattr(observation, "source_event_time", "") or "")
        if latest is None or event_time > latest[0]:
            latest = (event_time, close)
    return latest[1] if latest else None


def _option_sort_key(product: Any, underlying_price: float | None) -> tuple[float, float]:
    instrument = product.capabilities.get("instrument", {}) if hasattr(product, "capabilities") else {}
    try:
        expiry = float(instrument.get("expiration_timestamp") or 0)
    except (TypeError, ValueError):
        expiry = 0.0
    try:
        strike = float(instrument.get("strike") or 0)
    except (TypeError, ValueError):
        strike = 0.0
    distance = abs(strike - underlying_price) if underlying_price is not None else 0.0
    return (expiry, distance)


def _option_states(
    records: list[dict[str, Any]],
    product_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("data_type") != "option":
            continue
        product = product_by_id.get(record.get("product_id"), {})
        underlying = str(product.get("underlying_id") or "")
        if not underlying:
            continue
        payload = dict(record.get("payload") or {})
        payload["source_product_id"] = record.get("product_id")
        grouped[underlying].append(payload)

    states: dict[str, dict[str, Any]] = {}
    for underlying, rows in grouped.items():
        surface = actual_delta_surface(rows)
        iv_rows = [
            row for row in rows
            if row.get("mark_iv") is not None and row.get("strike") is not None
        ]
        atm_iv = None
        if iv_rows:
            prices = [float(row["underlying_price"]) for row in iv_rows if row.get("underlying_price") is not None]
            reference_price = prices[-1] if prices else None
            if reference_price is not None:
                closest = min(iv_rows, key=lambda row: abs(float(row["strike"]) - reference_price))
                atm_iv = closest.get("mark_iv")
        states[underlying] = {
            **surface,
            "atm_iv": atm_iv,
            "sample_count": len(rows),
            "source": "deribit_public",
            "quality": "ok" if atm_iv is not None else surface.get("quality", "partial"),
        }
    return states


def _event_from_dict(value: dict[str, Any]):
    return normalize_event(value, discovered_via=value.get("discovered_via") or "stored")
