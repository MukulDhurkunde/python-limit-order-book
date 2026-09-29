"""Append-only Write-Ahead Log for crash fault tolerance.

Rather than logging trade *outputs*, the WAL logs order/cancel *inputs*
(the same records the matching engine consumes). Matching is a pure,
deterministic function of (order book state, input sequence), so replaying
the exact input sequence through the same matching code on startup
reconstructs the exact same book state and produces the exact same trades
-- zero data drift, and no separate trade-log format to keep in sync.

Every record is fsync'd before the caller is allowed to mutate in-memory
state, so a crash between "order accepted" and "order applied" can never
lose an acknowledged order.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator


class WriteAheadLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a", buffering=1, encoding="utf-8")

    def append(self, record: Dict[str, Any]) -> None:
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def replay(self) -> Iterator[Dict[str, Any]]:
        """Yield previously-persisted records in the order they were written."""
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def close(self) -> None:
        self._file.close()
