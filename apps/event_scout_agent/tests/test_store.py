"""EventStore tests — global ranking cache, per-recipient delivery, migration."""

import json
from datetime import datetime, timedelta, timezone

from models import Event
from ranker import EventRanking
from store import EventStore


def _event(uid: str, start: datetime) -> Event:
    return Event(uid=uid, title=f"Event {uid}", start=start, source_name="test", source_type="ics")


def _ranking(score: int) -> EventRanking:
    return EventRanking(event_id=0, score=score, matched_topics=["AI"], reason="fit")


def test_ranking_cache_roundtrip(tmp_path):
    store = EventStore(str(tmp_path))
    future = datetime.now(timezone.utc) + timedelta(days=7)

    assert not store.is_ranked("a")
    store.add_rankings([_event("a", future)], {"a": _ranking(8)})
    assert store.is_ranked("a")
    assert store.ranking("a")["score"] == 8
    assert store.ranking("a")["matched_topics"] == ["AI"]


def test_add_rankings_ignores_uids_without_a_matching_event(tmp_path):
    store = EventStore(str(tmp_path))
    future = datetime.now(timezone.utc) + timedelta(days=7)
    store.add_rankings([_event("a", future)], {"b": _ranking(9)})
    assert not store.is_ranked("b")


def test_new_recipient_backfill_off_primes_backlog_as_delivered(tmp_path):
    store = EventStore(str(tmp_path))
    future = datetime.now(timezone.utc) + timedelta(days=7)
    store.init_recipient("friend", [_event("a", future), _event("b", future)], backfill=False)
    assert store.knows_recipient("friend")
    assert store.is_delivered("friend", "a")
    assert store.is_delivered("friend", "b")


def test_new_recipient_backfill_on_starts_empty(tmp_path):
    store = EventStore(str(tmp_path))
    future = datetime.now(timezone.utc) + timedelta(days=7)
    store.init_recipient("georgios", [_event("a", future)], backfill=True)
    assert store.knows_recipient("georgios")
    assert not store.is_delivered("georgios", "a")


def test_delivery_is_tracked_per_recipient(tmp_path):
    store = EventStore(str(tmp_path))
    future = datetime.now(timezone.utc) + timedelta(days=7)
    store.mark_delivered("georgios", [_event("a", future)])
    assert store.is_delivered("georgios", "a")
    assert not store.is_delivered("friend", "a")


def test_persistence_roundtrip(tmp_path):
    future = datetime.now(timezone.utc) + timedelta(days=7)
    store = EventStore(str(tmp_path))
    store.add_rankings([_event("a", future)], {"a": _ranking(7)})
    store.mark_delivered("georgios", [_event("a", future)])
    store.save()

    reloaded = EventStore(str(tmp_path))
    assert reloaded.is_ranked("a")
    assert reloaded.is_delivered("georgios", "a")
    assert len(reloaded) == 1


def test_prune_drops_past_from_ranked_and_delivered(tmp_path):
    now = datetime.now(timezone.utc)
    store = EventStore(str(tmp_path))
    past = _event("past", now - timedelta(days=3))
    yesterday = _event("yesterday", now - timedelta(hours=12))  # within 1-day grace
    future = _event("future", now + timedelta(days=3))
    store.add_rankings(
        [past, yesterday, future],
        {"past": _ranking(8), "yesterday": _ranking(8), "future": _ranking(8)},
    )
    store.mark_delivered("georgios", [past, yesterday, future])

    dropped = store.prune(now)

    assert dropped == 1
    assert not store.is_ranked("past")
    assert store.is_ranked("yesterday")
    assert store.is_ranked("future")
    assert not store.is_delivered("georgios", "past")
    assert store.is_delivered("georgios", "yesterday")


def test_corrupt_file_starts_fresh(tmp_path):
    (tmp_path / "seen.json").write_text("{not json")
    store = EventStore(str(tmp_path))
    assert len(store) == 0


def test_legacy_seen_file_migrates_to_tombstones(tmp_path):
    # old single-recipient format: flat uid -> {first_seen, event_start}
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    (tmp_path / "seen.json").write_text(
        json.dumps({"old-uid": {"first_seen": "2026-01-01T00:00:00+00:00", "event_start": future}})
    )
    store = EventStore(str(tmp_path))

    # migrated as ranked (never re-scored) with score 0 (never re-notified),
    # and not yet delivered to anyone — new recipients handle it via priming.
    assert store.is_ranked("old-uid")
    assert store.ranking("old-uid")["score"] == 0
    assert not store.is_delivered("georgios", "old-uid")
