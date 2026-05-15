"""Tests for scraper.py — price parsing and product extraction."""

from contextlib import ExitStack
from unittest.mock import patch, MagicMock

import pytest

from scraper import _parse_price, _extract_currency, scrape_product, Product


def _mock_page(title_text, price_text, html=""):
    """Create a mock Playwright page with given title and price."""
    title_el = MagicMock()
    title_el.inner_text.return_value = title_text

    price_el = None
    if price_text is not None:
        price_el = MagicMock()
        price_el.inner_text.return_value = price_text

    page = MagicMock()
    page.content.return_value = html or '"priceCurrency": "USD"'
    page.goto = MagicMock()
    page.wait_for_selector = MagicMock()

    def query_selector_side_effect(selector):
        if selector == "#productTitle":
            return title_el
        if "a-offscreen" in selector and price_el:
            return price_el
        return None

    page.query_selector.side_effect = query_selector_side_effect
    return page


def _patch_scraper(page):
    """Context manager that patches _get_context and _set_us_location."""
    context = MagicMock()
    context.new_page.return_value = page
    stack = ExitStack()
    stack.enter_context(patch("scraper._get_context", return_value=context))
    stack.enter_context(patch("scraper._set_us_location"))
    return stack


class TestParsePrice:
    """Test price string parsing."""

    def test_simple_us_price(self):
        assert _parse_price("29.99") == 29.99

    def test_eu_price_comma_decimal(self):
        assert _parse_price("29,99") == 29.99

    def test_eu_price_with_thousands(self):
        assert _parse_price("1.299,00") == 1299.00

    def test_us_price_with_thousands(self):
        assert _parse_price("1,299.00") == 1299.00

    def test_whole_number(self):
        assert _parse_price("100") == 100.0

    def test_empty_string(self):
        assert _parse_price("") is None

    def test_none_input(self):
        assert _parse_price(None) is None

    def test_invalid_string(self):
        assert _parse_price("abc") is None

    def test_large_eu_price(self):
        assert _parse_price("12.345,67") == 12345.67

    def test_large_us_price(self):
        assert _parse_price("12,345.67") == 12345.67

    def test_zero(self):
        assert _parse_price("0") == 0.0

    def test_single_digit_cents(self):
        # "9,5" could be 9.5 in EU format
        assert _parse_price("9,5") == 9.5

    def test_dollar_sign_stripped(self):
        assert _parse_price("$249.98") == 249.98

    def test_euro_sign_stripped(self):
        assert _parse_price("€29,99") == 29.99

    def test_pound_sign_stripped(self):
        assert _parse_price("£1,299.00") == 1299.0


class TestExtractCurrency:
    """Test currency detection from HTML."""

    def test_eur_symbol(self):
        html = '<span class="price">€29.99</span>' + "x" * 5000
        assert _extract_currency(html) == "EUR"

    def test_gbp_symbol(self):
        html = '<span class="price">£29.99</span>' + "x" * 5000
        assert _extract_currency(html) == "GBP"

    def test_structured_data_currency(self):
        html = '"priceCurrency": "USD"'
        assert _extract_currency(html) == "USD"

    def test_default_usd(self):
        assert _extract_currency("no currency here " * 500) == "USD"


class TestScrapeProduct:
    """Test full product scraping with mocked Playwright."""

    def test_deal_price_extraction(self):
        """The JS-rendered deal price should be extracted."""
        page = _mock_page("Test Widget", "$249.98")
        with _patch_scraper(page):
            product = scrape_product("https://www.amazon.com/dp/TEST123")
        assert product.title == "Test Widget"
        assert product.price == 249.98
        assert product.currency == "USD"

    def test_regular_price_extraction(self):
        """Regular prices (no deal) should work too."""
        page = _mock_page("Regular Item", "$649.99")
        with _patch_scraper(page):
            product = scrape_product("https://www.amazon.com/dp/REGULAR")
        assert product.price == 649.99

    def test_eu_price_extraction(self):
        """EU prices with comma decimal should parse correctly."""
        page = _mock_page("EU Widget", "29,99\xa0€", html='"priceCurrency": "EUR"')
        with _patch_scraper(page):
            product = scrape_product("https://www.amazon.de/dp/EU123")
        assert product.price == 29.99
        assert product.currency == "EUR"

    def test_no_price_returns_none(self):
        """When no price element exists, price should be None."""
        page = _mock_page("No Price Item", None)
        with _patch_scraper(page):
            product = scrape_product("https://www.amazon.com/dp/NOPRICE")
        assert product.title == "No Price Item"
        assert product.price is None

    def test_price_with_thousands(self):
        """Prices like $1,299.00 should parse correctly."""
        page = _mock_page("Expensive Item", "$1,299.00")
        with _patch_scraper(page):
            product = scrape_product("https://www.amazon.com/dp/EXPENSIVE")
        assert product.price == 1299.0
