from __future__ import annotations

from pathlib import Path

from .database import V2Storage


def migrate(root: str | Path = "data/v2") -> dict:
    store = V2Storage(root=root)
    return {"ok": True, "status": store.status()}
