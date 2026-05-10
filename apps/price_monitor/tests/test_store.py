"""Tests for store.py — price history persistence."""

import json
import os
import pytest

from store import PriceStore, PriceRecord


@pytest.fixture
def store(tmp_path):
    """Create a PriceStore backed by a temp directory."""
    return PriceStore(str(tmp_path))


class TestPriceStore:
    """Test price history read/write operations."""

    def test_append_creates_file(self, store):
        store.append("http://example.com/1", "Widget", 29.99, "USD")
        assert os.path.exists(store.history_path)

    def test_append_returns_record(self, store):
        record = store.append("http://example.com/1", "Widget", 29.99, "USD")
        assert record.url == "http://example.com/1"
        assert record.title == "Widget"
        assert record.price == 29.99
        assert record.currency == "USD"
        assert record.timestamp  # non-empty

    def test_get_last_price_no_history(self, store):
        assert store.get_last_price("http://example.com/1") is None

    def test_get_last_price_single_entry(self, store):
        store.append("http://example.com/1", "Widget", 29.99, "USD")
        last = store.get_last_price("http://example.com/1")
        assert last is not None
        assert last.price == 29.99

    def test_get_last_price_multiple_entries(self, store):
        store.append("http://example.com/1", "Widget", 29.99, "USD")
        store.append("http://example.com/1", "Widget", 24.99, "USD")
        store.append("http://example.com/1", "Widget", 19.99, "USD")
        last = store.get_last_price("http://example.com/1")
        assert last.price == 19.99

    def test_get_last_price_different_urls(self, store):
        store.append("http://example.com/1", "Widget A", 29.99, "USD")
        store.append("http://example.com/2", "Widget B", 49.99, "EUR")
        assert store.get_last_price("http://example.com/1").price == 29.99
        assert store.get_last_price("http://example.com/2").price == 49.99

    def test_get_last_price_unknown_url(self, store):
        store.append("http://example.com/1", "Widget", 29.99, "USD")
        assert store.get_last_price("http://example.com/unknown") is None

    def test_get_history_empty(self, store):
        assert store.get_history("http://example.com/1") == []

    def test_get_history_returns_records(self, store):
        store.append("http://example.com/1", "Widget", 29.99, "USD")
        store.append("http://example.com/1", "Widget", 24.99, "USD")
        history = store.get_history("http://example.com/1")
        assert len(history) == 2
        assert history[0].price == 29.99
        assert history[1].price == 24.99

    def test_get_history_limit(self, store):
        for i in range(20):
            store.append("http://example.com/1", "Widget", float(i), "USD")
        history = store.get_history("http://example.com/1", limit=5)
        assert len(history) == 5
        assert history[0].price == 15.0  # last 5: 15,16,17,18,19

    def test_handles_corrupt_lines(self, store):
        # Write a valid record, then corrupt data, then another valid record
        store.append("http://example.com/1", "Widget", 29.99, "USD")
        with open(store.history_path, "a") as f:
            f.write("not json\n")
            f.write("{bad json too\n")
        store.append("http://example.com/1", "Widget", 24.99, "USD")
        last = store.get_last_price("http://example.com/1")
        assert last.price == 24.99

    def test_jsonl_format(self, store):
        store.append("http://example.com/1", "Widget", 29.99, "USD")
        with open(store.history_path) as f:
            line = f.readline().strip()
        data = json.loads(line)
        assert data["url"] == "http://example.com/1"
        assert data["price"] == 29.99
