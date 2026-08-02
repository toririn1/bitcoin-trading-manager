from __future__ import annotations

import hashlib
import json
from enum import Enum
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .enums import (
    AssetClass,
    DataQuality,
    Direction,
    EntryPlan,
    EventCategory,
    EventStatus,
    Horizon,
    ProductType,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    value = ensure_utc(value)
    return value.isoformat().replace("+00:00", "Z") if value else None


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return ensure_utc(value)
    text = str(value).replace("Z", "+00:00")
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, Enum):
        return value.value
    return str(value)


class EnumLike:
    value: Any


def to_dict(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(k): to_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dict(item) for item in value]
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass(slots=True)
class AssetSpec:
    asset_id: str
    display_name: str
    underlying_id: str | None = None
    asset_class: AssetClass = AssetClass.CRYPTO
    base_currency: str | None = None
    quote_currency: str | None = None
    country: str | None = None
    timezone: str = "UTC"
    calendar_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class ProductSpec:
    product_id: str
    underlying_id: str
    provider: str
    venue: str
    venue_symbol: str
    product_type: ProductType
    quote_currency: str | None = None
    settlement_currency: str | None = None
    contract_size: float | None = None
    tick_size: float | None = None
    lot_size: float | None = None
    min_order_size: float | None = None
    max_leverage: float | None = None
    daily_target_leverage: float | None = None
    reference_product_id: str | None = None
    funding_supported: bool = False
    short_supported: bool = False
    trading_session: str | None = None
    price_source: str | None = None
    is_tradable: bool = False
    capabilities: dict[str, Any] = field(default_factory=dict)
    discovered_at: datetime | None = None
    role: str | None = None
    execution_venue: str | None = None
    market_data_provider: str | None = None
    maker_fee_bps: float | None = None
    taker_fee_bps: float | None = None
    estimated_slippage_bps: float | None = None

    def __post_init__(self) -> None:
        self.discovered_at = ensure_utc(self.discovered_at)
        if not self.product_id or not self.underlying_id or not self.venue_symbol:
            raise ValueError("product_id, underlying_id, and venue_symbol are required")
        self.role = self.role or ("tradable" if self.is_tradable else "reference")
        if self.role not in {"tradable", "reference"}:
            raise ValueError("ProductSpec.role must be tradable or reference")
        self.execution_venue = self.execution_venue or self.venue
        self.market_data_provider = self.market_data_provider or self.provider

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class Observation:
    observation_id: str
    provider: str
    venue: str | None
    product_id: str | None
    data_type: str
    source_event_time: datetime | None
    source_publish_time: datetime | None
    first_seen_at: datetime
    collected_at: datetime
    available_at: datetime | None
    processed_at: datetime | None
    quality: DataQuality
    schema_version: str
    payload: dict[str, Any]
    reason: str | None = None

    def __post_init__(self) -> None:
        self.source_event_time = ensure_utc(self.source_event_time)
        self.source_publish_time = ensure_utc(self.source_publish_time)
        self.first_seen_at = ensure_utc(self.first_seen_at) or utc_now()
        self.collected_at = ensure_utc(self.collected_at) or utc_now()
        self.available_at = ensure_utc(self.available_at)
        self.processed_at = ensure_utc(self.processed_at)
        if self.source_event_time is None and self.quality == DataQuality.OK:
            self.quality = DataQuality.TIMESTAMP_UNKNOWN
            self.reason = self.reason or "source_event_time_missing"

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))

    @property
    def payload_hash(self) -> str:
        raw = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), default=_json_default)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(slots=True)
class Candle:
    product_id: str
    timeframe: str
    open_time: datetime
    close_time: datetime | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    quote_volume: float | None = None
    trade_count: int | None = None
    is_final: bool | None = False
    source: str = ""
    collected_at: datetime | None = None
    available_at: datetime | None = None
    quality: DataQuality = DataQuality.OK

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class TradeExecutionActual:
    product_id: str
    trade_id: str | None
    price: float
    quantity: float
    aggressor_side: str | None
    event_time: datetime | None
    source: str
    quality: DataQuality = DataQuality.OK

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class LiquidationEventActual:
    product_id: str
    event_id: str
    side: str
    quantity: float | None
    price: float | None
    notional_usd: float | None
    event_time: datetime | None
    source: str
    quality: DataQuality = DataQuality.OK

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class LiquidationAggregateActual:
    product_id: str
    window: str
    long_usd: float | None
    short_usd: float | None
    event_count: int | None
    start_time: datetime | None
    end_time: datetime | None
    source: str
    quality: DataQuality = DataQuality.OK

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class LiquidationSnapshotPartial:
    product_id: str
    long_usd: float | None
    short_usd: float | None
    event_time: datetime | None
    source: str
    quality: DataQuality = DataQuality.PARTIAL

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class LiquidationClusterEstimate:
    product_id: str
    side: str
    price: float | None
    distance_pct: float | None
    intensity: float | None
    age_seconds: float | None
    source: str
    quality: DataQuality = DataQuality.ESTIMATED

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class Event:
    event_id: str
    headline: str
    original_source: str | None
    discovered_via: str | None
    source_url: str | None
    published_at: datetime | None
    event_time: datetime | None
    first_seen_at: datetime
    confirmed_at: datetime | None = None
    revised_at: datetime | None = None
    category: EventCategory = EventCategory.UNKNOWN
    subcategory: str | None = None
    affected_assets: list[str] = field(default_factory=list)
    affected_factors: list[str] = field(default_factory=list)
    expected: float | None = None
    actual: float | None = None
    previous: float | None = None
    previous_revised: float | None = None
    surprise: float | None = None
    surprise_z: float | None = None
    novelty: float | None = None
    source_reliability: float | None = None
    already_priced_probability: float | None = None
    expected_horizon: str | None = None
    half_life_minutes: float | None = None
    status: EventStatus = EventStatus.REPORTED
    summary: str | None = None
    raw_payload_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class OpportunityCandidate:
    candidate_id: str
    created_at: datetime
    product_id: str
    direction: Direction
    horizon: Horizon
    entry_plan: EntryPlan
    invalidation: str | None
    targets: list[float] = field(default_factory=list)
    setup_quality: str = "unknown"
    heuristic_setup_score: float | None = None
    edge_quality: str = "uncalibrated"
    cost_quality: str = "missing"
    mode: str = "live"
    session: str | None = None
    technical_score: float | None = None
    momentum_score: float | None = None
    orderflow_score: float | None = None
    derivatives_score: float | None = None
    cross_asset_score: float | None = None
    event_score: float | None = None
    data_quality_score: float | None = None
    liquidity_score: float | None = None
    product_risk_score: float | None = None
    portfolio_fit_score: float | None = None
    gross_edge_bps: float | None = None
    estimated_cost_bps: float | None = None
    net_edge_bps: float | None = None
    confidence: float | None = None
    reason_codes: list[str] = field(default_factory=list)
    risk_codes: list[str] = field(default_factory=list)
    source_snapshot_id: str | None = None
    valid: bool = True
    candidate_status: str = "no_trade"
    valid_for_shadow: bool = False
    valid_for_user_execution: bool = False
    setup_type: str = "unknown"
    trigger_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    time_expiry: datetime | None = None
    invalidation_reason: str | None = None
    execution_permission: str = "data_unavailable"
    calibration_group: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class DecisionPackage:
    schema_version: str
    generated_at: datetime
    snapshot_id: str
    market_view: dict[str, Any]
    setup_verdict: str
    setup_quality: str
    candidate_rank: list[dict[str, Any]]
    account_overlay: dict[str, Any]
    portfolio_overlay: dict[str, Any]
    product_guard: dict[str, Any]
    execution_permission: str
    final_action: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))


@dataclass(slots=True)
class FeatureValue:
    name: str
    value: Any
    unit: str | None
    source_snapshot_id: str | None
    as_of: datetime | None
    quality: DataQuality
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_dict(asdict(self))
