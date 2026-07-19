"""Persistent state in data/seen.json, pruned once an event's date passes:

  ranked:    uid -> {score, matched_topics, reason, event_start}
             global "rank each event once" cache — the LLM never re-scores an
             event it has already scored.
  delivered: recipient_name -> {uid -> event_start}
             per-recipient send log — each subscriber is deduped and pruned
             independently, so people who join at different times stay in sync
             without re-notifying each other.

A brand-new recipient is either primed with the current backlog (backfill
off — they only get events discovered from now on) or started empty (backfill
on — they get everything currently pending, i.e. the initial load)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from models import Event

log = logging.getLogger(__name__)


class EventStore:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or os.path.join(os.path.dirname(__file__), "..", "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "seen.json")
        state = self._load()
        self._ranked: dict[str, dict] = state["ranked"]
        self._delivered: dict[str, dict[str, str]] = state["delivered"]

    def _load(self) -> dict:
        empty = {"ranked": {}, "delivered": {}}
        if not os.path.exists(self.path):
            return empty
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read %s (%s) — starting fresh", self.path, e)
            return empty
        if isinstance(data.get("ranked"), dict) and isinstance(data.get("delivered"), dict):
            return {"ranked": data["ranked"], "delivered": data["delivered"]}
        # Legacy flat {uid: {first_seen, event_start}} — tombstone every entry
        # as a score-0 ranking so it is neither re-scored nor ever re-notified
        # (those events were already sent under the single-recipient design).
        ranked = {
            uid: {"score": 0, "matched_topics": [], "reason": "", "event_start": meta["event_start"]}
            for uid, meta in data.items()
            if isinstance(meta, dict) and "event_start" in meta
        }
        log.info("Migrated %d legacy seen entries to ranked-tombstones", len(ranked))
        return {"ranked": ranked, "delivered": {}}

    def is_ranked(self, uid: str) -> bool:
        return uid in self._ranked

    def ranking(self, uid: str) -> dict | None:
        return self._ranked.get(uid)

    def add_rankings(self, events: list[Event], rankings: dict) -> None:
        by_uid = {e.uid: e for e in events}
        for uid, r in rankings.items():
            event = by_uid.get(uid)
            if event is None:
                continue
            self._ranked[uid] = {
                "score": r.score,
                "matched_topics": r.matched_topics,
                "reason": r.reason,
                "event_start": event.start.isoformat(),
            }

    def knows_recipient(self, name: str) -> bool:
        return name in self._delivered

    def init_recipient(self, name: str, backlog: list[Event], backfill: bool) -> None:
        """Register a recipient on first sighting. backfill=False primes the
        current backlog as already-delivered so they skip the initial load."""
        if backfill:
            self._delivered[name] = {}
        else:
            self._delivered[name] = {e.uid: e.start.isoformat() for e in backlog}

    def is_delivered(self, name: str, uid: str) -> bool:
        return uid in self._delivered.get(name, {})

    def mark_delivered(self, name: str, events: list[Event]) -> None:
        log_ = self._delivered.setdefault(name, {})
        for event in events:
            log_[event.uid] = event.start.isoformat()

    def prune(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=1)

        def _stale(meta_date: str) -> bool:
            return datetime.fromisoformat(meta_date) < cutoff

        dropped = 0
        for uid in [u for u, m in self._ranked.items() if _stale(m["event_start"])]:
            del self._ranked[uid]
            dropped += 1
        for log_ in self._delivered.values():
            for uid in [u for u, start in log_.items() if _stale(start)]:
                del log_[uid]
        return dropped

    def save(self) -> None:
        with open(self.path, "w") as f:
            json.dump({"ranked": self._ranked, "delivered": self._delivered}, f, indent=2)

    def __len__(self) -> int:
        return len(self._ranked)
