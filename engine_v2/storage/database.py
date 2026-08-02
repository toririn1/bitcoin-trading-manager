from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from engine_v2.domain.models import Observation, to_dict


class V2Storage:
    """Normalized database plus partitioned raw storage.

    DuckDB is the primary backend whenever installed. SQLite is an explicit
    development fallback only. Parquet is the primary raw format when PyArrow
    is available; JSONL is opt-in audit output.
    """

    def __init__(
        self,
        root: str | Path = "data/v2",
        duckdb_path: str | Path = "data/v2/engine.duckdb",
        parquet_root: str | Path = "data/v2/raw",
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.duckdb_path = Path(duckdb_path)
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        self.parquet_root = Path(parquet_root)
        self.parquet_root.mkdir(parents=True, exist_ok=True)
        self._db: Any = None
        self._sqlite = None
        self.backend = "duckdb"
        self.parquet_available = False
        self.raw_format = "jsonl_audit_only"
        requested_backend = os.getenv("V2_STORAGE_BACKEND", "duckdb").strip().lower()
        if requested_backend == "sqlite":
            self._sqlite = sqlite3.connect(self.root / "engine.sqlite3", check_same_thread=False)
            self._sqlite.row_factory = sqlite3.Row
            self._db = self._sqlite
            self.backend = "sqlite_explicit"
            self._init_schema()
        elif requested_backend != "duckdb":
            raise ValueError("V2_STORAGE_BACKEND must be duckdb or explicit sqlite")
        else:
            try:
                import duckdb  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "DuckDB is required; install duckdb or explicitly set V2_STORAGE_BACKEND=sqlite"
                ) from exc
            self._db = duckdb.connect(str(self.duckdb_path))
            self._init_schema()
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.parquet_available = False
        else:
            self.parquet_available = True
            self.raw_format = "parquet"
        self.audit_jsonl = os.getenv("V2_JSONL_AUDIT_COPY", "false").lower() in {"1", "true", "yes"}

    def _init_schema(self) -> None:
        schema_sql = """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                observation_id VARCHAR PRIMARY KEY,
                provider VARCHAR NOT NULL,
                venue VARCHAR,
                product_id VARCHAR,
                data_type VARCHAR NOT NULL,
                source_event_time VARCHAR,
                source_publish_time VARCHAR,
                first_seen_at VARCHAR NOT NULL,
                collected_at VARCHAR NOT NULL,
                available_at VARCHAR,
                processed_at VARCHAR,
                quality VARCHAR NOT NULL,
                schema_version VARCHAR NOT NULL,
                reason VARCHAR,
                payload_hash VARCHAR UNIQUE,
                payload_json VARCHAR NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_observations_available ON observations(available_at);
            CREATE INDEX IF NOT EXISTS idx_observations_type_product ON observations(data_type, product_id);
            CREATE TABLE IF NOT EXISTS features (
                feature_id VARCHAR PRIMARY KEY,
                snapshot_id VARCHAR,
                feature_name VARCHAR NOT NULL,
                as_of VARCHAR,
                quality VARCHAR NOT NULL,
                value_json VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                snapshot_id VARCHAR PRIMARY KEY,
                decision_time VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outcomes (
                outcome_id VARCHAR PRIMARY KEY,
                decision_time VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            );
            """
        if self.backend == "sqlite_explicit":
            self._db.executescript(schema_sql)
        else:
            self._db.execute(schema_sql)
        self._db.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (1, self._timestamp(datetime.now(timezone.utc)),
        )
        )
        self._db.commit()

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        if value is None:
            return None
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _payload(observation: Observation) -> tuple[str, str]:
        payload = json.dumps(to_dict(observation.payload), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode()).hexdigest(), payload

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "raw_backend": self.raw_format,
            "parquet_available": self.parquet_available,
            "jsonl_audit_copy": self.audit_jsonl,
            "duckdb_path": str(self.duckdb_path) if self.backend == "duckdb" else None,
            "sqlite_path": str(self.root / "engine.sqlite3") if self.backend == "sqlite_explicit" else None,
            "parquet_root": str(self.parquet_root),
            "schema_version": 1,
        }

    def _existing(self, observations: list[Observation]) -> tuple[set[str], set[str]]:
        hashes: list[str] = []
        ids: list[str] = []
        for observation in observations:
            payload_hash, _ = self._payload(observation)
            hashes.append(payload_hash)
            ids.append(observation.observation_id)
        if not hashes:
            return set(), set()
        placeholders = ",".join("?" for _ in hashes)
        existing_hashes = {
            row[0]
            for row in self._db.execute(
                f"SELECT payload_hash FROM observations WHERE payload_hash IN ({placeholders})",
                hashes,
            ).fetchall()
        }
        placeholders = ",".join("?" for _ in ids)
        existing_ids = {
            row[0]
            for row in self._db.execute(
                f"SELECT observation_id FROM observations WHERE observation_id IN ({placeholders})",
                ids,
            ).fetchall()
        }
        return existing_hashes, existing_ids

    def append_observation(self, observation: Observation) -> bool:
        return self.append_observations([observation]) == 1

    def append_observations(self, observations: Iterable[Observation]) -> int:
        rows = list(observations)
        if not rows:
            return 0
        existing_hashes, existing_ids = self._existing(rows)
        candidates: list[tuple[Observation, str, str]] = []
        seen_hashes = set(existing_hashes)
        seen_ids = set(existing_ids)
        for observation in rows:
            payload_hash, payload_json = self._payload(observation)
            if payload_hash in seen_hashes or observation.observation_id in seen_ids:
                continue
            candidates.append((observation, payload_hash, payload_json))
            seen_hashes.add(payload_hash)
            seen_ids.add(observation.observation_id)
        if not candidates:
            return 0
        params = []
        for observation, payload_hash, payload_json in candidates:
            params.append((
                observation.observation_id,
                observation.provider,
                observation.venue,
                observation.product_id,
                observation.data_type,
                self._timestamp(observation.source_event_time),
                self._timestamp(observation.source_publish_time),
                self._timestamp(observation.first_seen_at),
                self._timestamp(observation.collected_at),
                self._timestamp(observation.available_at),
                self._timestamp(observation.processed_at),
                observation.quality.value,
                observation.schema_version,
                observation.reason,
                payload_hash,
                payload_json,
            ))
        self._db.execute("BEGIN TRANSACTION")
        try:
            self._db.executemany(
                """
                INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                params,
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        for observation, payload_hash, _ in candidates:
            self._write_raw(observation, payload_hash)
        return len(candidates)

    def _write_raw(self, observation: Observation, payload_hash: str) -> None:
        event_date = (observation.source_event_time or observation.collected_at).date().isoformat()
        directory = self.parquet_root / f"provider={_safe(observation.provider)}" / f"type={_safe(observation.data_type)}" / f"date={event_date}"
        directory.mkdir(parents=True, exist_ok=True)
        record = observation.to_dict()
        record.update({
            "payload_hash": payload_hash,
            "source_event_time": self._timestamp(observation.source_event_time),
            "collected_at": self._timestamp(observation.collected_at),
            "available_at": self._timestamp(observation.available_at),
            "payload_json": json.dumps(to_dict(observation.payload), sort_keys=True, default=str),
        })
        if self.parquet_available:
            import pyarrow as pa
            import pyarrow.parquet as pq
            table = pa.Table.from_pylist([_flat_record(record)])
            pq.write_table(table, directory / f"part-{uuid4().hex}.parquet", compression="zstd")
        if self.audit_jsonl:
            path = directory / "records.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")

    def _rows(self, cursor: Any) -> list[dict[str, Any]]:
        if self.backend == "sqlite_explicit":
            return [dict(row) for row in cursor.fetchall()]
        names = [description[0] for description in cursor.description or []]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def observations(
        self,
        *,
        data_type: str | None = None,
        product_id: str | None = None,
        decision_time: datetime | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if data_type:
            clauses.append("data_type = ?")
            params.append(data_type)
        if product_id:
            clauses.append("product_id = ?")
            params.append(product_id)
        if decision_time:
            clauses.append("available_at IS NOT NULL AND available_at <= ?")
            params.append(self._timestamp(decision_time))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cursor = self._db.execute(
            "SELECT * FROM observations" + where + " ORDER BY COALESCE(source_event_time, collected_at) DESC LIMIT ?",
            [*params, limit],
        )
        rows = self._rows(cursor)
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return rows

    def save_features(self, snapshot_id: str, values: Iterable[dict[str, Any]]) -> None:
        params = []
        for value in values:
            feature_id = hashlib.sha256(f"{snapshot_id}:{value.get('name')}".encode()).hexdigest()
            params.append((
                feature_id,
                snapshot_id,
                value.get("name"),
                value.get("as_of"),
                value.get("quality", "unknown"),
                json.dumps(value.get("value"), default=str),
            ))
        if params:
            self._db.executemany("INSERT OR REPLACE INTO features VALUES (?, ?, ?, ?, ?, ?)", params)
            self._db.commit()

    def save_decision(self, snapshot_id: str, decision_time: datetime, payload: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?)",
            (snapshot_id, self._timestamp(decision_time), json.dumps(payload, ensure_ascii=False, default=str)),
        )
        self._db.commit()

    def save_outcome(self, outcome_id: str, decision_time: datetime, payload: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO outcomes VALUES (?, ?, ?)",
            (outcome_id, self._timestamp(decision_time), json.dumps(payload, ensure_ascii=False, default=str)),
        )
        self._db.commit()

    def evaluation_summary(self) -> dict[str, Any]:
        decision_row = self._db.execute("SELECT COUNT(*) FROM decisions").fetchone()
        outcome_row = self._db.execute("SELECT COUNT(*) FROM outcomes").fetchone()
        return {
            "decision_count": int(decision_row[0] if decision_row else 0),
            "outcome_count": int(outcome_row[0] if outcome_row else 0),
            "backend": self.backend,
            "raw_backend": self.raw_format,
        }

    def close(self) -> None:
        if self._db is not None:
            self._db.close()


def _flat_record(record: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for key, value in record.items():
        if isinstance(value, (dict, list)):
            output[key] = json.dumps(value, ensure_ascii=False, default=str)
        elif isinstance(value, datetime):
            output[key] = value.isoformat()
        else:
            output[key] = value
    return output


def _safe(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"_", "-", "."} else "_" for character in str(value))
