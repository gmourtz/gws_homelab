"""Tests for scraper.py — price parsing and product extraction."""

from unittest.mock import patch, MagicMock

import pytest

from scraper import _parse_price, _extract_currency, scrape_product, Product


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
    """Test full product scraping with mocked HTTP."""

    def test_dom_price_extraction(self):
        """Test extraction from a-price-whole + a-price-fraction classes."""
        html = '''
        <span id="productTitle" class="a-size-large">Test Widget</span>
        <span class="a-price-whole">174,</span>
        <span class="a-price-fraction">52</span>
        "priceCurrency": "USD"
        '''
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        with patch("scraper.requests.get", return_value=mock_resp):
            product = scrape_product("https://www.amazon.com/dp/TEST123")

        assert product.title == "Test Widget"
        assert product.price == 174.52
        assert product.currency == "USD"

    def test_structured_data_price_preferred(self):
        """Structured data price should be tried before DOM classes."""
        html = '''
        <span id="productTitle" class="a-size-large">Widget</span>
        "priceAmount": "99.99"
        <span class="a-price-whole">174,</span>
        <span class="a-price-fraction">52</span>
        "priceCurrency": "EUR"
        ''' + "x" * 5000
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        with patch("scraper.requests.get", return_value=mock_resp):
            product = scrape_product("https://www.amazon.de/dp/TEST456")

        assert product.price == 99.99  # structured data wins

    def test_no_price_returns_none(self):
        """When no price pattern matches, price should be None."""
        html = '<span id="productTitle" class="a-size-large">No Price Item</span>'
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        with patch("scraper.requests.get", return_value=mock_resp):
            product = scrape_product("https://www.amazon.com/dp/NOPRICE")

        assert product.title == "No Price Item"
        assert product.price is None

    def test_dom_price_whole_only(self):
        """When fraction is missing, default to .00."""
        html = '''
        <span id="productTitle" class="a-size-large">Round Price</span>
        <span class="a-price-whole">200,</span>
        '''
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        with patch("scraper.requests.get", return_value=mock_resp):
            product = scrape_product("https://www.amazon.com/dp/ROUND")

        assert product.price == 200.0
