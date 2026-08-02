from __future__ import annotations

import argparse
import asyncio
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import V2Settings
from .engine import V2Engine


class ShadowRunner:
    """Periodic read-only decision runner.

    Each cycle creates a fresh snapshot and persists the decision through V2Engine.
    It never submits orders; stopping the process cancels only the local loop.
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
        self.history: list[dict[str, Any]] = []

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> dict[str, Any]:
        decision = await self.engine.decision(mode=self.mode)
        record = {
            "collected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "mode": decision.get("mode"),
            "snapshot_id": decision.get("snapshot_id"),
            "final_action": decision.get("final_action"),
            "data_unavailable": decision.get("data_unavailable", False),
        }
        self.history.append(record)
        return decision

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

    runner = ShadowRunner(
        interval_seconds=args.interval,
        mode=args.mode,
    )
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, runner.stop)
        except (NotImplementedError, RuntimeError):
            pass
    await runner.run(iterations=1 if args.once else None)


if __name__ == "__main__":
    asyncio.run(_main())
