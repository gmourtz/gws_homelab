"""Telegram notification sender."""

import logging

import requests

log = logging.getLogger(__name__)


class TelegramNotifier:
    """Send messages to a Telegram chat via Bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, text: str) -> bool:
        """Send a message, splitting into chunks if it exceeds Telegram's limit."""
        chunks = self._split(text, 4000)
        ok = True
        for chunk in chunks:
            if not self._send_chunk(chunk):
                ok = False
        return ok

    def _send_chunk(self, text: str) -> bool:
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
            if not resp.ok:
                log.warning("Markdown send failed, retrying as plain text")
                resp = requests.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "disable_web_page_preview": False,
                    },
                    timeout=30,
                )
                if not resp.ok:
                    log.error("Telegram send failed: %s", resp.text)
                    return False
            return True
        except requests.RequestException as e:
            log.error("Telegram error: %s", e)
            return False

    @staticmethod
    def _split(text: str, max_len: int) -> list[str]:
        """Split text at line boundaries to stay under max_len."""
        if len(text) <= max_len:
            return [text]

        chunks: list[str] = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            split_at = text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks
