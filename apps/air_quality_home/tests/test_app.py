"""Tests for app.py — dashboard metric classification.

Importing `app` is side-effect-free: the Streamlit UI lives in render(),
which only runs under `if __name__ == "__main__"`.
"""

import pytest

from app import classify_metric


class TestClassifyMetricBoundedRange:
    """temp (20-25) and humid (40-50) have both a lower and an upper bound."""

    @pytest.mark.parametrize(
        "metric,value,expected",
        [
            ("temp", 19.9, False),   # below lower
            ("temp", 20.0, True),    # on lower bound — inclusive
            ("temp", 22.5, True),    # mid range
            ("temp", 25.0, True),    # on upper bound — inclusive
            ("temp", 25.1, False),   # above upper
            ("humid", 39.9, False),
            ("humid", 40.0, True),
            ("humid", 45.0, True),
            ("humid", 50.0, True),
            ("humid", 50.1, False),
        ],
    )
    def test_boundaries(self, metric, value, expected):
        assert classify_metric(metric, value) is expected


class TestClassifyMetricUpperOnly:
    """co2 (<=600), voc (<=300), pm25 (<=12) have no lower bound."""

    @pytest.mark.parametrize(
        "metric,value,expected",
        [
            ("co2", 0.0, True),       # no lower bound — zero is fine
            ("co2", 600.0, True),     # on upper bound
            ("co2", 600.1, False),    # above upper
            ("voc", 0.0, True),
            ("voc", 300.0, True),
            ("voc", 300.1, False),
            ("pm25", 0.0, True),
            ("pm25", 12.0, True),
            ("pm25", 12.1, False),
        ],
    )
    def test_boundaries(self, metric, value, expected):
        assert classify_metric(metric, value) is expected
