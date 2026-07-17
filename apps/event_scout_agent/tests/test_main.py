"""Tests for the cycle-step functions in main.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from zoneinfo import ZoneInfo

import sources
from main import build_digest, filter_events, run_cycle, seconds_until_hour
from models import Event
from ranker import EventRanking
from store import SeenStore

LONDON = ZoneInfo("Europe/London")


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


class TestSecondsUntilHour:
    def test_hour_later_today(self):
        now = datetime(2026, 1, 15, 3, 0, tzinfo=LONDON)
        assert seconds_until_hour(5, now) == 2 * 3600

    def test_hour_already_passed_rolls_to_tomorrow(self):
        now = datetime(2026, 1, 15, 6, 0, tzinfo=LONDON)
        assert seconds_until_hour(5, now) == 23 * 3600

    def test_exactly_on_the_hour_rolls_to_tomorrow(self):
        now = datetime(2026, 1, 15, 5, 0, tzinfo=LONDON)
        assert seconds_until_hour(5, now) == 24 * 3600

    def test_dst_transition_keeps_wall_clock(self):
        # clocks go forward 29 Mar 2026: 01:00 GMT -> 02:00 BST (23h day)
        now = datetime(2026, 3, 28, 5, 0, tzinfo=LONDON)
        assert seconds_until_hour(5, now) == 23 * 3600


class TestRunCycleSendSemantics:
    def _setup(self, tmp_path, monkeypatch, send_ok):
        now = datetime.now(timezone.utc)
        events = [
            _event("selected", now + timedelta(days=2)),
            _event("rejected", now + timedelta(days=3)),
        ]
        monkeypatch.setattr(sources, "fetch_all", lambda s: events)
        monkeypatch.setattr(
            sources, "enrich_luma_descriptions", lambda evs, delay=0.3: 0
        )
        cfg = {
            "location": "London",
            "lookahead_days": 45,
            "min_score": 6,
            "include_online": False,
            "notes": "",
            "topics": ["AI"],
            "sources": {},
        }
        ranker = MagicMock()
        ranker.rank.return_value = {
            "selected": EventRanking(event_id=0, score=9, matched_topics=["AI"], reason="x"),
            "rejected": EventRanking(event_id=1, score=2, matched_topics=[], reason="x"),
        }
        notifier = MagicMock()
        notifier.send.return_value = send_ok
        store = SeenStore(str(tmp_path))
        return cfg, ranker, notifier, store

    def test_failed_send_leaves_selected_events_unseen_for_retry(self, tmp_path, monkeypatch):
        cfg, ranker, notifier, store = self._setup(tmp_path, monkeypatch, send_ok=False)

        notified = run_cycle(cfg, ranker, notifier, store)

        assert notified == 0
        assert not store.is_seen("selected")  # retries next cycle
        assert store.is_seen("rejected")  # low scores aren't re-ranked daily

    def test_successful_send_marks_all_ranked_seen(self, tmp_path, monkeypatch):
        cfg, ranker, notifier, store = self._setup(tmp_path, monkeypatch, send_ok=True)

        notified = run_cycle(cfg, ranker, notifier, store)

        assert notified == 1
        assert store.is_seen("selected")
        assert store.is_seen("rejected")


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
