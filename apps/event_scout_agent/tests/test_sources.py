"""Source fetcher tests — parse real captured fixtures, no network."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sources
from sources import (
    enrich_luma_descriptions,
    fetch_all,
    fetch_eventbrite,
    fetch_eventbrite_price,
    fetch_ics,
    is_luma_url,
)
from models import Event

FIXTURES = Path(__file__).parent / "fixtures"


def _mock_response(content: bytes):
    resp = MagicMock()
    resp.content = content
    resp.text = content.decode()
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def ics_response():
    return _mock_response((FIXTURES / "meetup_feed.ics").read_bytes())


@pytest.fixture
def eventbrite_response():
    return _mock_response((FIXTURES / "eventbrite_search.html").read_bytes())


class TestFetchIcs:
    def test_parses_events(self, ics_response):
        with patch.object(sources.requests, "get", return_value=ics_response):
            events = fetch_ics("PyData London", "https://example.com/ical/")

        assert len(events) == 2
        meetup = events[0]
        assert meetup.uid == "event_315269445@meetup.com"
        assert meetup.title == "PyData London Meetup #93"
        assert meetup.location == "Man Group, Riverbank House, London"
        assert meetup.source_name == "PyData London"
        assert meetup.source_type == "ics"

    def test_converts_to_utc(self, ics_response):
        with patch.object(sources.requests, "get", return_value=ics_response):
            events = fetch_ics("x", "https://example.com/ical/")

        # 18:30 Europe/London (BST, +01:00) == 17:30 UTC
        assert events[0].start == datetime(2026, 7, 24, 17, 30, tzinfo=timezone.utc)
        assert events[0].end == datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)

    def test_url_extracted_from_description_when_no_url_property(self, ics_response):
        with patch.object(sources.requests, "get", return_value=ics_response):
            events = fetch_ics("x", "https://example.com/ical/")

        assert events[0].url == (
            "https://www.meetup.com/pydata-london-meetup/events/315269445/"
        )

    def test_all_day_event_uses_url_property_and_midnight_utc(self, ics_response):
        with patch.object(sources.requests, "get", return_value=ics_response):
            events = fetch_ics("x", "https://example.com/ical/")

        all_day = events[1]
        assert all_day.start == datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        assert all_day.url == "https://www.meetup.com/example/events/999/"


class TestFetchEventbrite:
    def test_parses_jsonld_itemlist(self, eventbrite_response):
        with patch.object(sources.requests, "get", return_value=eventbrite_response):
            events = fetch_eventbrite("EB data eng", "https://example.com/d/x/")

        assert len(events) == 3
        first = events[0]
        assert first.title.startswith("Software Engineering Leadership")
        assert first.url.startswith("https://www.eventbrite.co")
        assert first.uid == first.url
        assert first.start == datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)
        assert first.source_type == "eventbrite"

    def test_ignores_non_itemlist_jsonld(self, eventbrite_response):
        # fixture contains a second ld+json block (BreadcrumbList) — must not crash
        with patch.object(sources.requests, "get", return_value=eventbrite_response):
            events = fetch_eventbrite("x", "https://example.com/d/x/")
        assert all(e.url for e in events)


class TestFetchEventbritePrice:
    def test_returns_low_price_for_paid_event(self):
        page = _mock_response((FIXTURES / "eventbrite_event_paid.html").read_bytes())
        with patch.object(sources.requests, "get", return_value=page):
            price = fetch_eventbrite_price("https://example.com/e/paid")
        assert price == 216.16

    def test_returns_zero_for_free_event(self):
        page = _mock_response((FIXTURES / "eventbrite_event_free.html").read_bytes())
        with patch.object(sources.requests, "get", return_value=page):
            price = fetch_eventbrite_price("https://example.com/e/free")
        assert price == 0.0

    def test_returns_none_when_offers_missing(self, eventbrite_response):
        # search-page fixture's items have no `offers` key on the page itself
        with patch.object(sources.requests, "get", return_value=eventbrite_response):
            price = fetch_eventbrite_price("https://example.com/e/unknown")
        assert price is None

    def test_fetch_failure_returns_none(self):
        with patch.object(sources.requests, "get", side_effect=ConnectionError("boom")):
            price = fetch_eventbrite_price("https://example.com/e/down")
        assert price is None


class TestIsLumaUrl:
    def test_matches_luma_and_lu_ma_hosts(self):
        assert is_luma_url("https://luma.com/claude-gz2s")
        assert is_luma_url("https://lu.ma/claude-gz2s")

    def test_rejects_non_luma_hosts(self):
        assert not is_luma_url("https://www.meetup.com/x/events/1/")
        assert not is_luma_url("https://www.eventbrite.co.uk/e/x-tickets-1")
        assert not is_luma_url("")


def _luma_event(uid: str = "a", description: str = "") -> Event:
    return Event(
        uid=uid,
        title="Claude Community Meetup",
        description=description
        or "Get up-to-date information at: https://luma.com/claude-wx2j\n\nHosted by X",
        start=datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc),
        url="https://luma.com/claude-wx2j",
        source_name="Claude Community Events",
        source_type="ics",
    )


class TestEnrichLuma:
    def test_appends_full_description_from_event_page(self):
        page = _mock_response((FIXTURES / "luma_event.html").read_bytes())
        event = _luma_event()
        with patch.object(sources.requests, "get", return_value=page):
            enriched = enrich_luma_descriptions([event], delay=0)

        assert enriched == 1
        assert event.description.startswith("Get up-to-date information")
        assert "founders, builders, AI-native operators" in event.description
        assert len(event.description) <= 2000

    def test_resolves_venue_city_for_offline_event(self):
        page = _mock_response((FIXTURES / "luma_event_offline.html").read_bytes())
        event = _luma_event()
        with patch.object(sources.requests, "get", return_value=page):
            enrich_luma_descriptions([event], delay=0)

        assert event.location == "Porto Alegre, RS, Brazil"

    def test_online_event_location_stays_unresolved(self):
        # geo_address_info can still carry the organizer's home city for an
        # online event — must not be mistaken for the event's venue
        page = _mock_response((FIXTURES / "luma_event_online.html").read_bytes())
        event = _luma_event()
        with patch.object(sources.requests, "get", return_value=page):
            enrich_luma_descriptions([event], delay=0)

        assert event.location == ""

    def test_stale_location_is_cleared_on_fetch_failure(self):
        # Luma's ICS LOCATION is the event URL, not a venue — a prior filter
        # must see "unknown" (empty), never that leftover URL, on failure
        event = _luma_event()
        event.location = "https://luma.com/claude-wx2j"
        with patch.object(sources.requests, "get", side_effect=ConnectionError("boom")):
            enrich_luma_descriptions([event], delay=0)

        assert event.location == ""

    def test_non_luma_event_untouched(self):
        event = _luma_event(description="A normal Meetup description")
        event.url = "https://www.meetup.com/x/events/1/"
        with patch.object(sources.requests, "get") as get:
            enriched = enrich_luma_descriptions([event], delay=0)

        assert enriched == 0
        get.assert_not_called()
        assert event.description == "A normal Meetup description"

    def test_fetch_failure_is_soft(self):
        event = _luma_event()
        original = event.description
        with patch.object(sources.requests, "get", side_effect=ConnectionError("boom")):
            enriched = enrich_luma_descriptions([event], delay=0)

        assert enriched == 0
        assert event.description == original


class TestFetchAll:
    def test_one_broken_source_does_not_kill_the_cycle(self, ics_response):
        def fake_get(url, **kwargs):
            if "broken" in url:
                raise ConnectionError("boom")
            return ics_response

        with patch.object(sources.requests, "get", side_effect=fake_get):
            events = fetch_all(
                {
                    "ics": [
                        {"name": "broken", "url": "https://broken.example/ical/"},
                        {"name": "good", "url": "https://good.example/ical/"},
                    ]
                }
            )
        assert len(events) == 2  # only the good feed's events

    def test_deduplicates_across_sources(self, ics_response):
        with patch.object(sources.requests, "get", return_value=ics_response):
            events = fetch_all(
                {
                    "ics": [
                        {"name": "a", "url": "https://a.example/ical/"},
                        {"name": "b", "url": "https://b.example/ical/"},
                    ]
                }
            )
        # both feeds return the same UIDs — kept once
        assert len(events) == 2

    def test_empty_sources(self):
        assert fetch_all({}) == []
