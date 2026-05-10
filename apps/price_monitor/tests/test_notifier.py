"""Tests for notifier.py — Telegram message splitting."""

import pytest

from notifier import TelegramNotifier


class TestSplit:
    """Test the static _split method (no network calls)."""

    def test_short_message_no_split(self):
        chunks = TelegramNotifier._split("Hello world", 4000)
        assert chunks == ["Hello world"]

    def test_exact_limit(self):
        msg = "a" * 4000
        chunks = TelegramNotifier._split(msg, 4000)
        assert len(chunks) == 1

    def test_split_at_newline(self):
        msg = ("Line one\n" * 500).strip()
        chunks = TelegramNotifier._split(msg, 100)
        assert all(len(c) <= 100 for c in chunks)
        recombined = "\n".join(chunks)
        assert recombined.replace("\n", "") == msg.replace("\n", "")

    def test_split_long_line_no_newlines(self):
        msg = "x" * 10000
        chunks = TelegramNotifier._split(msg, 4000)
        assert len(chunks) == 3
        assert all(len(c) <= 4000 for c in chunks)

    def test_empty_message(self):
        chunks = TelegramNotifier._split("", 4000)
        assert chunks == [""]
