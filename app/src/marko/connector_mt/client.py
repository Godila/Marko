"""HTTP-клиент True API ГИС МТ (лёгпром, pg=lp).

Прод: v3 https://markirovka.crpt.ru/api/v3/true-api (auth, создание документов),
статусы — v4 https://markirovka.crpt.ru/api/v4/true-api.
Ретраи — как у WB: только 429/5xx, 1/4/16 c, максимум 3; 4xx — сразу ошибка.
"""
import base64
import json
import logging
import re
import time

import httpx

log = logging.getLogger("marko.mt")

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class MtHttpError(Exception):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f"MT {status}: {body[:300]}")


class MtClient:
    def __init__(self, base_v3: str, base_v4: str, pg: str = "lp",
                 transport=None, sleeper=time.sleep):
        self.v3, self.v4, self.pg = base_v3, base_v4, pg
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
                raise MtHttpError(r.status_code, r.text)
            return r

    # --- аутентификация (единый токен UUID) ---
    def auth_key(self) -> dict:
        r = self._req("GET", f"{self.v3}/auth/key",
                      headers={"Accept": "application/json"})
        return r.json()  # {"uuid": ..., "data": "<строка на подпись>"}

    def sign_in(self, uuid: str, signature_b64: str, inn: str) -> dict:
        r = self._req("POST", f"{self.v3}/auth/simpleSignIn",
                      headers={"Accept": "application/json",
                               "Content-Type": "application/json"},
                      json={"uuid": uuid, "data": signature_b64,
                            "inn": inn, "unitedToken": True})
        return r.json()  # {"token": ..., "uuidToken"?: ..., "expireDate"?: ...}

    # --- документы ---

    def create_doc_signed(self, token: str, doc_type: str,
                          product_document_b64: str, signature_b64: str) -> str:
        r = self._req("POST", f"{self.v3}/lk/documents/create",
                      params={"pg": self.pg},
                      headers={"Accept": "application/json",
                               "Content-Type": "application/json",
                               "Authorization": f"Bearer {token}"},
                      json={"document_format": "MANUAL",
                            "product_document": product_document_b64,
                            "type": doc_type,
                            "signature": signature_b64})
        # Прод (markirovka.crpt.ru) отдаёт 201 text/plain с ГОЛЫМ uuid без JSON
        # (live 2026-09-04); для совместимости принимаем и JSON {"uuid": ...}.
        try:
            data = r.json()
            if isinstance(data, dict) and data.get("uuid"):
                return data["uuid"]
        except ValueError:
            pass
        text = r.text.strip()
        if len(text) == 36 and _UUID_RE.fullmatch(text):
            return text
        raise MtHttpError(r.status_code, f"no uuid in response: {r.text[:200]!r}")

    def doc_info(self, token: str, doc_uuid: str) -> dict:
        r = self._req("GET", f"{self.v4}/doc/{doc_uuid}/info",
                      params={"pg": self.pg},
                      headers={"Accept": "application/json",
                               "Authorization": f"Bearer {token}"})
        # Прод отдаёт JSON-МАССИВ с объектом документа (live 2026-09-04);
        # dict — прежний формат песочницы/моков.
        data = r.json()
        if isinstance(data, list):
            return data[0] if data else {}
        return data
