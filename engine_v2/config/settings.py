from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).replace("\r", "").replace("\n", "").strip()


def _bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() not in {"", "0", "false", "no", "off"}


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class V2Settings:
    enabled: bool = True
    legacy_engine_enabled: bool = False
    legacy_debate_enabled: bool = False
    shadow_mode: bool = True
    live_enabled: bool = False
    duckdb_path: str = "data/v2/engine.duckdb"
    parquet_root: str = "data/v2/raw"
    retention_days: int = 90
    min_data_quality: str = "partial"
    min_net_edge_bps: float = 8.0
    max_data_age_seconds: int = 900
    max_factor_exposure: float = 2.5
    max_event_risk: float = 0.8
    max_factor_positions: int = 2
    request_timeout_seconds: float = 8.0
    max_retries: int = 2
    default_timeframes: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d")
    lead_lag_bars: int = 6
    minimum_sample_count: int = 30
    minimum_overlap_ratio: float = 0.6
    minimum_stability: float = 0.25
    data_dir: str = "data/v2"

    @classmethod
    def from_env(cls) -> "V2Settings":
        return cls(
            enabled=_bool("ENGINE_V2_ENABLED", True),
            legacy_engine_enabled=_bool("LEGACY_ENGINE_ENABLED", False),
            legacy_debate_enabled=_bool("LEGACY_DEBATE_ENABLED", False),
            shadow_mode=_bool("ENGINE_V2_SHADOW_MODE", True),
            live_enabled=_bool("V2_LIVE_ENABLED", False),
            duckdb_path=_env("V2_DUCKDB_PATH", "data/v2/engine.duckdb"),
            parquet_root=_env("V2_PARQUET_ROOT", "data/v2/raw"),
            retention_days=_int("V2_RETENTION_DAYS", 90),
            min_data_quality=_env("V2_MIN_DATA_QUALITY", "partial"),
            min_net_edge_bps=_float("V2_MIN_NET_EDGE_BPS", 8.0),
            max_data_age_seconds=_int("V2_MAX_DATA_AGE_SECONDS", 900),
            max_factor_exposure=_float("V2_MAX_FACTOR_EXPOSURE", 2.5),
            max_event_risk=_float("V2_MAX_EVENT_RISK", 0.8),
            max_factor_positions=_int("V2_MAX_CORRELATED_POSITIONS", 2),
            request_timeout_seconds=_float("V2_REQUEST_TIMEOUT_SECONDS", 8.0),
            max_retries=_int("V2_MAX_RETRIES", 2),
            data_dir=_env("V2_DATA_DIR", "data/v2"),
        )

    def ensure_dirs(self, root: Path) -> None:
        (root / self.data_dir).mkdir(parents=True, exist_ok=True)
        (root / self.parquet_root).mkdir(parents=True, exist_ok=True)

    def public_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "legacy_engine_enabled": self.legacy_engine_enabled,
            "legacy_debate_enabled": self.legacy_debate_enabled,
            "shadow_mode": self.shadow_mode,
            "live_enabled": self.live_enabled,
            "retention_days": self.retention_days,
            "min_data_quality": self.min_data_quality,
            "min_net_edge_bps": self.min_net_edge_bps,
            "max_data_age_seconds": self.max_data_age_seconds,
            "max_factor_exposure": self.max_factor_exposure,
            "max_event_risk": self.max_event_risk,
            "lead_lag_bars": self.lead_lag_bars,
            "minimum_sample_count": self.minimum_sample_count,
            "minimum_overlap_ratio": self.minimum_overlap_ratio,
        }


settings = V2Settings.from_env()
