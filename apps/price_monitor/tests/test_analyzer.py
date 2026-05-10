"""Tests for analyzer.py — LLM deal analysis."""

from unittest.mock import patch, MagicMock

import pytest

from analyzer import analyze_deal


class TestAnalyzeDeal:
    """Test deal analysis with mocked LLM."""

    def test_successful_llm_call(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Great deal, buy now!"}}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("analyzer.requests.post", return_value=mock_resp) as mock_post:
            result = analyze_deal(
                title="Widget",
                current_price=20.0,
                previous_price=30.0,
                currency="USD",
                url="http://example.com",
                base_url="http://localhost:11434/v1",
                api_key="test-key",
                model="qwen3:1.7b",
            )

        assert result == "Great deal, buy now!"
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "chat/completions" in call_args[0][0]

    def test_fallback_on_llm_error_big_drop(self):
        with patch("analyzer.requests.post", side_effect=Exception("timeout")):
            result = analyze_deal(
                title="Widget",
                current_price=20.0,
                previous_price=30.0,
                currency="USD",
                url="http://example.com",
                base_url="http://localhost:11434/v1",
                api_key="test-key",
                model="qwen3:1.7b",
            )
        assert "33%" in result
        assert "solid deal" in result.lower() or "drop" in result.lower()

    def test_fallback_on_llm_error_medium_drop(self):
        with patch("analyzer.requests.post", side_effect=Exception("timeout")):
            result = analyze_deal(
                title="Widget",
                current_price=85.0,
                previous_price=100.0,
                currency="EUR",
                url="http://example.com",
                base_url="http://localhost:11434/v1",
                api_key="test",
                model="qwen3:1.7b",
            )
        assert "15%" in result

    def test_fallback_on_llm_error_small_drop(self):
        with patch("analyzer.requests.post", side_effect=Exception("timeout")):
            result = analyze_deal(
                title="Widget",
                current_price=95.0,
                previous_price=100.0,
                currency="USD",
                url="http://example.com",
                base_url="http://localhost:11434/v1",
                api_key="test",
                model="qwen3:1.7b",
            )
        assert "5.0%" in result
        assert "wait" in result.lower()

    def test_request_includes_auth_header(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Analysis"}}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("analyzer.requests.post", return_value=mock_resp) as mock_post:
            analyze_deal(
                title="Widget",
                current_price=20.0,
                previous_price=30.0,
                currency="USD",
                url="http://example.com",
                base_url="http://localhost:11434/v1",
                api_key="my-secret-key",
                model="qwen3:1.7b",
            )

        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["headers"]["Authorization"] == "Bearer my-secret-key"
