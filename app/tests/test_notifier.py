import json
import logging

import httpx
import pytest

from marko.notifier import send


@pytest.mark.asyncio
async def test_send_without_tg_settings_logs_and_skips_api(monkeypatch, caplog):
    from marko import notifier

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.read())
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(notifier.settings, "tg_bot_token", "")
    monkeypatch.setattr(notifier.settings, "tg_chat_id", "")
    monkeypatch.setattr(notifier, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with caplog.at_level(logging.WARNING, logger="marko.notifier"):
        await send("привет")

    assert calls == []               # TG not configured -> API never called
    assert "привет" in caplog.text   # fallback: the message goes to the log


@pytest.mark.asyncio
async def test_send_with_tg_settings_posts_once_and_never_raises(monkeypatch, caplog):
    from marko import notifier

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), json.loads(request.read())))
        return httpx.Response(500)   # TG unhappy: send() still must not raise

    monkeypatch.setattr(notifier.settings, "tg_bot_token", "123:TEST")
    monkeypatch.setattr(notifier.settings, "tg_chat_id", "42")
    monkeypatch.setattr(notifier, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with caplog.at_level(logging.ERROR, logger="marko.notifier"):
        await send("алерт")

    assert len(calls) == 1
    url, body = calls[0]
    assert url == "https://api.telegram.org/bot123:TEST/sendMessage"
    assert body == {"chat_id": "42", "text": "алерт"}
    assert "tg sendMessage failed: HTTP 500" in caplog.text


@pytest.mark.asyncio
async def test_send_tg_http_failure_logged_without_token_or_traceback(monkeypatch, caplog):
    from marko import notifier

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(500)

    monkeypatch.setattr(notifier.settings, "tg_bot_token", "123:TEST")
    monkeypatch.setattr(notifier.settings, "tg_chat_id", "42")
    monkeypatch.setattr(notifier, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with caplog.at_level(logging.ERROR, logger="marko.notifier"):
        await send("алерт")   # must not raise

    assert len(calls) == 1
    assert "tg sendMessage failed: HTTP 500" in caplog.text
    assert "123:TEST" not in caplog.text                    # bot token must never be logged
    assert not any(r.exc_info for r in caplog.records)      # HTTP errors: no exception/traceback path
