from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass


@dataclass(slots=True)
class RequestBudget:
    provider: str
    minimum_interval_seconds: float = 0.0
    max_requests: int | None = None
    _last_request: float = 0.0
    _requests: int = 0

    async def wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        delay = self.minimum_interval_seconds - elapsed
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_request = time.monotonic()
        self._requests += 1

    def exhausted(self) -> bool:
        return self.max_requests is not None and self._requests >= self.max_requests


async def retry_with_backoff(operation, *, retries: int = 2, base_delay: float = 0.4):
    last_error = None
    for attempt in range(retries + 1):
        try:
            return await operation()
        except Exception as exc:  # provider-specific errors are classified by caller
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(base_delay * (2**attempt) + random.random() * 0.2)
    raise last_error  # type: ignore[misc]
