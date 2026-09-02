import json
import logging

import httpx
import pytest

from mpmt.notifier import send


@pytest.mark.asyncio
async def test_send_without_tg_settings_logs_and_skips_api(monkeypatch, caplog):
    from mpmt import notifier

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.read())
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(notifier.settings, "tg_bot_token", "")
    monkeypatch.setattr(notifier.settings, "tg_chat_id", "")
    monkeypatch.setattr(notifier, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with caplog.at_level(logging.WARNING, logger="mpmt.notifier"):
        await send("привет")

    assert calls == []               # TG not configured -> API never called
    assert "привет" in caplog.text   # fallback: the message goes to the log


@pytest.mark.asyncio
async def test_send_with_tg_settings_posts_once_and_never_raises(monkeypatch):
    from mpmt import notifier

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), json.loads(request.read())))
        return httpx.Response(500)   # TG unhappy: send() still must not raise

    monkeypatch.setattr(notifier.settings, "tg_bot_token", "123:TEST")
    monkeypatch.setattr(notifier.settings, "tg_chat_id", "42")
    monkeypatch.setattr(notifier, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    await send("алерт")

    assert len(calls) == 1
    url, body = calls[0]
    assert url == "https://api.telegram.org/bot123:TEST/sendMessage"
    assert body == {"chat_id": "42", "text": "алерт"}
