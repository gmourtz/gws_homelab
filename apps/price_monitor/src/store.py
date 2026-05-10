"""Price history store — JSON-lines file for tracking price changes."""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass
class PriceRecord:
    """A single price observation."""

    url: str
    title: str
    price: float
    currency: str
    timestamp: str  # ISO 8601


class PriceStore:
    """Append-only JSONL store for price history."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.history_path = os.path.join(data_dir, "price_history.jsonl")
        os.makedirs(data_dir, exist_ok=True)

    def append(self, url: str, title: str, price: float, currency: str) -> PriceRecord:
        """Record a price observation."""
        record = PriceRecord(
            url=url,
            title=title,
            price=price,
            currency=currency,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with open(self.history_path, "a") as f:
            f.write(json.dumps(record.__dict__) + "\n")
        return record

    def get_last_price(self, url: str) -> PriceRecord | None:
        """Get the most recent price record for a URL."""
        if not os.path.exists(self.history_path):
            return None

        last = None
        with open(self.history_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("url") == url:
                        last = PriceRecord(**data)
                except (json.JSONDecodeError, TypeError):
                    continue
        return last

    def get_history(self, url: str, limit: int = 10) -> list[PriceRecord]:
        """Get the last N price records for a URL."""
        if not os.path.exists(self.history_path):
            return []

        records: list[PriceRecord] = []
        with open(self.history_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("url") == url:
                        records.append(PriceRecord(**data))
                except (json.JSONDecodeError, TypeError):
                    continue
        return records[-limit:]
