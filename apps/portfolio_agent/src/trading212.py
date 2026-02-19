"""Trading 212 API client (read-only)."""

import base64
import logging
import time

import requests

log = logging.getLogger(__name__)


class Trading212Client:
    """Minimal read-only client for Trading 212 public API v0.

    Uses HTTP Basic Authentication (API_KEY:API_SECRET → Base64).
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://live.trading212.com/api/v0",
    ):
        self.base_url = base_url.rstrip("/")
        credentials = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Basic {credentials}"})

    def _get(self, endpoint: str) -> dict | list | None:
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            log.error("T212 API error on %s: %s", endpoint, e)
            return None

    def get_account_summary(self) -> dict | None:
        """Fetch account summary (cash, invested, P/L, total value)."""
        return self._get("/equity/account/summary")

    def get_positions(self) -> list | None:
        """Fetch all open positions."""
        return self._get("/equity/positions")

    def get_portfolio_snapshot(self) -> dict | None:
        """Fetch a complete portfolio snapshot with polite delays."""
        summary = self.get_account_summary()
        time.sleep(0.5)
        positions = self.get_positions()

        if any(x is None for x in [summary, positions]):
            log.error("Failed to fetch complete portfolio snapshot")
            return None

        return {
            "account": {
                "id": summary.get("id"),
                "currency": summary.get("currency", "Unknown"),
            },
            "cash": {
                "free": summary.get("cash", {}).get("availableToTrade", 0),
                "invested": summary.get("investments", {}).get("totalCost", 0),
                "ppl": summary.get("investments", {}).get("unrealizedProfitLoss", 0),
                "realizedPpl": summary.get("investments", {}).get("realizedProfitLoss", 0),
                "totalValue": summary.get("totalValue", 0),
            },
            "positions": positions,
        }
