"""HTTP-клиент Национального каталога (True API /nk/*).

Все ответы НК обёрнуты в {"apiversion": 3, "result": ...} — методы отдают
result. Ретраи — как у MtClient/WB: только 429/5xx, 1/4/16 c, максимум 3;
4xx — сразу ошибка.
"""
import time

import httpx


class NkHttpError(Exception):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f"NK {status}: {body[:300]}")


class NkClient:
    def __init__(self, base: str, transport=None, sleeper=time.sleep):
        self.base = base.rstrip("/")
        self.sleeper = sleeper
        self.http = httpx.Client(transport=transport, timeout=30)

    def _req(self, method, url, **kw):
        # обёртка для инъекции sleeper в тестах
        delays = [1, 4, 16]
        for attempt in range(4):
            r = self.http.request(method, url, **kw)
            if (r.status_code == 429 or r.status_code >= 500) and attempt < 3:
                self.sleeper(delays[attempt]); continue
            if r.status_code >= 400:
                raise NkHttpError(r.status_code, r.text)
            return r

    def _get(self, path, token, params=None):
        r = self._req("GET", f"{self.base}{path}", params=params,
                      headers={"Accept": "application/json",
                               "Authorization": f"Bearer {token}"})
        return r.json()

    def _post_json(self, path, token, json_body):
        r = self._req("POST", f"{self.base}{path}",
                      headers={"Accept": "application/json",
                               "Content-Type": "application/json",
                               "Authorization": f"Bearer {token}"},
                      json=json_body)
        return r.json()

    # --- справочники НК ---

    def attributes(self, token: str, tnved: str, attr_type: str | None = None) -> list[dict]:
        params = {"tnved": tnved}
        if attr_type is not None:
            params["attr_type"] = attr_type
        return self._get("/nk/attributes", token, params)["result"]

    def categories(self, token: str, tnved: str) -> list[dict]:
        return self._get("/nk/categories", token, {"tnved": tnved})["result"]

    def brands(self, token: str, name: str) -> list[dict]:
        return self._get("/nk/brands", token, {"name": name})["result"]

    def generate_gtins(self, token: str, quantity: int) -> dict:
        return self._get("/nk/generate-gtins", token, {"quantity": quantity})["result"]

    # --- публикация карточек (feed) ---

    def feed(self, token: str, entries: list[dict]) -> dict:
        j = self._post_json("/nk/feed", token, entries)
        res = j.get("result") or {}
        # feed_id может лежать в result.feed_id, result.id или быть самим result
        feed_id = res.get("feed_id", res.get("id")) if isinstance(res, dict) else res
        return {"feed_id": feed_id}

    def feed_status(self, token: str, feed_id) -> dict:
        return self._get("/nk/feed-status", token, {"feed_id": feed_id})["result"]

    def feed_product_document(self, token: str, gtins: list[str]) -> dict:
        # дамп trueapi: result задан как array (85410-85414), пример
        # 85586-85614 — "result":[ {xmls, errors} ]: разворачиваем список
        res = self._post_json("/nk/feed-product-document", token,
                              {"gtins": gtins, "publicationAgreement": False}).get("result")
        if isinstance(res, list):
            res = res[0] if res else {}
        return res or {}

    def feed_product_sign_pkcs(self, token: str, items: list[dict]) -> dict:
        # дамп типизирует result как number, но пример — объект {signed,
        # errors}; list-обёртку разворачиваем как в feed-product-document
        res = self._post_json("/nk/feed-product-sign-pkcs", token, items).get("result")
        if isinstance(res, list):
            res = res[0] if res else {}
        return res or {}
