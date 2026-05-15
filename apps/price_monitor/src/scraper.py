"""Amazon product price scraper using headless Chromium.

Uses Playwright to render the full page (including JS-rendered deal prices,
coupons, and 'Limited time deal' discounts that are invisible to plain HTTP).

Amazon geo-locates by IP and hides prices for products that can't ship to the
detected country.  We work around this by first visiting amazon.com and setting
the delivery ZIP code to a US address via the location popup, which stores the
preference in session cookies for subsequent page loads.
"""

import logging
import os
import re
from dataclasses import dataclass

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

# Default US ZIP code — can be overridden via env var
_US_ZIP = os.environ.get("AMAZON_ZIP", "10001")


@dataclass
class Product:
    """Scraped product data."""

    url: str
    title: str
    price: float | None
    currency: str


def _parse_price(text: str) -> float | None:
    """Parse a price string like '$29.99' or '1.299,00' into a float."""
    if not text:
        return None
    # Strip currency symbols and whitespace
    text = re.sub(r'[^\d.,]', '', text).strip()
    if not text:
        return None
    # Remove thousands separators
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
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


# Shared browser context (reused across calls within a process)
_browser = None
_context = None
_playwright = None
_location_set = False


def _get_context():
    """Lazily start a shared headless Chromium browser and context.

    The context persists cookies between calls so the US delivery location
    only needs to be set once per process lifetime.
    """
    global _browser, _context, _playwright
    if _browser is None or not _browser.is_connected():
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        log.info("Headless Chromium started")
    if _context is None:
        _context = _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 800},
            timezone_id="America/New_York",
        )
        _context.add_cookies([
            {"name": "i18n-prefs", "value": "USD", "domain": ".amazon.com", "path": "/"},
            {"name": "lc-main", "value": "en_US", "domain": ".amazon.com", "path": "/"},
            {"name": "sp-cdn", "value": '"L5Z9:US"', "domain": ".amazon.com", "path": "/"},
        ])
    return _context


def _set_us_location(context) -> None:
    """Set the Amazon delivery location to a US ZIP code.

    Opens amazon.com, clicks the location popup, enters the ZIP code, and
    closes the modal.  The resulting session cookies keep the location for
    all subsequent page loads in this context.
    """
    global _location_set
    if _location_set:
        return

    page = context.new_page()
    try:
        page.goto("https://www.amazon.com", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2000)

        page.click("#glow-ingress-block", timeout=5000)
        page.wait_for_timeout(2000)

        page.fill("#GLUXZipUpdateInput", _US_ZIP)
        page.wait_for_timeout(500)

        page.click(
            "#GLUXZipUpdate input[type='submit'], #GLUXZipUpdate .a-button-input",
            timeout=5000,
        )
        page.wait_for_timeout(3000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        deliver = page.query_selector("#glow-ingress-line2")
        if deliver:
            log.info("Delivery location set to: %s", deliver.inner_text().strip())

        _location_set = True
    except Exception:
        log.warning("Failed to set US delivery location — prices may be unavailable", exc_info=True)
    finally:
        page.close()


def scrape_product(url: str, timeout: int = 30) -> Product:
    """Fetch an Amazon product page with headless Chromium and extract title + price.

    On first call, sets the delivery location to a US ZIP code so Amazon shows
    US prices and availability.  Renders JavaScript so deal prices, coupons,
    and limited-time deals are visible.

    Returns a Product with price=None if extraction fails.
    """
    context = _get_context()
    _set_us_location(context)

    page = context.new_page()

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

        # Wait for the price to render (Amazon loads prices dynamically)
        try:
            page.wait_for_selector(".a-price .a-offscreen", timeout=10000)
        except PlaywrightTimeout:
            log.debug("Price selector didn't appear within 10s, proceeding with what we have")

        html = page.content()

        # Extract title
        title_el = page.query_selector("#productTitle")
        title = title_el.inner_text().strip() if title_el else "Unknown Product"

        # Extract price — priority order:
        #   1. corePrice_feature_div — deal / current price (most reliable)
        #   2. corePrice_desktop — regular price display
        #   3. corePriceDisplay_desktop — alternate layout
        #   4. apex_desktop — fallback (list / was-price area)
        price = None
        for selector in [
            "#corePrice_feature_div .a-price .a-offscreen",
            "#corePrice_desktop .a-price .a-offscreen",
            "#corePriceDisplay_desktop .a-price .a-offscreen",
            "#apex_desktop .a-price .a-offscreen",
        ]:
            el = page.query_selector(selector)
            if el:
                price_text = el.inner_text().strip()
                price = _parse_price(price_text)
                if price is not None and price > 0:
                    log.debug("Price from selector '%s': %s → %.2f", selector, price_text, price)
                    break
                price = None

        currency = _extract_currency(html)

        return Product(url=url, title=title, price=price, currency=currency)

    finally:
        page.close()
