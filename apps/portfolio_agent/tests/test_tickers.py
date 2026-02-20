"""Tests for tickers.py — unified T212 ticker parsing."""

import pytest

from tickers import parse_ticker, to_finnhub_symbol


# ---------------------------------------------------------------------------
# parse_ticker
# ---------------------------------------------------------------------------

class TestParseTicker:
    """Test (symbol, market) extraction from T212 tickers."""

    @pytest.mark.parametrize(
        "t212, expected_symbol, expected_market",
        [
            ("SNOW_US_EQ", "SNOW", "US"),
            ("GOOGL_US_EQ", "GOOGL", "US"),
            ("AAPL_US_EQ", "AAPL", "US"),
            ("CCLl_EQ", "CCL", "UK"),
            ("ENRd_EQ", "ENR", "DE"),
            ("ASML_AS_EQ", "ASML", "NL"),
            ("TTE_PA_EQ", "TTE", "FR"),
            ("ENEL_MI_EQ", "ENEL", "IT"),
            ("SAN_MC_EQ", "SAN", "ES"),
            ("SHOP_TO_EQ", "SHOP", "CA"),
            ("9988_HK_EQ", "9988", "HK"),
            ("7203_T_EQ", "7203", "JP"),
        ],
    )
    def test_known_exchanges(self, t212, expected_symbol, expected_market):
        symbol, market = parse_ticker(t212)
        assert symbol == expected_symbol
        assert market == expected_market

    def test_unknown_3part_exchange(self):
        symbol, market = parse_ticker("XYZ_ZZ_EQ")
        assert symbol == "XYZ"
        assert market == "Other"

    def test_unknown_2part_no_suffix(self):
        symbol, market = parse_ticker("FOO_EQ")
        assert symbol == "FOO"
        assert market == "Other"

    def test_single_part_passthrough(self):
        symbol, market = parse_ticker("RAWSTRING")
        assert symbol == "RAWSTRING"
        assert market == "Other"

    def test_suffix_char_must_have_symbol(self):
        """Single-char ticker like 'd_EQ' should not match suffix."""
        symbol, market = parse_ticker("d_EQ")
        assert symbol == "d"
        assert market == "Other"


# ---------------------------------------------------------------------------
# to_finnhub_symbol
# ---------------------------------------------------------------------------

class TestToFinnhubSymbol:
    """Test Finnhub symbol generation from T212 tickers."""

    @pytest.mark.parametrize(
        "t212, expected",
        [
            ("SNOW_US_EQ", "SNOW"),
            ("GOOGL_US_EQ", "GOOGL"),
            ("CCLl_EQ", "CCL.L"),
            ("ENRd_EQ", "ENR.DE"),
            ("ASML_AS_EQ", "ASML.AS"),
            ("TTE_PA_EQ", "TTE.PA"),
            ("ENEL_MI_EQ", "ENEL.MI"),
            ("SAN_MC_EQ", "SAN.MC"),
            ("SHOP_TO_EQ", "SHOP.TO"),
            ("9988_HK_EQ", "9988.HK"),
            ("7203_T_EQ", "7203.T"),
        ],
    )
    def test_known_symbols(self, t212, expected):
        assert to_finnhub_symbol(t212) == expected

    def test_unknown_3part(self):
        assert to_finnhub_symbol("XYZ_ZZ_EQ") == "XYZ"

    def test_passthrough(self):
        assert to_finnhub_symbol("JUSTASYMBOL") == "JUSTASYMBOL"


# ---------------------------------------------------------------------------
# Consistency: parse_ticker and to_finnhub_symbol agree on symbol root
# ---------------------------------------------------------------------------

class TestConsistency:
    """Both functions must extract the same base symbol."""

    @pytest.mark.parametrize(
        "t212",
        [
            "SNOW_US_EQ",
            "CCLl_EQ",
            "ENRd_EQ",
            "ASML_AS_EQ",
            "FOO_EQ",
            "RAWSTRING",
        ],
    )
    def test_same_base_symbol(self, t212):
        symbol_from_parse, _ = parse_ticker(t212)
        fh_symbol = to_finnhub_symbol(t212)
        # The Finnhub symbol starts with the same base symbol
        assert fh_symbol.startswith(symbol_from_parse)
