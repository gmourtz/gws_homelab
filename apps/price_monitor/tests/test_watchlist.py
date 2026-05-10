"""Tests for watchlist.py — config loading."""

import json
import os
import pytest

from watchlist import load_watchlist, WatchItem


@pytest.fixture
def watchlist_file(tmp_path):
    """Helper to create a temp watchlist file."""
    path = tmp_path / "watchlist.json"

    def _write(data):
        path.write_text(json.dumps(data))
        return str(path)

    return _write


class TestLoadWatchlist:
    """Test watchlist loading from JSON."""

    def test_load_valid_watchlist(self, watchlist_file):
        path = watchlist_file([
            {"url": "https://amazon.com/dp/A1", "label": "Keyboard"},
            {"url": "https://amazon.com/dp/A2", "label": "Mouse"},
        ])
        items = load_watchlist(path)
        assert len(items) == 2
        assert items[0].url == "https://amazon.com/dp/A1"
        assert items[0].label == "Keyboard"
        assert items[1].label == "Mouse"

    def test_missing_file(self, tmp_path):
        items = load_watchlist(str(tmp_path / "nonexistent.json"))
        assert items == []

    def test_empty_array(self, watchlist_file):
        path = watchlist_file([])
        items = load_watchlist(path)
        assert items == []

    def test_not_an_array(self, watchlist_file):
        path = watchlist_file({"url": "https://amazon.com"})
        items = load_watchlist(path)
        assert items == []

    def test_skip_entry_without_url(self, watchlist_file):
        path = watchlist_file([
            {"label": "No URL"},
            {"url": "https://amazon.com/dp/A1", "label": "Valid"},
        ])
        items = load_watchlist(path)
        assert len(items) == 1
        assert items[0].label == "Valid"

    def test_auto_label_from_url(self, watchlist_file):
        path = watchlist_file([
            {"url": "https://amazon.com/dp/B08N5WRWNW"},
        ])
        items = load_watchlist(path)
        assert len(items) == 1
        assert items[0].label == "B08N5WRWNW"

    def test_whitespace_trimming(self, watchlist_file):
        path = watchlist_file([
            {"url": "  https://amazon.com/dp/A1  ", "label": "  Keyboard  "},
        ])
        items = load_watchlist(path)
        assert items[0].url == "https://amazon.com/dp/A1"
        assert items[0].label == "Keyboard"
