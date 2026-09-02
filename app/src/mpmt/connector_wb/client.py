import logging
import time

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mpmt.platform.models import PlatformKV

log = logging.getLogger("mpmt.wb")


class WbHttpError(Exception):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f"WB {status}: {body[:200]}")


class WbLimitError(Exception):
    pass


class WBClient:
    # orders живут на отдельном хосте относительно analytics-методов
    orders_base = "https://marketplace-api.wildberries.ru"

    def __init__(self, token: str, base: str = "https://seller-analytics-api.wildberries.ru",
                 transport=None, sleeper=time.sleep, db: Session | None = None):
        self.token, self.base, self.sleeper, self.db = token, base, sleeper, db
        self.http = httpx.Client(transport=transport, timeout=30)

    def _headers(self):
        # WB-токен идёт как есть, без Bearer
        return {"Authorization": self.token, "Accept": "application/json"}

    def _fetch(self, method, url, *, params=None, json_body=None, retries=3) -> httpx.Response:
        delays = [1, 4, 16]
        for attempt in range(retries + 1):
            r = self.http.request(method, url, params=params,
                                  json=json_body, headers=self._headers())
            if (r.status_code == 429 or r.status_code >= 500) and attempt < retries:
                wait = delays[min(attempt, len(delays) - 1)]
                if "Retry-After" in r.headers:
                    wait = min(float(r.headers["Retry-After"]), 60.0)
                log.warning("wb retry %s %s -> %s, wait %ss", method, url, r.status_code, wait)
                self.sleeper(wait)
                continue
            if r.status_code >= 400:
                raise WbHttpError(r.status_code, r.text)
            return r

    def request(self, method, path, *, params=None, json_body=None,
                retries=3) -> httpx.Response:
        return self._fetch(method, self.base + path, params=params,
                           json_body=json_body, retries=retries)

    # ---- excise: лимит 2 запроса/24ч на базовом токене ----
    def _gate_excise(self):
        if self.db is None:
            return
        kv = self.db.get(PlatformKV, "wb_excise_usage")
        now = time.time()
        # окно чуть короче 24ч: cron-слоты ровно в 12ч, джиттер исполнения
        # не должен оставлять вчерашний штамп в окне и ложно блокировать поллинг
        stamps = [t for t in (kv.value["stamps"] if kv else []) if now - t < 86400 - 120]
        if len(stamps) >= 2:
            raise WbLimitError("excise 2/24h limit reached")
        self.db.execute(pg_insert(PlatformKV).values(
            key="wb_excise_usage", value={"stamps": stamps + [now]},
        ).on_conflict_do_update(
            index_elements=[PlatformKV.key],
            set_={"value": {"stamps": stamps + [now]}},
        ))
        self.db.commit()

    def excise_report(self, date_from: str, date_to: str) -> list[dict]:
        self._gate_excise()
        r = self.request("POST", "/api/v1/analytics/excise-report",
                         params={"dateFrom": date_from, "dateTo": date_to},
                         json_body={"countries": ["RU"]})
        return r.json()["response"]["data"]

    def orders(self, limit: int = 1000) -> list[dict]:
        out: list[dict] = []
        cursor = 0
        for _ in range(1000):  # жёсткий потолок страниц — страховка от зацикливания
            r = self._fetch("GET", self.orders_base + "/api/v3/orders",
                            params={"next": cursor, "limit": limit})
            data = r.json()
            batch = data.get("orders", [])
            out.extend(batch)
            nxt = data.get("next", 0)
            if not nxt or not batch or nxt == cursor:
                break  # next=0/None — документированный конец WB; пустая партия; повтор курсора
            cursor = nxt
        else:
            log.error("orders pagination cap hit")
        return out


def load_wb_token(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()
