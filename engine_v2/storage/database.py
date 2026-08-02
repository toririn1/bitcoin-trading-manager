from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from engine_v2.domain.models import Observation, parse_datetime, to_dict


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
            CREATE TABLE IF NOT EXISTS shadow_candidates (
                candidate_id VARCHAR PRIMARY KEY,
                snapshot_id VARCHAR,
                decision_time VARCHAR NOT NULL,
                product_id VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                opened_at VARCHAR NOT NULL,
                closed_at VARCHAR,
                outcome_id VARCHAR,
                payload_json VARCHAR NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_shadow_candidates_status
                ON shadow_candidates(status, product_id, direction);
            """
        if self.backend == "sqlite_explicit":
            self._db.executescript(schema_sql)
        else:
            self._db.execute(schema_sql)
        for version in (1, 2):
            self._db.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (version, self._timestamp(datetime.now(timezone.utc))),
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
            "schema_version": 2,
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
        self._write_raw_batch(candidates)
        return len(candidates)

    def _write_raw_batch(self, candidates: list[tuple[Observation, str, str]]) -> None:
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for observation, payload_hash, _ in candidates:
            event_date = (observation.source_event_time or observation.collected_at).date().isoformat()
            key = (
                _safe(observation.provider),
                _safe(observation.data_type),
                event_date,
            )
            record = observation.to_dict()
            record.update({
                "payload_hash": payload_hash,
                "source_event_time": self._timestamp(observation.source_event_time),
                "collected_at": self._timestamp(observation.collected_at),
                "available_at": self._timestamp(observation.available_at),
                "payload_json": json.dumps(to_dict(observation.payload), sort_keys=True, default=str),
            })
            grouped.setdefault(key, []).append(_flat_record(record))
        for (provider, data_type, event_date), records in grouped.items():
            directory = self.parquet_root / f"provider={provider}" / f"type={data_type}" / f"date={event_date}"
            directory.mkdir(parents=True, exist_ok=True)
            if self.parquet_available:
                import os
                import pyarrow as pa
                import pyarrow.parquet as pq
                table = pa.Table.from_pylist(records)
                temporary = directory / f".tmp-{uuid4().hex}.parquet"
                target = directory / f"part-{uuid4().hex}.parquet"
                pq.write_table(table, temporary, compression="zstd")
                os.replace(temporary, target)
            if self.audit_jsonl:
                path = directory / "records.jsonl"
                with path.open("a", encoding="utf-8") as handle:
                    for record in records:
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

    def candle_history(
        self,
        product_id: str,
        timeframe: str,
        *,
        limit: int = 2000,
        decision_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return unique candle payloads in chronological order.

        The payload with the same product/timeframe/open_time is represented once;
        a final candle supersedes a previously stored forming candle.
        """
        clauses = ["product_id = ?", "data_type = ?"]
        params: list[Any] = [product_id, f"candle_{timeframe}"]
        if decision_time is not None:
            clauses.append("available_at IS NOT NULL AND available_at <= ?")
            params.append(self._timestamp(decision_time))
        rows = self._rows(self._db.execute(
            "SELECT source_event_time, collected_at, quality, reason, payload_json "
            "FROM observations WHERE " + " AND ".join(clauses) +
            " ORDER BY COALESCE(source_event_time, collected_at) ASC",
            params,
        ))
        by_open: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = json.loads(row.pop("payload_json"))
            key = str(payload.get("open_time") or row.get("source_event_time") or "")
            if not key:
                continue
            current = by_open.get(key)
            if current is None or bool(payload.get("is_final")) and not bool(current.get("is_final")):
                payload.setdefault("quality", row.get("quality"))
                payload.setdefault("storage_reason", row.get("reason"))
                by_open[key] = payload
        values = list(by_open.values())
        values.sort(key=lambda item: str(item.get("open_time") or ""))
        return values[-max(1, int(limit)):]

    def history_readiness(
        self,
        product_id: str,
        timeframe: str,
        *,
        requested: int,
        minimum_closed: int = 30,
        decision_time: datetime | None = None,
    ) -> dict[str, Any]:
        rows = self.candle_history(
            product_id,
            timeframe,
            limit=max(requested * 2, requested + 10, 5000),
            decision_time=decision_time,
        )
        closed = [row for row in rows if row.get("is_final") is True]
        forming = [row for row in rows if row.get("is_final") is False]
        timestamps = []
        for row in closed:
            value = parse_datetime(row.get("open_time"))
            if value:
                timestamps.append(value)
        timestamps.sort()
        interval_seconds = {
            "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
            "4h": 14400, "1d": 86400, "1w": 604800,
        }.get(timeframe)
        gap_count = 0
        if interval_seconds and len(timestamps) > 1:
            for previous, current in zip(timestamps, timestamps[1:]):
                delta = (current - previous).total_seconds()
                if delta > interval_seconds * 1.5:
                    gap_count += max(0, int(round(delta / interval_seconds)) - 1)
        return {
            "product_id": product_id,
            "timeframe": timeframe,
            "requested_samples": requested,
            "closed_count": len(closed),
            "forming_count": len(forming),
            "first_closed_at": timestamps[0].isoformat().replace("+00:00", "Z") if timestamps else None,
            "last_closed_at": timestamps[-1].isoformat().replace("+00:00", "Z") if timestamps else None,
            "gap_count": gap_count,
            "duplicate_count": max(0, len(rows) - len({str(row.get("open_time")) for row in rows})),
            "readiness": "ready" if len(closed) >= max(minimum_closed, requested) else "insufficient_data",
            "analysis_ready": len(closed) >= max(minimum_closed, requested),
        }

    def history_summary(
        self,
        products: Iterable[Any],
        limits: dict[str, int],
        *,
        minimum_closed: int = 30,
        decision_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        output = []
        for product in products:
            for timeframe, requested in limits.items():
                output.append(self.history_readiness(
                    product.product_id if hasattr(product, "product_id") else str(product.get("product_id")),
                    timeframe,
                    requested=requested,
                    minimum_closed=minimum_closed,
                    decision_time=decision_time,
                ))
        return output

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

    def save_candidates(
        self,
        snapshot_id: str,
        decision_time: datetime,
        candidates: Iterable[dict[str, Any]],
    ) -> int:
        rows = []
        opened_at = self._timestamp(datetime.now(timezone.utc))
        open_keys: set[tuple[str, str, str]] = set()
        for row in self._rows(self._db.execute(
            "SELECT product_id, direction, payload_json FROM shadow_candidates WHERE status = ?",
            ("open",),
        )):
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                payload = {}
            open_keys.add((
                str(row["product_id"]),
                str(row["direction"]),
                str(payload.get("setup_type") or "unknown"),
            ))
        for candidate in candidates:
            status = str(candidate.get("candidate_status") or "")
            if not status.startswith(("research_only_", "actionable_", "shadow_eligible_")):
                continue
            if not candidate.get("valid_for_shadow"):
                continue
            key = (
                str(candidate.get("product_id")),
                str(candidate.get("direction")),
                str(candidate.get("setup_type") or "unknown"),
            )
            if key in open_keys:
                continue
            open_keys.add(key)
            rows.append((
                str(candidate.get("candidate_id")),
                snapshot_id,
                self._timestamp(decision_time),
                key[0],
                key[1],
                "open",
                opened_at,
                None,
                None,
                json.dumps(candidate, ensure_ascii=False, default=str),
            ))
        if rows:
            self._db.executemany(
                "INSERT OR REPLACE INTO shadow_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._db.commit()
        return len(rows)

    def open_candidates(self, *, limit: int = 500) -> list[dict[str, Any]]:
        cursor = self._db.execute(
            "SELECT * FROM shadow_candidates WHERE status = ? ORDER BY decision_time LIMIT ?",
            ("open", limit),
        )
        rows = self._rows(cursor)
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return rows

    def close_candidate(
        self,
        candidate_id: str,
        *,
        status: str,
        outcome_id: str | None = None,
        closed_at: datetime | None = None,
    ) -> None:
        self._db.execute(
            "UPDATE shadow_candidates SET status = ?, closed_at = ?, outcome_id = ? WHERE candidate_id = ?",
            (
                status,
                self._timestamp(closed_at or datetime.now(timezone.utc)),
                outcome_id,
                candidate_id,
            ),
        )
        self._db.commit()

    def _outcome_payloads(self) -> list[dict[str, Any]]:
        rows = self._rows(self._db.execute("SELECT payload_json FROM outcomes ORDER BY decision_time"))
        output = []
        for row in rows:
            try:
                value = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                output.append(value)
        return output

    def calibration_summary(self, *, min_samples: int = 30) -> dict[str, Any]:
        groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
        for payload in self._outcome_payloads():
            if payload.get("status") not in {None, "filled", "not_triggered", "not_filled"}:
                continue
            key = (
                str(payload.get("product_id") or "unknown"),
                str(payload.get("direction") or "unknown"),
                str(payload.get("setup") or payload.get("setup_type") or "unknown"),
                str(payload.get("horizon") or "intraday"),
                str(payload.get("regime") or "unknown"),
            )
            groups.setdefault(key, []).append(payload)
        summaries = []
        for key, rows in groups.items():
            net_values = [_number(row.get("net_return_bps")) for row in rows]
            net_values = [value for value in net_values if value is not None]
            gross_values = [_number(row.get("gross_return_bps")) for row in rows]
            gross_values = [value for value in gross_values if value is not None]
            successes = [1.0 if value > 0 else 0.0 for value in net_values]
            sample_count = len(net_values)
            item = {
                "product_id": key[0],
                "direction": key[1],
                "setup": key[2],
                "horizon": key[3],
                "regime": key[4],
                "sample_count": sample_count,
                "status": "calibrated" if sample_count >= min_samples else "insufficient_sample",
            }
            if sample_count >= min_samples:
                success_rate = sum(successes) / sample_count
                standard_error = (success_rate * (1 - success_rate) / sample_count) ** 0.5
                item.update({
                    "gross_edge_bps": sum(gross_values) / len(gross_values) if gross_values else None,
                    "net_edge_bps": sum(net_values) / sample_count,
                    "success_rate": success_rate,
                    "confidence_interval_95": [
                        max(0.0, success_rate - 1.96 * standard_error),
                        min(1.0, success_rate + 1.96 * standard_error),
                    ],
                    "brier_score": _brier(rows),
                    "walk_forward": "expanding",
                })
            summaries.append(item)
        return {
            "minimum_samples": min_samples,
            "groups": summaries,
            "status": "calibrated" if any(item["status"] == "calibrated" for item in summaries) else "insufficient_sample",
        }

    def calibrated_edges(self, *, min_samples: int = 30) -> dict[str, dict[str, dict[str, Any]]]:
        summary = self.calibration_summary(min_samples=min_samples)
        output: dict[str, dict[str, dict[str, Any]]] = {}
        for item in summary["groups"]:
            if item["status"] != "calibrated":
                continue
            output.setdefault(item["product_id"], {})[item["direction"]] = item
        return output

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
        db = self._db
        self._db = None
        self._sqlite = None
        if db is not None:
            db.close()


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


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _brier(rows: list[dict[str, Any]]) -> float | None:
    values = []
    for row in rows:
        probability = _number(row.get("predicted_probability") or row.get("confidence"))
        outcome = _number(row.get("net_return_bps"))
        if probability is None or outcome is None:
            continue
        probability = max(0.0, min(1.0, probability))
        values.append((probability - (1.0 if outcome > 0 else 0.0)) ** 2)
    return sum(values) / len(values) if values else None
