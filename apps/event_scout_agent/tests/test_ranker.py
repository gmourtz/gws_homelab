"""Ranker tests — prompt building and response mapping with a mocked client."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from models import Event
from ranker import BATCH_SIZE, EventRanker, EventRanking, RankingResult


def _event(uid: str, title: str = "AI meetup") -> Event:
    return Event(
        uid=uid,
        title=title,
        description="Talks about LLM agents",
        start=datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc),
        location="London",
        url=f"https://example.com/{uid}",
        source_name="test",
        source_type="ics",
    )


def _ranker_with_parse_result(rankings: list[EventRanking]) -> EventRanker:
    ranker = EventRanker(api_key="test")
    parsed = RankingResult(rankings=rankings)
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(parsed=parsed))]
    ranker.client = MagicMock()
    ranker.client.beta.chat.completions.parse.return_value = response
    return ranker


def test_rankings_mapped_back_to_uids():
    events = [_event("a"), _event("b")]
    ranker = _ranker_with_parse_result(
        [
            EventRanking(event_id=0, score=9, matched_topics=["AI"], reason="direct match"),
            EventRanking(event_id=1, score=2, matched_topics=[], reason="unrelated"),
        ]
    )

    results = ranker.rank(events, ["AI"], "London")

    assert results["a"].score == 9
    assert results["b"].score == 2


def test_out_of_range_event_id_is_dropped():
    ranker = _ranker_with_parse_result(
        [EventRanking(event_id=5, score=9, matched_topics=[], reason="x")]
    )
    assert ranker.rank([_event("a")], ["AI"], "London") == {}


def test_batching_splits_large_input():
    events = [_event(f"e{i}") for i in range(BATCH_SIZE + 3)]
    ranker = _ranker_with_parse_result(
        [EventRanking(event_id=0, score=5, matched_topics=[], reason="x")]
    )

    ranker.rank(events, ["AI"], "London")

    assert ranker.client.beta.chat.completions.parse.call_count == 2


def test_fallback_parses_plain_json():
    ranker = EventRanker(api_key="test")
    ranker.client = MagicMock()
    ranker.client.beta.chat.completions.parse.side_effect = RuntimeError("no structured output")
    fallback = MagicMock()
    fallback.choices = [
        MagicMock(
            message=MagicMock(
                content='Sure! {"rankings": [{"event_id": 0, "score": 7, '
                '"matched_topics": ["AI"], "reason": "good fit"}]}'
            )
        )
    ]
    ranker.client.chat.completions.create.return_value = fallback

    results = ranker.rank([_event("a")], ["AI"], "London")

    assert results["a"].score == 7


def test_failed_batch_returns_empty_so_events_retry_next_cycle():
    ranker = EventRanker(api_key="test")
    ranker.client = MagicMock()
    ranker.client.beta.chat.completions.parse.side_effect = RuntimeError("down")
    ranker.client.chat.completions.create.side_effect = RuntimeError("down")

    assert ranker.rank([_event("a")], ["AI"], "London") == {}


def test_prompt_contains_topics_location_and_events():
    events = [_event("a", title="Databricks World Tour")]
    prompt = EventRanker._build_prompt(events, ["Databricks", "AI"], "London", False)

    assert "Databricks World Tour" in prompt
    assert "- Databricks" in prompt
    assert "User's city: London" in prompt
    assert "Online-only events allowed: no" in prompt
    assert "Event id: 0" in prompt
