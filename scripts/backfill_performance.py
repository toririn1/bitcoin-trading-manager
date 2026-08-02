#!/usr/bin/env python3
"""Write a migration-safe performance-enriched analysis history file."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis_performance import evaluate_analysis_record


def _timestamp(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def select_future_candles(record: dict, candles: Iterable[dict] | None) -> list[dict]:
    """Select candles after the analysis timestamp through the 4h horizon."""
    analysis_ts = _timestamp(record.get("timestamp"))
    if analysis_ts is None or not candles:
        return []
    selected = []
    for candle in candles:
        candle_ts = _timestamp(candle.get("timestamp"))
        if candle_ts is None:
            continue
        minutes_after = (candle_ts - analysis_ts) / 60
        if 0 < minutes_after <= 245:
            selected.append({**candle, "minutes_after": minutes_after})
    return sorted(selected, key=lambda row: row["minutes_after"])


def enrich_record(record: dict, candles: Iterable[dict] | None) -> dict:
    enriched = dict(record)
    future = select_future_candles(record, candles)
    enriched.update(evaluate_analysis_record(record, future))
    return enriched


def fetch_recent_candles(symbol: str, limit: int) -> list[dict]:
    """Reuse the project's public Binance fetcher; callers handle failures."""
    from data_fetcher import fetch_ohlcv

    frame = fetch_ohlcv(symbol, "5m", limit=limit)
    rows = []
    for timestamp, row in frame.iterrows():
        rows.append(
            {
                "timestamp": timestamp.to_pydatetime().replace(tzinfo=timezone.utc).isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return rows


def load_candle_file(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        payload = payload.get("candles") or []
    return [row for row in payload if isinstance(row, dict)]


def backfill_file(source: Path, destination: Path, candles: list[dict] | None) -> tuple[int, int]:
    processed = 0
    computed = 0
    with source.open(encoding="utf-8") as reader, destination.open("w", encoding="utf-8") as writer:
        for line in reader:
            stripped = line.strip()
            if not stripped:
                writer.write(line)
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                writer.write(line)
                continue
            enriched = enrich_record(record, candles)
            if enriched.get("return_30m") is not None:
                computed += 1
            writer.write(json.dumps(enriched, ensure_ascii=False) + "\n")
            processed += 1
    return processed, computed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", default="data/analysis_history.jsonl")
    parser.add_argument("--output")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--candles-json", type=Path)
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()

    source = Path(args.input)
    destination = Path(args.output) if args.output else source.with_name(source.stem + ".with_performance.jsonl")
    if not source.exists():
        parser.error(f"history file not found: {source}")

    candles = None
    if args.candles_json:
        candles = load_candle_file(args.candles_json)
    elif not args.no_fetch:
        try:
            candles = fetch_recent_candles(args.symbol, max(1, min(args.limit, 1000)))
        except Exception as exc:
            print(f"candle fetch unavailable; writing null fallback: {type(exc).__name__}: {exc}", file=sys.stderr)

    processed, computed = backfill_file(source, destination, candles)
    print(f"{destination} processed={processed} computed={computed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
