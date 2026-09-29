"""Runtime configuration, overridable via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    wal_path: str = os.environ.get("LOB_WAL_PATH", "data/wal.jsonl")
    host: str = os.environ.get("LOB_HOST", "0.0.0.0")
    port: int = int(os.environ.get("LOB_PORT", "8000"))


settings = Settings()
