"""Amazon product price scraper."""

import logging
import re
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

# Rotate user agents to reduce blocking
_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# Structured data price patterns (JSON-LD)
_PRICE_PATTERNS = [
    re.compile(r'"priceAmount"\s*:\s*"?([\d.,]+)"?'),
    re.compile(r'"price"\s*:\s*"?([\d.,]+)"?'),
]

# HTML DOM price patterns (a-price-whole + a-price-fraction)
_PRICE_WHOLE_PATTERN = re.compile(
    r'class="a-price-whole"[^>]*>([\d,]+)', re.DOTALL
)
_PRICE_FRACTION_PATTERN = re.compile(
    r'class="a-price-fraction"[^>]*>(\d+)', re.DOTALL
)

# Title extraction
_TITLE_PATTERN = re.compile(r'<span[^>]*id="productTitle"[^>]*>\s*(.+?)\s*</span>', re.DOTALL)


@dataclass
class Product:
    """Scraped product data."""

    url: str
    title: str
    price: float | None
    currency: str


def _parse_price(text: str) -> float | None:
    """Parse a price string like '29,99' or '1.299,00' into a float."""
    if not text:
        return None
    # Remove thousands separators (dots in EU, commas in US)
    # Heuristic: if last separator is comma and has 2-3 digits after → EU format
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            # EU: 1.299,00 → 1299.00
            text = text.replace(".", "").replace(",", ".")
        else:
            # US: 1,299.00
            text = text.replace(",", "")
    elif "," in text:
        # Could be EU decimal: 29,99
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _extract_currency(html: str) -> str:
    """Try to extract currency symbol from page."""
    m = re.search(r'"priceCurrency"\s*:\s*"(\w{3})"', html)
    if m:
        return m.group(1)
    if "€" in html[:5000]:
        return "EUR"
    if "£" in html[:5000]:
        return "GBP"
    return "USD"


def scrape_product(url: str, timeout: int = 15) -> Product:
    """Fetch an Amazon product page and extract title + price.

    Returns a Product with price=None if extraction fails.
    """
    import hashlib

    # Pick a deterministic but rotating user agent based on URL
    ua_index = int(hashlib.md5(url.encode()).hexdigest(), 16) % len(_USER_AGENTS)

    headers = {
        "User-Agent": _USER_AGENTS[ua_index],
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }

    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    # Extract title
    title_match = _TITLE_PATTERN.search(html)
    title = title_match.group(1).strip() if title_match else "Unknown Product"

    # Extract price — try structured data patterns first
    price = None
    for pattern in _PRICE_PATTERNS:
        m = pattern.search(html)
        if m:
            price = _parse_price(m.group(1))
            if price is not None and price > 0:
                break
            price = None

    # Fall back to DOM price classes (a-price-whole + a-price-fraction)
    if price is None:
        whole_match = _PRICE_WHOLE_PATTERN.search(html)
        fraction_match = _PRICE_FRACTION_PATTERN.search(html)
        if whole_match:
            whole = whole_match.group(1).replace(",", "")
            fraction = fraction_match.group(1) if fraction_match else "00"
            price = _parse_price(f"{whole}.{fraction}")

    currency = _extract_currency(html)

    return Product(url=url, title=title, price=price, currency=currency)
