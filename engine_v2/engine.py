from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine_v2.config import V2Settings
from engine_v2.domain.registry import AssetRegistry, build_default_registry
from engine_v2.events import classify, deduplicate, normalize_event
from engine_v2.features import (
    aggregate_quality,
    classify_regime,
    closed_candle_features,
    dynamic_relationship,
    factor_state,
    funding_basis,
    orderbook_features,
    portfolio_concentration,
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
    ManualNewsProvider,
    MarketDataManager,
    OfficialEventsProvider,
)
from engine_v2.intelligence import build_snapshot, explain_snapshot, validate_claims
from engine_v2.opportunities import scan_opportunities
from engine_v2.storage import V2Storage

from .fixture import fixture_observations


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
        self.events: list[dict[str, Any]] = []
        self.last_snapshot: dict[str, Any] | None = None
        self.last_decision: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "engine": "v2",
            "enabled": self.settings.enabled,
            "shadow_mode": self.settings.shadow_mode,
            "legacy_engine_enabled": self.settings.legacy_engine_enabled,
            "legacy_debate_enabled": self.settings.legacy_debate_enabled,
            "storage": self.storage.status(),
            "provider_count": len(self.manager.providers),
            "registered_products": len(self.registry.products),
        }

    async def discover(self) -> dict[str, Any]:
        results = await self.manager.discover(["BTC", "ETH", "SOXL", "SK_HYNIX_KRX"])
        return {
            "results": [result.to_dict() for result in results],
            "registry": self.registry.to_dict(),
            "provider_health": self.manager.health(),
        }

    async def build_snapshot(self, *, live: bool = False) -> dict[str, Any]:
        if live and self.settings.live_enabled:
            observations = await self._live_observations()
        else:
            observations = fixture_observations()
            self.storage.append_observations(observations)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for observation in observations:
            grouped[observation.data_type].append(observation.payload)
        candles = grouped.get("candle_15m", [])
        technical = closed_candle_features(
            candles,
            minimum_samples=min(30, self.settings.minimum_sample_count),
        )
        cvd = trade_cvd(grouped.get("trade", []))
        orderbook = orderbook_features(
            grouped["orderbook"][-1],
            mid_price=technical.get("latest_close"),
        ) if grouped.get("orderbook") else {
            "quality": "partial",
            "reason": "orderbook_snapshot_missing",
        }
        quality = aggregate_quality([
            {
                "quality": observation.quality.value,
                "reason": observation.reason,
                "data_type": observation.data_type,
            }
            for observation in observations
        ])
        return self._assemble_snapshot(observations, technical, cvd, orderbook, quality)

    async def _live_observations(self) -> list[Any]:
        await self.manager.discover(["BTC"])
        observations = []
        for product_id in ("BTC_BINANCE_PERP", "BTC_BYBIT_PERP"):
            result = await self.manager.backfill(product_id, timeframe="15m", limit=300)
            observations.extend(result.data)
        return observations

    def _assemble_snapshot(self, observations, technical, cvd, orderbook, quality) -> dict[str, Any]:
        derivative_rows: list[dict[str, Any]] = []
        for observation in observations:
            if observation.data_type == "open_interest":
                row = dict(observation.payload)
                row["venue"] = observation.venue
                row["price"] = technical.get("latest_close")
                if row.get("open_interest_usd") is None:
                    row["open_interest_usd"] = row.get("open_interest_value")
                derivative_rows.append(row)
            elif observation.data_type == "mark_funding":
                row = dict(observation.payload)
                row["venue"] = observation.venue
                mark = row.get("mark_price")
                index = row.get("index_price")
                if row.get("basis") is None and mark and index:
                    row["basis"] = (float(mark) / float(index)) - 1
                derivative_rows.append(row)
        derivatives = {
            **weighted_oi(derivative_rows, price=technical.get("latest_close")),
            **funding_basis(derivative_rows),
        }
        cross_asset = dynamic_relationship([], [], minimum_samples=30)
        product_rows = [product.to_dict() for product in self.registry.tradable_products()]
        regime = classify_regime({
            **technical,
            "cross_asset_state": cross_asset.get("state"),
            "weighted_funding": derivatives.get("weighted_funding"),
            "liquidity_vacuum_score": orderbook.get("liquidity_vacuum_score"),
        })
        feature_values = {
            "technical_structure": _directional_value(technical.get("return_24")),
            "momentum": _directional_value(technical.get("return_4")),
            "orderflow": _directional_value(cvd.get("trade_cvd")),
            "derivatives": _directional_value(derivatives.get("weighted_funding")),
            "cross_asset": _directional_value(cross_asset.get("ew_corr")),
            "event": None,
            "liquidity": _directional_value(orderbook.get("liquidity_vacuum_score")),
            "product_risk": 0,
            "portfolio_fit": 0,
            "technical": technical,
            "microstructure": {"trade_cvd": cvd, "orderbook": orderbook},
            "derivatives_state": derivatives,
            "cross_asset_state": cross_asset,
            "regime": regime,
        }
        factors = factor_state({"BTC": technical.get("return_24"), "ETH": None, "QQQ": None})
        base = {
            "features": feature_values,
            "data_quality": quality,
            "costs": {"spread_bps": orderbook.get("spread_bps") or 3.0, "estimated_slippage_bps": 2.0},
            "product_context": {},
            "snapshot_id": None,
        }
        candidates = scan_opportunities(
            product_rows[:3],
            base,
            min_net_edge_bps=self.settings.min_net_edge_bps,
        )
        candidate_dicts = [candidate.to_dict() for candidate in candidates]
        snapshot = build_snapshot(
            registry=self.registry.to_dict(),
            observations=[observation.to_dict() for observation in observations],
            features=feature_values,
            data_quality=quality,
            factor_state=factors,
            event_state=self.events,
            ranked_candidates=candidate_dicts,
            portfolio_constraints=portfolio_concentration([]),
            unsupported_data=self._unsupported_data(),
        )
        snapshot["explanation"] = explain_snapshot(snapshot)
        snapshot["critic"] = validate_claims(snapshot["explanation"], snapshot)
        self.last_snapshot = snapshot
        self.last_decision = self._decision_from_snapshot(snapshot)
        self.storage.save_decision(snapshot["snapshot_id"], datetime.now(timezone.utc), self.last_decision)
        return snapshot

    def _decision_from_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        candidates = snapshot.get("ranked_candidates", [])
        top = next(
            (item for item in candidates if item.get("valid") and item.get("direction") != "no_trade"),
            None,
        )
        no_trade = next(
            (item for item in candidates if item.get("direction") == "no_trade"),
            None,
        )
        selected = top or no_trade
        final_action = selected.get("direction", "data_unavailable") if selected else "data_unavailable"
        if selected and not selected.get("valid") and final_action != "no_trade":
            final_action = "no_trade"
        return {
            "schema_version": "2.0",
            "generated_at": snapshot.get("generated_at"),
            "snapshot_id": snapshot.get("snapshot_id"),
            "market_view": snapshot.get("computed_features", {}).get("regime", {}),
            "setup_verdict": "actionable" if top else "no_trade",
            "setup_quality": selected.get("setup_quality") if selected else "unknown",
            "candidate_rank": candidates,
            "account_overlay": {
                "execution_permission": "data_unavailable",
                "reason": "account_overlay_not_loaded",
            },
            "portfolio_overlay": snapshot.get("portfolio_constraints", {}),
            "product_guard": {},
            "execution_permission": "data_unavailable",
            "final_action": final_action,
            "warnings": snapshot.get("unsupported_data", []),
        }

    async def decision(self, *, live: bool = False) -> dict[str, Any]:
        if self.last_decision is None or live:
            await self.build_snapshot(live=live)
        return self.last_decision or {"final_action": "data_unavailable"}

    def data_health(self) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": self.last_snapshot.get("data_quality") if self.last_snapshot else {
                "quality": "partial",
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
            if status.status in {"unsupported", "temporarily_unavailable"}
        ]


def _directional_value(value: Any) -> float | None:
    try:
        return max(-1.0, min(1.0, float(value) * 10)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _event_from_dict(value: dict[str, Any]):
    return normalize_event(value, discovered_via=value.get("discovered_via") or "stored")
