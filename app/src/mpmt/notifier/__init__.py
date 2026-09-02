import logging

import httpx

from mpmt.settings import settings

log = logging.getLogger("mpmt.notifier")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)


async def send(text: str) -> None:
    """Fire a notification; used by pollers. Never raises."""
    try:
        if not settings.tg_bot_token or not settings.tg_chat_id:
            log.warning("ALERT (no tg): %s", text)
            return
        async with _client() as c:
            await c.post(
                f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
                json={"chat_id": settings.tg_chat_id, "text": text},
            )
    except Exception:
        log.exception("notify failed: %s", text)
