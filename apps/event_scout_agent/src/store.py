"""Notify-once dedup store: uid -> {first_seen, event_start} in seen.json,
pruned once the event date has passed."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from models import Event

log = logging.getLogger(__name__)


class SeenStore:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or os.path.join(os.path.dirname(__file__), "..", "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "seen.json")
        self._seen: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read %s (%s) — starting fresh", self.path, e)
            return {}

    def is_seen(self, uid: str) -> bool:
        return uid in self._seen

    def mark_seen(self, events: list[Event]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for event in events:
            self._seen[event.uid] = {
                "first_seen": now,
                "event_start": event.start.isoformat(),
            }

    def prune(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=1)
        stale = [
            uid
            for uid, meta in self._seen.items()
            if datetime.fromisoformat(meta["event_start"]) < cutoff
        ]
        for uid in stale:
            del self._seen[uid]
        return len(stale)

    def save(self) -> None:
        with open(self.path, "w") as f:
            json.dump(self._seen, f, indent=2)

    def __len__(self) -> int:
        return len(self._seen)
