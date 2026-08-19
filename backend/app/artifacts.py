"""
Artifacts produced by server-side work: batch runs and reports.

These are outputs of a job, not domain records, so they live in memory with a
cap rather than in Postgres. A real deployment would persist them (they are an
audit trail of who changed what, in bulk, and when).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

MAX_KEPT = 50


class ArtifactStore:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._lock = threading.Lock()
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._counter = 0

    def put(self, payload: dict) -> dict:
        with self._lock:
            self._counter += 1
            artifact_id = f"{self._prefix}-{self._counter:04d}"
            record = {
                "id": artifact_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **payload,
            }
            self._items[artifact_id] = record
            while len(self._items) > MAX_KEPT:
                self._items.popitem(last=False)
            return record

    def get(self, artifact_id: str) -> Optional[dict]:
        with self._lock:
            return self._items.get(artifact_id)

    def list(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return list(reversed(list(self._items.values())))[:limit]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._counter = 0


batches = ArtifactStore("BATCH")
reports = ArtifactStore("RPT")
