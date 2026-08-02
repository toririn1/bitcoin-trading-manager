from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ProviderHTTPError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, headers: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.headers = headers or {}


@dataclass(slots=True)
class JSONResponse:
    payload: object
    status: int
    headers: dict[str, str]
    latency_ms: float


def _get(
    url: str,
    params: dict[str, object],
    timeout: float,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    full_url = f"{url}?{query}" if query else url
    started = time.perf_counter()
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "bitcoin-trading-manager-v2/1.0",
    }
    request_headers.update(headers or {})
    request = Request(full_url, headers=request_headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            return JSONResponse(
                json.loads(raw.decode("utf-8")),
                response.status,
                response_headers,
                (time.perf_counter() - started) * 1000,
            )
    except HTTPError as exc:
        raise ProviderHTTPError(
            f"http_{exc.code}",
            status=exc.code,
            headers=dict(exc.headers.items()),
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProviderHTTPError(type(exc).__name__) from exc


class AsyncJSONClient:
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    async def get(
        self,
        url: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        return await asyncio.to_thread(_get, url, params or {}, self.timeout, headers)
