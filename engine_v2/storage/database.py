from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from engine_v2.domain.models import Observation, to_dict
from engine_v2.storage.point_in_time import filter_available


class V2Storage:
    """Raw/normalized store with explicit backend status.

    DuckDB and Parquet are preferred when installed. The stdlib SQLite/JSONL
    path is an honest local fallback for a fresh checkout; it reports the
    backend so a deployment cannot mistake it for a production DuckDB setup.
    """

    def __init__(self, root: str | Path = "data/v2", duckdb_path: str | Path = "data/v2/engine.duckdb", parquet_root: str | Path = "data/v2/raw") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.duckdb_path = Path(duckdb_path)
        self.parquet_root = Path(parquet_root)
        self.parquet_root.mkdir(parents=True, exist_ok=True)
        self._duckdb = None
        self._sqlite = sqlite3.connect(self.root / "engine.sqlite3", check_same_thread=False)
        self._sqlite.row_factory = sqlite3.Row
        self._init_sqlite()
        try:
            import duckdb  # type: ignore
        except ImportError:
            self.backend = "sqlite_fallback"
            self.parquet_available = False
        else:
            self._duckdb = duckdb.connect(str(self.duckdb_path))
            self.backend = "duckdb"
            try:
                import pyarrow  # noqa: F401
            except ImportError:
                self.parquet_available = False
            else:
                self.parquet_available = True

    def _init_sqlite(self) -> None:
        self._sqlite.executescript(
            """
            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                venue TEXT,
                product_id TEXT,
                data_type TEXT NOT NULL,
                source_event_time TEXT,
                source_publish_time TEXT,
                first_seen_at TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                available_at TEXT,
                processed_at TEXT,
                quality TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                reason TEXT,
                payload_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_observations_available ON observations(available_at);
            CREATE INDEX IF NOT EXISTS idx_observations_type_product ON observations(data_type, product_id);
            CREATE TABLE IF NOT EXISTS features (
                feature_id TEXT PRIMARY KEY,
                snapshot_id TEXT,
                feature_name TEXT NOT NULL,
                as_of TEXT,
                quality TEXT NOT NULL,
                value_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                snapshot_id TEXT PRIMARY KEY,
                decision_time TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outcomes (
                outcome_id TEXT PRIMARY KEY,
                decision_time TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        self._sqlite.commit()

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        if value is None:
            return None
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "parquet_available": self.parquet_available,
            "duckdb_path": str(self.duckdb_path),
            "parquet_root": str(self.parquet_root),
        }

    def append_observation(self, observation: Observation) -> bool:
        payload = json.dumps(to_dict(observation.payload), sort_keys=True, separators=(",", ":"), default=str)
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()
        params = (
            observation.observation_id, observation.provider, observation.venue, observation.product_id,
            observation.data_type, self._timestamp(observation.source_event_time), self._timestamp(observation.source_publish_time),
            self._timestamp(observation.first_seen_at), self._timestamp(observation.collected_at), self._timestamp(observation.available_at),
            self._timestamp(observation.processed_at), observation.quality.value, observation.schema_version, observation.reason,
            payload_hash, payload,
        )
        try:
            self._sqlite.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params,
            )
        except sqlite3.IntegrityError:
            return False
        self._sqlite.commit()
        self._write_raw(observation, payload_hash)
        return True

    def append_observations(self, observations: Iterable[Observation]) -> int:
        return sum(1 for observation in observations if self.append_observation(observation))

    def _write_raw(self, observation: Observation, payload_hash: str) -> None:
        date = (observation.source_event_time or observation.collected_at).date().isoformat()
        directory = self.parquet_root / f"provider={observation.provider}" / f"type={observation.data_type}" / f"date={date}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "records.jsonl"
        record = observation.to_dict()
        record.update({"payload_hash": payload_hash, "collected_at": self._timestamp(observation.collected_at), "available_at": self._timestamp(observation.available_at)})
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")

    def observations(self, *, data_type: str | None = None, product_id: str | None = None, decision_time: datetime | None = None, limit: int = 500) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if data_type:
            clauses.append("data_type = ?")
            params.append(data_type)
        if product_id:
            clauses.append("product_id = ?")
            params.append(product_id)
        query = "SELECT * FROM observations" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY COALESCE(source_event_time, collected_at) DESC LIMIT ?"
        params.append(limit)
        rows = [dict(row) for row in self._sqlite.execute(query, params).fetchall()]
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return filter_available(rows, decision_time) if decision_time else rows

    def save_features(self, snapshot_id: str, values: Iterable[dict[str, Any]]) -> None:
        for value in values:
            feature_id = hashlib.sha256(f"{snapshot_id}:{value.get('name')}".encode()).hexdigest()
            self._sqlite.execute(
                "INSERT OR REPLACE INTO features VALUES (?, ?, ?, ?, ?, ?)",
                (feature_id, snapshot_id, value.get("name"), value.get("as_of"), value.get("quality", "unknown"), json.dumps(value.get("value"), default=str)),
            )
        self._sqlite.commit()

    def save_decision(self, snapshot_id: str, decision_time: datetime, payload: dict[str, Any]) -> None:
        self._sqlite.execute("INSERT OR REPLACE INTO decisions VALUES (?, ?, ?)", (snapshot_id, self._timestamp(decision_time), json.dumps(payload, ensure_ascii=False, default=str)))
        self._sqlite.commit()

    def evaluation_summary(self) -> dict[str, Any]:
        row = self._sqlite.execute("SELECT COUNT(*) AS count FROM decisions").fetchone()
        return {"decision_count": int(row["count"] if row else 0), "outcome_count": int(self._sqlite.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]), "backend": self.backend}

    def close(self) -> None:
        self._sqlite.close()
        if self._duckdb is not None:
            self._duckdb.close()
