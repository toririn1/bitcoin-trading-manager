from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def envelope(data: Any, *, schema_version: str = "2.0", generated_at: str | None = None) -> dict[str, Any]:
    return {"schema_version": schema_version, "generated_at": generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "data": data}
