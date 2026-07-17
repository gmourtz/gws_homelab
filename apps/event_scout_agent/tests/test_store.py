"""SeenStore dedup + pruning tests."""

from datetime import datetime, timedelta, timezone

from models import Event
from store import SeenStore


def _event(uid: str, start: datetime) -> Event:
    return Event(
        uid=uid,
        title=f"Event {uid}",
        start=start,
        source_name="test",
        source_type="ics",
    )


def test_mark_and_check(tmp_path):
    store = SeenStore(str(tmp_path))
    future = datetime.now(timezone.utc) + timedelta(days=7)

    assert not store.is_seen("a")
    store.mark_seen([_event("a", future)])
    assert store.is_seen("a")


def test_persistence_roundtrip(tmp_path):
    future = datetime.now(timezone.utc) + timedelta(days=7)
    store = SeenStore(str(tmp_path))
    store.mark_seen([_event("a", future), _event("b", future)])
    store.save()

    reloaded = SeenStore(str(tmp_path))
    assert reloaded.is_seen("a")
    assert reloaded.is_seen("b")
    assert len(reloaded) == 2


def test_prune_drops_past_events_only(tmp_path):
    now = datetime.now(timezone.utc)
    store = SeenStore(str(tmp_path))
    store.mark_seen(
        [
            _event("past", now - timedelta(days=3)),
            _event("yesterday", now - timedelta(hours=12)),  # within 1-day grace
            _event("future", now + timedelta(days=3)),
        ]
    )

    dropped = store.prune(now)

    assert dropped == 1
    assert not store.is_seen("past")
    assert store.is_seen("yesterday")
    assert store.is_seen("future")


def test_corrupt_file_starts_fresh(tmp_path):
    (tmp_path / "seen.json").write_text("{not json")
    store = SeenStore(str(tmp_path))
    assert len(store) == 0
