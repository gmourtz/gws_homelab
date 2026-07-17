"""Tests for the pure cycle-step functions in main.py."""

from datetime import datetime, timedelta, timezone

from main import build_digest, filter_events
from models import Event
from ranker import EventRanking
from store import SeenStore


def _event(uid: str, start: datetime) -> Event:
    return Event(
        uid=uid,
        title=f"Event {uid}",
        start=start,
        location="London",
        url=f"https://example.com/{uid}",
        source_name="test",
        source_type="ics",
    )


class TestFilterEvents:
    def test_keeps_only_window_and_unseen(self, tmp_path):
        now = datetime.now(timezone.utc)
        store = SeenStore(str(tmp_path))
        seen_event = _event("seen", now + timedelta(days=2))
        store.mark_seen([seen_event])

        events = [
            _event("past", now - timedelta(days=1)),
            _event("fresh", now + timedelta(days=2)),
            seen_event,
            _event("too-far", now + timedelta(days=90)),
        ]

        kept = filter_events(events, now, lookahead_days=45, seen_store=store)

        assert [e.uid for e in kept] == ["fresh"]


class TestBuildDigest:
    def test_formats_events_with_scores(self):
        event = _event("a", datetime(2026, 7, 24, 17, 30, tzinfo=timezone.utc))
        ranking = EventRanking(
            event_id=0, score=9, matched_topics=["AI", "startups"], reason="great fit"
        )

        digest = build_digest([(event, ranking)])

        assert "1 new event for you" in digest
        assert "*Event a*" in digest
        # 17:30 UTC == 18:30 Europe/London in July (BST)
        assert "Fri 24 Jul, 18:30" in digest
        assert "⭐ 9/10 — AI, startups" in digest
        assert "https://example.com/a" in digest

    def test_midnight_start_shows_date_only(self):
        event = _event("a", datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc))
        ranking = EventRanking(event_id=0, score=7, matched_topics=[], reason="")

        digest = build_digest([(event, ranking)])

        assert "Tue 21 Jul" in digest
        assert "00:00" not in digest

    def test_plural_header(self):
        events = [
            (
                _event(uid, datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc)),
                EventRanking(event_id=0, score=8, matched_topics=[], reason=""),
            )
            for uid in ("a", "b")
        ]
        assert "2 new events for you" in build_digest(events)
