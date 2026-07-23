"""Telegram notification sender. Each recipient carries its own bot token and
chat id, so subscribers on different bots are notified independently."""

import logging

import requests

log = logging.getLogger(__name__)


class TelegramNotifier:
    def send(self, recipient: dict, text: str) -> bool:
        """Send to one recipient's chat, splitting to stay under Telegram's limit."""
        ok = True
        for chunk in self._split(text, 4000):
            if not self._send_chunk(recipient["name"], recipient["bot_token"], recipient["chat_id"], chunk):
                ok = False
        return ok

    def _send_chunk(self, name: str, bot_token: str, chat_id: str, text: str) -> bool:
        base_url = f"https://api.telegram.org/bot{bot_token}"
        try:
            resp = requests.post(
                f"{base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )
            if not resp.ok:
                # Retry without Markdown if parsing fails
                log.warning("Markdown send failed, retrying as plain text")
                resp = requests.post(
                    f"{base_url}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                    timeout=30,
                )
                if not resp.ok:
                    log.error("Telegram send failed: %s", resp.text)
                    return False
            # No delete/recall feature in this app — log enough to do it by hand:
            # POST https://api.telegram.org/bot<TOKEN>/deleteMessage with these two.
            message_id = (resp.json() or {}).get("result", {}).get("message_id")
            log.info(
                "Sent to %r (chat_id=%s, message_id=%s) — recall manually via "
                "bot<TOKEN>/deleteMessage?chat_id=%s&message_id=%s",
                name, chat_id, message_id, chat_id, message_id,
            )
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


class ConsoleNotifier:
    """Prints instead of sending — used for local dry runs (--once without
    Telegram credentials)."""

    def send(self, recipient: dict, text: str) -> bool:
        header = f" [{recipient['name']}]" if recipient.get("name") else ""
        print("\n" + "=" * 60 + header + "\n" + text + "\n" + "=" * 60)
        return True
