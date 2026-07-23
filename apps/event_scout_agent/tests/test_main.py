"""Tests for the cycle-step functions in main.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from zoneinfo import ZoneInfo

import sources
from main import (
    build_digest,
    drop_non_local_luma,
    drop_paid_eventbrite,
    filter_events,
    resolve_recipients,
    run_cycle,
    seconds_until_hour,
)
from models import Event
from ranker import EventRanking
from store import EventStore

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
    def test_keeps_only_future_events_in_window(self):
        now = datetime.now(timezone.utc)
        events = [
            _event("past", now - timedelta(days=1)),
            _event("fresh", now + timedelta(days=2)),
            _event("too-far", now + timedelta(days=90)),
        ]
        kept = filter_events(events, now, lookahead_days=45)
        assert [e.uid for e in kept] == ["fresh"]


class TestDropPaidEventbrite:
    def _eb_event(self, uid: str) -> Event:
        event = _event(uid, datetime.now(timezone.utc) + timedelta(days=2))
        event.source_type = "eventbrite"
        return event

    def test_paid_event_is_tombstoned_and_dropped(self, tmp_path, monkeypatch):
        store = EventStore(str(tmp_path))
        event = self._eb_event("paid")
        monkeypatch.setattr(sources, "fetch_eventbrite_price", lambda url: 216.16)

        kept = drop_paid_eventbrite([event], store)

        assert kept == []
        assert store.is_ranked("paid")
        assert store.ranking("paid")["score"] == 0

    def test_unknown_price_event_passes_through_unranked(self, tmp_path, monkeypatch):
        store = EventStore(str(tmp_path))
        event = self._eb_event("unknown")
        monkeypatch.setattr(sources, "fetch_eventbrite_price", lambda url: None)

        kept = drop_paid_eventbrite([event], store)

        assert kept == [event]
        assert not store.is_ranked("unknown")

    def test_confirmed_free_event_passes_through_unranked(self, tmp_path, monkeypatch):
        store = EventStore(str(tmp_path))
        event = self._eb_event("free")
        monkeypatch.setattr(sources, "fetch_eventbrite_price", lambda url: 0.0)

        kept = drop_paid_eventbrite([event], store)

        assert kept == [event]
        assert not store.is_ranked("free")

    def test_non_eventbrite_event_skips_price_check(self, tmp_path, monkeypatch):
        store = EventStore(str(tmp_path))
        event = _event("meetup", datetime.now(timezone.utc) + timedelta(days=2))
        price_check = MagicMock()
        monkeypatch.setattr(sources, "fetch_eventbrite_price", price_check)

        kept = drop_paid_eventbrite([event], store)

        assert kept == [event]
        price_check.assert_not_called()


class TestDropNonLocalLuma:
    def _luma_event(self, uid: str, location: str = "") -> Event:
        event = _event(uid, datetime.now(timezone.utc) + timedelta(days=2))
        event.url = f"https://luma.com/{uid}"
        event.location = location
        return event

    def test_drops_event_with_resolved_venue_in_another_city(self, tmp_path):
        store = EventStore(str(tmp_path))
        event = self._luma_event("porto", location="Porto Alegre, Brazil")

        kept = drop_non_local_luma([event], "London", store)

        assert kept == []
        assert store.is_ranked("porto")
        assert store.ranking("porto")["score"] == 0

    def test_keeps_event_with_resolved_venue_in_target_city(self, tmp_path):
        store = EventStore(str(tmp_path))
        event = self._luma_event("ldn", location="London, United Kingdom")

        kept = drop_non_local_luma([event], "London", store)

        assert kept == [event]
        assert not store.is_ranked("ldn")

    def test_keeps_event_with_unresolved_location(self, tmp_path):
        # empty location = "couldn't tell" (online event, geocode miss, or a
        # failed page fetch) — must never be treated as "wrong city"
        store = EventStore(str(tmp_path))
        event = self._luma_event("unknown", location="")

        kept = drop_non_local_luma([event], "London", store)

        assert kept == [event]
        assert not store.is_ranked("unknown")

    def test_non_luma_event_is_never_filtered_by_location(self, tmp_path):
        # scoped deliberately to Luma only — Meetup/Eventbrite location text
        # isn't guaranteed to literally contain the city name
        store = EventStore(str(tmp_path))
        event = _event("meetup", datetime.now(timezone.utc) + timedelta(days=2))
        event.location = "180 Studios, SE1 9PG"  # a real London venue, no "London" in the text

        kept = drop_non_local_luma([event], "London", store)

        assert kept == [event]
        assert not store.is_ranked("meetup")


class TestResolveRecipients:
    def test_skips_recipients_whose_creds_are_unset(self, monkeypatch):
        monkeypatch.setenv("TG_A", "tokA")
        monkeypatch.setenv("CH_A", "chatA")
        monkeypatch.delenv("TG_B", raising=False)
        monkeypatch.delenv("CH_B", raising=False)
        cfg = {
            "recipients": [
                {"name": "a", "token_env": "TG_A", "chat_env": "CH_A", "backfill": True},
                {"name": "b", "token_env": "TG_B", "chat_env": "CH_B", "backfill": False},
            ]
        }

        out = resolve_recipients(cfg)

        assert [r["name"] for r in out] == ["a"]
        assert out[0]["bot_token"] == "tokA"
        assert out[0]["chat_id"] == "chatA"
        assert out[0]["backfill"] is True


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


def _cfg(recipients=None) -> dict:
    return {
        "location": "London",
        "lookahead_days": 45,
        "min_score": 6,
        "include_online": False,
        "notes": "",
        "topics": ["AI"],
        "sources": {},
        "recipients": recipients or [],
    }


class TestRunCycleSendSemantics:
    """No config recipients -> the console fallback recipient (backfill on)."""

    def _setup(self, tmp_path, monkeypatch, send_ok):
        now = datetime.now(timezone.utc)
        events = [
            _event("selected", now + timedelta(days=2)),
            _event("rejected", now + timedelta(days=3)),
        ]
        monkeypatch.setattr(sources, "fetch_all", lambda s: events)
        monkeypatch.setattr(sources, "enrich_luma_descriptions", lambda evs, delay=0.3: 0)
        ranker = MagicMock()
        ranker.rank.return_value = {
            "selected": EventRanking(event_id=0, score=9, matched_topics=["AI"], reason="x"),
            "rejected": EventRanking(event_id=1, score=2, matched_topics=[], reason="x"),
        }
        notifier = MagicMock()
        notifier.send.return_value = send_ok
        store = EventStore(str(tmp_path))
        return _cfg(), ranker, notifier, store

    def test_failed_send_leaves_selected_pending_for_retry(self, tmp_path, monkeypatch):
        cfg, ranker, notifier, store = self._setup(tmp_path, monkeypatch, send_ok=False)

        notified = run_cycle(cfg, ranker, notifier, store)

        assert notified == 0
        assert not store.is_delivered("console", "selected")  # retries next cycle
        assert store.is_ranked("selected")  # but is not re-ranked
        assert store.is_ranked("rejected")

    def test_successful_send_marks_selected_delivered(self, tmp_path, monkeypatch):
        cfg, ranker, notifier, store = self._setup(tmp_path, monkeypatch, send_ok=True)

        notified = run_cycle(cfg, ranker, notifier, store)

        assert notified == 1
        assert store.is_delivered("console", "selected")
        assert not store.is_delivered("console", "rejected")  # below min_score
        assert store.is_ranked("rejected")


class TestRunCyclePerRecipientBackfill:
    def _wire(self, monkeypatch, feed):
        monkeypatch.setattr(sources, "fetch_all", lambda s: feed)
        monkeypatch.setattr(sources, "enrich_luma_descriptions", lambda evs, delay=0.3: 0)
        monkeypatch.setenv("TG", "tok")
        monkeypatch.setenv("CH", "chat")
        ranker = MagicMock()
        ranker.rank.side_effect = lambda events, *a, **k: {
            e.uid: EventRanking(event_id=0, score=9, matched_topics=["AI"], reason="x")
            for e in events
        }
        notifier = MagicMock()
        notifier.send.return_value = True
        return ranker, notifier

    def test_backfill_off_recipient_skips_existing_backlog(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        ranker, notifier = self._wire(monkeypatch, [_event("existing", now + timedelta(days=2))])
        store = EventStore(str(tmp_path))
        cfg = _cfg([{"name": "sultan", "token_env": "TG", "chat_env": "CH", "backfill": False}])

        notified = run_cycle(cfg, ranker, notifier, store)

        assert notified == 0
        assert store.is_delivered("sultan", "existing")  # primed, never sent
        notifier.send.assert_not_called()

    def test_backfill_on_recipient_gets_current_catalogue(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        ranker, notifier = self._wire(monkeypatch, [_event("existing", now + timedelta(days=2))])
        store = EventStore(str(tmp_path))
        cfg = _cfg([{"name": "georgios", "token_env": "TG", "chat_env": "CH", "backfill": True}])

        notified = run_cycle(cfg, ranker, notifier, store)

        assert notified == 1
        notifier.send.assert_called_once()
        assert store.is_delivered("georgios", "existing")

    def test_backfill_off_recipient_gets_events_added_after_they_joined(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        feed = [_event("old", now + timedelta(days=2))]
        ranker, notifier = self._wire(monkeypatch, feed)
        store = EventStore(str(tmp_path))
        cfg = _cfg([{"name": "sultan", "token_env": "TG", "chat_env": "CH", "backfill": False}])

        # cycle 1: only 'old' is known -> sultan is primed, receives nothing
        assert run_cycle(cfg, ranker, notifier, store) == 0
        # cycle 2: 'new' appears after he joined -> he gets exactly it
        feed.append(_event("new", now + timedelta(days=3)))
        assert run_cycle(cfg, ranker, notifier, store) == 1
        assert store.is_delivered("sultan", "new")
        # 'old' was primed as already-delivered at join, so it was never sent
        assert store.is_delivered("sultan", "old")
        notifier.send.assert_called_once()


class TestBuildDigest:
    def test_formats_events_with_scores(self):
        event = _event("a", datetime(2026, 7, 24, 17, 30, tzinfo=timezone.utc))
        ranking = {"score": 9, "matched_topics": ["AI", "startups"], "reason": "great fit"}

        digest = build_digest([(event, ranking)])

        assert "1 new event for you" in digest
        assert "*Event a*" in digest
        # 17:30 UTC == 18:30 Europe/London in July (BST)
        assert "Fri 24 Jul, 18:30" in digest
        assert "⭐ 9/10 — AI, startups" in digest
        assert "https://example.com/a" in digest

    def test_midnight_start_shows_date_only(self):
        event = _event("a", datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc))
        ranking = {"score": 7, "matched_topics": [], "reason": ""}

        digest = build_digest([(event, ranking)])

        assert "Tue 21 Jul" in digest
        assert "00:00" not in digest

    def test_plural_header(self):
        events = [
            (_event(uid, datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc)),
             {"score": 8, "matched_topics": [], "reason": ""})
            for uid in ("a", "b")
        ]
        assert "2 new events for you" in build_digest(events)
