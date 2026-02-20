"""Canonical Trading 212 ticker parsing and symbol mapping.

Single source of truth for converting T212 ticker format to:
  1. (symbol, market) — used by metrics and policy engines
  2. Finnhub-compatible symbol — used by the research client

T212 ticker formats:
  3-part: "SNOW_US_EQ"   → symbol=SNOW, exchange=US
  2-part: "CCLl_EQ"      → symbol=CCL, suffix char='l' (London)
  2-part: "ENRd_EQ"      → symbol=ENR, suffix char='d' (Frankfurt)
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Exchange registry — add new exchanges here once
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Exchange:
    """One entry in the exchange registry."""

    market: str            # normalised market label (US, UK, DE, …)
    finnhub_suffix: str    # appended to symbol for Finnhub API ("", ".L", ".DE", …)


# Keyed by the middle part of 3-part tickers (e.g. "US" in "SNOW_US_EQ")
_EXCHANGE_3PART: dict[str, Exchange] = {
    "US": Exchange(market="US", finnhub_suffix=""),
    "LSE": Exchange(market="UK", finnhub_suffix=".L"),
    "AS": Exchange(market="NL", finnhub_suffix=".AS"),
    "PA": Exchange(market="FR", finnhub_suffix=".PA"),
    "MI": Exchange(market="IT", finnhub_suffix=".MI"),
    "MC": Exchange(market="ES", finnhub_suffix=".MC"),
    "TO": Exchange(market="CA", finnhub_suffix=".TO"),
    "HK": Exchange(market="HK", finnhub_suffix=".HK"),
    "T": Exchange(market="JP", finnhub_suffix=".T"),
}

# Keyed by the trailing character of 2-part tickers (e.g. "l" in "CCLl_EQ")
_EXCHANGE_SUFFIX_CHAR: dict[str, Exchange] = {
    "l": Exchange(market="UK", finnhub_suffix=".L"),
    "d": Exchange(market="DE", finnhub_suffix=".DE"),
}

_UNKNOWN = Exchange(market="Other", finnhub_suffix="")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_ticker(t212_ticker: str) -> tuple[str, str]:
    """Parse Trading 212 ticker into (symbol, market).

    >>> parse_ticker("SNOW_US_EQ")
    ('SNOW', 'US')
    >>> parse_ticker("CCLl_EQ")
    ('CCL', 'UK')
    >>> parse_ticker("ENRd_EQ")
    ('ENR', 'DE')
    >>> parse_ticker("GOOGL_US_EQ")
    ('GOOGL', 'US')
    """
    symbol, exchange = _resolve(t212_ticker)
    return symbol, exchange.market


def to_finnhub_symbol(t212_ticker: str) -> str:
    """Convert Trading 212 ticker to Finnhub-compatible symbol.

    >>> to_finnhub_symbol("SNOW_US_EQ")
    'SNOW'
    >>> to_finnhub_symbol("CCLl_EQ")
    'CCL.L'
    >>> to_finnhub_symbol("ENRd_EQ")
    'ENR.DE'
    >>> to_finnhub_symbol("GOOGL_US_EQ")
    'GOOGL'
    """
    symbol, exchange = _resolve(t212_ticker)
    return symbol + exchange.finnhub_suffix


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _resolve(t212_ticker: str) -> tuple[str, Exchange]:
    """Resolve a T212 ticker to (clean_symbol, Exchange)."""
    parts = t212_ticker.split("_")

    # 3-part: SNOW_US_EQ
    if len(parts) == 3:
        return parts[0], _EXCHANGE_3PART.get(parts[1], _UNKNOWN)

    # 2-part: CCLl_EQ
    if len(parts) == 2:
        raw = parts[0]
        for suffix_char, exchange in _EXCHANGE_SUFFIX_CHAR.items():
            if raw.endswith(suffix_char) and len(raw) > 1:
                return raw[: -len(suffix_char)], exchange
        return raw, _UNKNOWN

    # Unknown format
    return t212_ticker, _UNKNOWN
