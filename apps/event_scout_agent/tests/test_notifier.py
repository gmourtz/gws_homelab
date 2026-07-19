"""Notifier tests — per-recipient send with mocked HTTP."""

from unittest.mock import MagicMock

import notifier as notifier_mod
from notifier import ConsoleNotifier, TelegramNotifier


def _resp(ok: bool = True):
    r = MagicMock()
    r.ok = ok
    return r


def _recipient(name="sultan", token="tokS", chat="chatS"):
    return {"name": name, "bot_token": token, "chat_id": chat, "backfill": False}


def test_sends_to_the_recipients_own_bot_and_chat(monkeypatch):
    post = MagicMock(return_value=_resp(ok=True))
    monkeypatch.setattr(notifier_mod.requests, "post", post)

    assert TelegramNotifier().send(_recipient(), "hi") is True

    post.assert_called_once()
    assert "bottokS/sendMessage" in post.call_args.args[0]
    assert post.call_args.kwargs["json"]["chat_id"] == "chatS"


def test_returns_false_when_send_fails(monkeypatch):
    # first (Markdown) and retry (plain) both fail
    monkeypatch.setattr(notifier_mod.requests, "post", MagicMock(return_value=_resp(ok=False)))
    assert TelegramNotifier().send(_recipient(), "hi") is False


def test_console_notifier_ignores_creds(capsys):
    assert ConsoleNotifier().send(_recipient(name="console"), "body text") is True
    out = capsys.readouterr().out
    assert "console" in out
    assert "body text" in out
