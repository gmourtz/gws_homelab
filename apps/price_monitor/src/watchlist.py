"""Product watchlist configuration loader."""

import json
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class WatchItem:
    """A product to monitor."""

    url: str
    label: str  # friendly name for notifications


def load_watchlist(path: str) -> list[WatchItem]:
    """Load products from a JSON config file.

    Expected format:
    [
        {"url": "https://www.amazon.com/dp/...", "label": "PS5 Controller"},
        {"url": "https://www.amazon.de/dp/...", "label": "USB-C Hub"}
    ]
    """
    if not os.path.exists(path):
        log.error("Watchlist not found: %s", path)
        return []

    with open(path) as f:
        data = json.load(f)

    if not isinstance(data, list):
        log.error("Watchlist must be a JSON array")
        return []

    items = []
    for entry in data:
        url = entry.get("url", "").strip()
        label = entry.get("label", "").strip()
        if not url:
            log.warning("Skipping watchlist entry with no URL: %s", entry)
            continue
        if not label:
            label = url.split("/dp/")[-1][:20] if "/dp/" in url else "Unknown"
        items.append(WatchItem(url=url, label=label))

    log.info("Loaded %d products from watchlist", len(items))
    return items
