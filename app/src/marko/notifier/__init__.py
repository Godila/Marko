import logging

import httpx

from marko.settings import settings

log = logging.getLogger("marko.notifier")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)


async def send(text: str) -> None:
    """Fire a notification; used by pollers. Never raises."""
    try:
        if not settings.tg_bot_token or not settings.tg_chat_id:
            log.warning("ALERT (no tg): %s", text)
            return
        async with _client() as c:
            resp = await c.post(
                f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
                json={"chat_id": settings.tg_chat_id, "text": text},
            )
            if resp.status_code >= 400:
                # Do NOT use raise_for_status()/log.exception here:
                # httpx error messages embed the full URL (with bot token).
                log.error("tg sendMessage failed: HTTP %s (token redacted)", resp.status_code)
                return
    except Exception:
        log.exception("notify failed: %s", text)
