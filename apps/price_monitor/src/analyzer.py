"""LLM-powered deal analysis using local Ollama."""

import logging

import requests

log = logging.getLogger(__name__)


def analyze_deal(
    title: str,
    current_price: float,
    previous_price: float,
    currency: str,
    url: str,
    base_url: str,
    api_key: str,
    model: str,
) -> str:
    """Ask the local LLM to briefly analyze a price drop.

    Returns a short analysis string, or a fallback if the LLM is unavailable.
    """
    drop = previous_price - current_price
    drop_pct = (drop / previous_price) * 100

    prompt = (
        f"A product I'm tracking just dropped in price.\n\n"
        f"Product: {title}\n"
        f"Previous price: {currency} {previous_price:.2f}\n"
        f"Current price: {currency} {current_price:.2f}\n"
        f"Drop: {currency} {drop:.2f} ({drop_pct:.1f}%)\n\n"
        f"In 1-2 sentences, tell me: is this a significant deal? "
        f"Should I buy now or wait for a bigger drop? Be concise."
    )

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a concise shopping advisor. Give brief, practical advice."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 150,
                "temperature": 0.3,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("LLM analysis failed: %s — using fallback", e)
        if drop_pct >= 20:
            return f"Big drop of {drop_pct:.0f}%! Looks like a solid deal."
        elif drop_pct >= 10:
            return f"Decent {drop_pct:.0f}% drop. Worth considering."
        else:
            return f"Small {drop_pct:.1f}% drop. Might want to wait for a better deal."
