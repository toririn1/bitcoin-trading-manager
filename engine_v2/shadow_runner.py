from __future__ import annotations

import argparse
import asyncio
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import V2Settings
from .engine import V2Engine
from .evaluation.replay import ReplayConfig, replay_candidate
from .domain.models import parse_datetime


class ShadowRunner:
    """Periodic read-only decision and outcome runner.

    It records candidates, waits for future candles, replays the deterministic
    entry/stop/target plan, and writes outcomes. It never submits orders.
    """

    def __init__(
        self,
        engine: V2Engine | None = None,
        *,
        interval_seconds: int | None = None,
        mode: str | None = None,
    ) -> None:
        self.engine = engine or V2Engine()
        self.interval_seconds = max(
            1,
            int(interval_seconds or self.engine.settings.shadow_interval_seconds),
        )
        self.mode = mode
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self.history: list[dict[str, Any]] = []
        self._lock_path = self.engine.storage.root / "shadow_runner.lock"

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> dict[str, Any]:
        async with self._lock:
            self._acquire_process_lock()
            try:
                settled = self._settle_open_candidates()
                snapshot = await self.engine.build_snapshot(mode=self.mode)
                decision = self.engine.last_decision or self.engine._decision_from_snapshot(snapshot)
                record = {
                    "collected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "mode": decision.get("mode"),
                    "snapshot_id": decision.get("snapshot_id"),
                    "final_action": decision.get("final_action"),
                    "execution_permission": decision.get("execution_permission"),
                    "settled_outcomes": settled,
                    "data_unavailable": decision.get("data_unavailable", False),
                }
                self.history.append(record)
                return decision
            finally:
                self._release_process_lock()

    def _settle_open_candidates(self) -> int:
        settled = 0
        now = datetime.now(timezone.utc)
        for row in self.engine.storage.open_candidates():
            candidate = row.get("payload") or {}
            decision_time = parse_datetime(row.get("decision_time"))
            if decision_time is None:
                continue
            observations = self.engine.storage.observations(
                data_type="candle_15m",
                product_id=row.get("product_id"),
                limit=1000,
            )
            future = [
                item for item in observations
                if (parse_datetime(item.get("source_event_time") or (item.get("payload") or {}).get("open_time")) or datetime.min.replace(tzinfo=timezone.utc))
                > decision_time
            ]
            expiry = parse_datetime(candidate.get("time_expiry"))
            if not future and (expiry is None or expiry > now):
                continue
            if future:
                result = replay_candidate(
                    candidate,
                    future,
                    config=ReplayConfig(
                        fee_bps=float(candidate.get("estimated_cost_bps") or 0.0) / 2,
                    ),
                )
            else:
                result = {
                    "status": "not_filled",
                    "reason": "time_expiry",
                    "product_id": candidate.get("product_id"),
                    "direction": candidate.get("direction"),
                    "failure_codes": ["time_expiry"],
                }
            if result.get("status") not in {"filled", "not_triggered", "not_filled"}:
                continue
            outcome = {
                **result,
                "candidate_id": candidate.get("candidate_id"),
                "snapshot_id": row.get("snapshot_id"),
                "decision_time": row.get("decision_time"),
                "setup": candidate.get("setup_type"),
                "horizon": candidate.get("horizon"),
                "regime": (candidate.get("calibration_group") or {}).get("regime"),
                "predicted_probability": candidate.get("confidence"),
                "status": result.get("status"),
            }
            outcome_id = f"outcome-{candidate.get('candidate_id')}"
            self.engine.storage.save_outcome(outcome_id, decision_time, outcome)
            self.engine.storage.close_candidate(
                str(candidate.get("candidate_id")),
                status="closed",
                outcome_id=outcome_id,
                closed_at=now,
            )
            settled += 1
        return settled

    def _acquire_process_lock(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._lock_path.exists():
            try:
                pid = int(self._lock_path.read_text().strip())
                os.kill(pid, 0)
            except (OSError, ValueError):
                self._lock_path.unlink(missing_ok=True)
            else:
                raise RuntimeError(f"shadow_runner_already_running:{pid}")
        self._lock_path.write_text(str(os.getpid()))

    def _release_process_lock(self) -> None:
        try:
            pid = int(self._lock_path.read_text().strip())
        except (OSError, ValueError):
            return
        if pid == os.getpid():
            self._lock_path.unlink(missing_ok=True)

    async def run(self, *, iterations: int | None = None) -> list[dict[str, Any]]:
        count = 0
        while not self._stop.is_set() and (iterations is None or count < iterations):
            await self.run_once()
            count += 1
            if iterations is not None and count >= iterations:
                break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue
        return list(self.history)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run the V2 read-only shadow decision loop.")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--mode", choices=("live", "fixture", "replay"), default=None)
    parser.add_argument("--interval", type=int, default=None)
    args = parser.parse_args()

    runner = ShadowRunner(interval_seconds=args.interval, mode=args.mode)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, runner.stop)
        except (NotImplementedError, RuntimeError):
            pass
    await runner.run(iterations=1 if args.once else None)


if __name__ == "__main__":
    asyncio.run(_main())
