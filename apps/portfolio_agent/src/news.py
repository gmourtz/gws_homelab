"""Finnhub research client — fundamentals, news, profiles, earnings.

Provides all external market data.  Every method returns structured dicts
or None on failure — never raises.
"""

import logging
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)

from tickers import to_finnhub_symbol as extract_symbol


class FinnhubClient:
    """Finnhub client for fundamentals, news, company profiles, and earnings."""

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str, call_delay: float = 1.1):
        self.session = requests.Session()
        self.session.params = {"token": api_key}
        self.call_delay = call_delay  # seconds between calls (free tier: 60/min)

    def _get(self, endpoint: str, params: dict | None = None) -> dict | list | None:
        url = f"{self.BASE_URL}{endpoint}"
        try:
            resp = self.session.get(url, params=params or {}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            log.warning("Finnhub error on %s: %s", endpoint, e)
            return None

    # --- Company news ---
    def get_company_news(self, symbol: str, days_back: int = 7) -> list:
        """Fetch recent news articles for a symbol."""
        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        result = self._get(
            "/company-news",
            {"symbol": symbol, "from": from_date, "to": to_date},
        )
        return (result or [])[:5]

    # --- Basic financials ---
    def get_basic_financials(self, symbol: str) -> dict | None:
        """Fetch key fundamental metrics for a symbol."""
        result = self._get("/stock/metric", {"symbol": symbol, "metric": "all"})
        if not result:
            return None

        m = result.get("metric", {})
        return {
            "pe": m.get("peBasicExclExtraTTM"),
            "pb": m.get("pbQuarterly"),
            "div_yield": m.get("dividendYieldIndicatedAnnual"),
            "eps_growth": m.get("epsGrowthTTMYoy"),
            "rev_growth": m.get("revenueGrowthTTMYoy"),
            "debt_to_equity": m.get("totalDebt/totalEquityQuarterly"),
            "roe": m.get("roeTTM"),
            "beta": m.get("beta"),
            "w52_high": m.get("52WeekHigh"),
            "w52_low": m.get("52WeekLow"),
            "market_cap": m.get("marketCapitalization"),
            "net_margin": m.get("netProfitMarginTTM"),
            "current_ratio": m.get("currentRatioQuarterly"),
        }

    # --- Company profile (sector / industry) ---
    def get_company_profile(self, symbol: str) -> dict | None:
        """Fetch company profile for sector classification."""
        result = self._get("/stock/profile2", {"symbol": symbol})
        if not result or not result.get("name"):
            return None
        return {
            "sector": result.get("finnhubIndustry", "Unknown"),
            "country": result.get("country", "Unknown"),
            "market_cap": result.get("marketCapitalization"),
            "ipo_date": result.get("ipo"),
        }

    # --- Earnings calendar ---
    def get_earnings_calendar(self, days_ahead: int = 14) -> dict[str, str]:
        """Fetch upcoming earnings dates.  Returns {symbol: date_str}."""
        from_date = datetime.now().strftime("%Y-%m-%d")
        to_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        result = self._get(
            "/calendar/earnings", {"from": from_date, "to": to_date}
        )
        if not result:
            return {}

        earnings: dict[str, str] = {}
        for item in result.get("earningsCalendar", []):
            sym = item.get("symbol")
            date = item.get("date")
            if sym and date:
                earnings[sym] = date
        return earnings
