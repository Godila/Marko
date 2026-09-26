import pytest
from fastapi.testclient import TestClient
from marko.api.app import create_app
from marko.platform.models import PlatformPrincipal, PlatformToken, hash_token

AUTH = {"Authorization": "Bearer t1"}                 # read + nkmt:import + docs:submit
AUTH_RO = {"Authorization": "Bearer t-ro"}            # read-only


@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="user", name="owner"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t1"),
                         scopes="read,nkmt:import,docs:submit"))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t-ro"), scopes="read"))
    db.commit()
    return TestClient(create_app())


DECL = {"doc_number": "ЕАЭС №RU Д-RU.АБ12.В.12345", "doc_date": "2026-01-01"}


def test_declarations_crud(db, client):
    r = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL)
    assert r.status_code == 200 and r.json()["id"]
    assert r.json()["found"] is False   # ЧЗ офлайн в тестах — обогащение best-effort
    lst = client.get("/v1/nkmt/declarations", headers=AUTH_RO).json()
    row = lst[0]
    assert row["id"] == r.json()["id"] and row["status"] == "" and row["tnved_list"] == []
    dup = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL)
    assert dup.status_code == 409
    assert client.delete(f"/v1/nkmt/declarations/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.get("/v1/nkmt/declarations", headers=AUTH_RO).json() == []


def test_declarations_check_enriches(db, client, monkeypatch):
    """POST /declarations/{id}/check: rd/list → rich-поля; не найдена — found: 0."""
    from marko.nkmt.client import NkClient
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    monkeypatch.setattr(NkClient, "rd_list", lambda self, token, docs: {
        "documents": [{"type": "CONFORMITY_DECLARATION", "number": DECL["doc_number"],
                       "dateFrom": DECL["doc_date"], "dateTo": "2031-05-12",
                       "status": "Действует", "productName": "Головные уборы",
                       "productTnved": "6505003000, 6505009000",
                       "productTechRegulations": "ТР ТС 017/2011",
                       "applicantProductName": "ИП", "manufacturerProductName": "ИП"}],
        "errors": []})
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()
    out = client.post(f"/v1/nkmt/declarations/{d['id']}/check", headers=AUTH).json()
    assert out["found"] == 1 and out["declaration"]["status"] == "Действует"
    assert out["declaration"]["tnved_list"] == ["6505003000", "6505009000"]
    assert out["declaration"]["date_to"] == "2031-05-12"
    assert out["declaration"]["checked_at"]
    # check-all по всему реестру — тот же enriched-ответ
    allout = client.post("/v1/nkmt/declarations/check-all", headers=AUTH).json()
    assert allout["checked"] == 1 and allout["found"] == 1


def test_declarations_check_all_empty_409(db, client):
    assert client.post("/v1/nkmt/declarations/check-all", headers=AUTH).status_code == 409


def test_producers_crud(db, client):
    r = client.post("/v1/nkmt/producers", headers=AUTH, json={
        "name": "ИП Байкулов Д. А.", "inn": "090201471350",
        "kind": "entrepreneur", "note": "осн. производитель"})
    assert r.status_code == 200 and r.json()["id"]
    lst = client.get("/v1/nkmt/producers", headers=AUTH_RO).json()
    assert lst == [{"id": r.json()["id"], "name": "ИП Байкулов Д. А.",
                    "inn": "090201471350", "kind": "entrepreneur",
                    "note": "осн. производитель"}]
    # дубль имени без учёта регистра — 409; битый ИНН — 400; пустое имя — 400
    assert client.post("/v1/nkmt/producers", headers=AUTH,
                       json={"name": "ип байкулов д. а."}).status_code == 409
    assert client.post("/v1/nkmt/producers", headers=AUTH,
                       json={"name": "X", "inn": "123"}).status_code == 400
    assert client.post("/v1/nkmt/producers", headers=AUTH,
                       json={"name": "  "}).status_code == 400
    assert client.delete(f"/v1/nkmt/producers/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.get("/v1/nkmt/producers", headers=AUTH_RO).json() == []
    assert client.delete("/v1/nkmt/producers/999", headers=AUTH).status_code == 404


def test_defaults_get_put(db, client):
    r = client.get("/v1/nkmt/defaults", headers=AUTH_RO).json()
    assert r["brand"] == "YCPB"
    assert client.put("/v1/nkmt/defaults", headers=AUTH,
                      json={**r, "brand": "ADEL"}).json() == {"ok": True}
    assert client.get("/v1/nkmt/defaults", headers=AUTH_RO).json()["brand"] == "ADEL"


def test_dicts_attributes_validates_tnved(db, client, monkeypatch):
    r = client.get("/v1/nkmt/dicts/attributes?tnved=6109", headers=AUTH_RO)
    assert r.status_code == 400


def test_dicts_hints(db, client):
    """Подсказки для правил: пресеты вида товара, размерные системы и пол из
    кэша атрибутных моделей + бренды и производители из справочников."""
    from marko.nkmt.models import BrandCache, Producer
    from marko.platform.models import PlatformKV
    db.add(PlatformKV(key="nk_attrs:6109100000", value={"m": [
        {"attr_id": 12, "attr_name": "Вид товара", "attr_preset": ["ФУТБОЛКА", "ШАПКА"]},
        {"attr_id": 35, "attr_name": "Размер", "attr_preset": [],
         "attr_value_type": ["МЕЖДУНАРОДНЫЙ", "ЕВРОПЕЙСКИЙ"]},
        {"attr_id": 14013, "attr_name": "Целевой пол",
         "attr_preset": ["ЖЕНСКИЙ", "МУЖСКОЙ"]}], "r": []}))
    db.add(BrandCache(name="ycpb", brand_id=2102811))
    db.add(Producer(name="ИП Байкулов Д. А.", inn="090201471350", kind="entrepreneur"))
    db.commit()
    r = client.get("/v1/nkmt/dicts/hints", headers=AUTH_RO)
    assert r.status_code == 200
    hints = r.json()
    assert hints["product_types"] == ["ФУТБОЛКА", "ШАПКА"]
    # casefold-дедуп: кэш «ycpb» + дефолт «YCPB» → один вариант с дефолтным написанием
    assert hints["brands"] == ["YCPB"]
    assert hints["size_systems"] == ["ЕВРОПЕЙСКИЙ", "МЕЖДУНАРОДНЫЙ"]
    assert hints["genders"] == ["ЖЕНСКИЙ", "МУЖСКОЙ"]
    assert hints["producers"] == ["ИП Байкулов Д. А."]


def test_rules_crud_and_declaration_guard(db, client):
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    r = client.post("/v1/nkmt/rules", headers=AUTH,
                    json={"brand": "YCPB", "product_types": ["ФУТБОЛКА", "ШАПКА"],
                          "declaration_id": d, "producer": "ИП Байкулов"})
    assert r.status_code == 200 and r.json()["id"]
    lst = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()
    assert lst == [{"id": r.json()["id"], "brand": "YCPB",
                    "product_types": ["ФУТБОЛКА", "ШАПКА"],
                    "declaration_id": d, "declaration_number": DECL["doc_number"],
                    "declaration_date": DECL["doc_date"], "declaration_title": "",
                    "producer": "ИП Байкулов", "fields": {}}]
    # правило без условия — 400; неизвестная декларация — 404; дубль условия — 409
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"declaration_id": d}).status_code == 400
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "X", "declaration_id": 99999}).status_code == 404
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "YCPB", "product_types": ["ФУТБОЛКА", "ШАПКА"],
                             "declaration_id": d}).status_code == 409
    # матчится casefold → дубль условия в другом регистре тоже 409
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "ycpb", "product_types": ["футболка", "Шапка"],
                             "declaration_id": d}).status_code == 409
    # дубль в другом порядке видов — то же множество (семантика match_rule);
    # кириллический бренд фолдится в Python, а не lower() локали БД
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "Адель", "product_types": ["ШАПКА", "КЕПКА"],
                             "declaration_id": d}).status_code == 200
    adel = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()[-1]["id"]
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "АДЕЛЬ", "product_types": ["кепка", "шапка"],
                             "declaration_id": d}).status_code == 409
    assert client.delete(f"/v1/nkmt/rules/{adel}", headers=AUTH).json() == {"ok": True}
    # пустые виды в списке выбрасываются: ["  ", ""] → [] → без условия → 400
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "", "product_types": ["  ", ""],
                             "declaration_id": d}).status_code == 400
    # декларация под правилом не удаляется; после удаления правила — удаляется
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).status_code == 409
    assert client.delete(f"/v1/nkmt/rules/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).json() == {"ok": True}
    assert client.delete("/v1/nkmt/rules/99999", headers=AUTH).status_code == 404


def test_rules_fields_crud(db, client):
    """fields правила: whitelist-ключи сохраняются и возвращаются, неизвестное
    поле — 400, пустые значения нормализуются в отсутствие ключа."""
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    r = client.post("/v1/nkmt/rules", headers=AUTH,
                    json={"product_types": ["ШАПКА"], "declaration_id": d,
                          "fields": {"size": "ONE SIZE", "color": "  "}})
    assert r.status_code == 200
    lst = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()
    assert lst[0]["fields"] == {"size": "ONE SIZE"}
    bad = client.post("/v1/nkmt/rules", headers=AUTH,
                      json={"product_types": ["КЕПКА"], "declaration_id": d,
                            "fields": {"article": "X"}})
    assert bad.status_code == 400 and "article" in bad.json()["detail"]


def test_rules_types_casefold_dedup(db, client):
    """Виды товара дедуплицируются по casefold при записи: «ШАПКА, шапка» —
    один вид с первым написанием (роут открыт API-агентам, UI так не шлёт)."""
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    r = client.post("/v1/nkmt/rules", headers=AUTH,
                    json={"product_types": ["ШАПКА", "шапка", " КЕПКА "],
                          "declaration_id": d})
    assert r.status_code == 200
    lst = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()
    assert lst[0]["product_types"] == ["ШАПКА", "КЕПКА"]


def test_declarations_update(db, client):
    """PUT /declarations/{id}: полная замена ядровых полей; дубль «кроме себя»,
    валидации и ошибки — те же, что у POST."""
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    out = client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH,
                     json={**DECL, "doc_type": "certificate", "title": "Шапки"})
    assert out.status_code == 200 and out.json()["id"] == d
    row = client.get("/v1/nkmt/declarations", headers=AUTH_RO).json()[0]
    assert row["doc_type"] == "certificate" and row["title"] == "Шапки"
    # собственная пара — не дубль сам с собой (retitle без смены номера/даты)
    assert client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH,
                      json={**DECL, "title": "Шапки 2"}).status_code == 200
    d2 = client.post("/v1/nkmt/declarations", headers=AUTH,
                     json={**DECL, "doc_date": "2026-02-02"}).json()["id"]
    assert client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH,
                      json={**DECL, "doc_date": "2026-02-02"}).status_code == 409
    assert client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH,
                      json={**DECL, "doc_number": "  "}).status_code == 400
    assert client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH,
                      json={**DECL, "doc_date": "01.01.2026"}).status_code == 400
    assert client.put("/v1/nkmt/declarations/99999", headers=AUTH,
                      json=DECL).status_code == 404
    assert client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH_RO,
                      json=DECL).status_code == 403


def test_declarations_update_resets_rich_fields(db, client, monkeypatch):
    """Смена пары (номер/дата) сбрасывает rich-поля ЧЗ и пере-обогащает
    (best-effort по cached-токену); правка title/типа — не сбрасывает."""
    from datetime import datetime, timedelta
    from marko.nkmt.client import NkClient
    from marko.platform.models import PlatformKV

    def echo_rd_list(self, token, docs):
        return {"documents": [
            {"type": doc["type"], "number": doc["number"], "dateFrom": doc["dateFrom"],
             "status": "Действует", "dateTo": "2031-05-12", "productName": "Головные уборы",
             "productTnved": "6505003000, 6505009000",
             "productTechRegulations": "ТР ТС 017/2011",
             "applicantProductName": "ИП", "manufacturerProductName": "ИП"}
            for doc in docs], "errors": []}
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    monkeypatch.setattr(NkClient, "rd_list", echo_rd_list)

    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    assert client.post(f"/v1/nkmt/declarations/{d}/check", headers=AUTH).json()["found"] == 1
    # смена пары без cached-токена: сброс + обогащение пропущено (found False)
    new_pair = {**DECL, "doc_date": "2026-03-03"}
    out = client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH, json=new_pair).json()
    assert out["found"] is False
    assert out["declaration"]["status"] == "" and out["declaration"]["tnved_list"] == []
    assert out["declaration"]["checked_at"] is None
    # правка title/типа: rich-поля сохраняются
    assert client.post(f"/v1/nkmt/declarations/{d}/check", headers=AUTH).json()["found"] == 1
    out2 = client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH,
                      json={**new_pair, "title": "Шапки"}).json()
    assert out2["found"] is True and out2["declaration"]["status"] == "Действует"
    # смена пары при живом cached-токене — пере-обогащение сразу из ответа PUT
    db.add(PlatformKV(key="mt_token", value={
        "token": "T", "expires": (datetime.utcnow() + timedelta(hours=1)).isoformat()}))
    db.commit()
    out3 = client.put(f"/v1/nkmt/declarations/{d}", headers=AUTH,
                      json={**new_pair, "doc_date": "2026-04-04"}).json()
    assert out3["found"] is True and out3["declaration"]["status"] == "Действует"
    assert out3["declaration"]["tnved_list"] == ["6505003000", "6505009000"]


def test_producers_update(db, client):
    """PUT /producers/{id}: rename/ИНН/тип/примечание; casefold-дубль «кроме себя»."""
    r = client.post("/v1/nkmt/producers", headers=AUTH, json={
        "name": "ИП Байкулов Д. А.", "inn": "090201471350",
        "kind": "entrepreneur"}).json()
    out = client.put(f"/v1/nkmt/producers/{r['id']}", headers=AUTH,
                     json={"name": "ИП Байкулов Д. А. (осн.)", "inn": "090201471359",
                           "kind": "company", "note": "правка"})
    assert out.status_code == 200 and out.json()["id"] == r["id"]
    lst = client.get("/v1/nkmt/producers", headers=AUTH_RO).json()
    assert lst[0]["name"] == "ИП Байкулов Д. А. (осн.)" and lst[0]["kind"] == "company"
    assert lst[0]["note"] == "правка"
    # собственный casefold-вариант — не дубль сам с собой
    assert client.put(f"/v1/nkmt/producers/{r['id']}", headers=AUTH,
                      json={"name": "ип байкулов д. а. (осн.)"}).status_code == 200
    r2 = client.post("/v1/nkmt/producers", headers=AUTH,
                     json={"name": "ООО Рога"}).json()
    assert client.put(f"/v1/nkmt/producers/{r['id']}", headers=AUTH,
                      json={"name": "ооо рога"}).status_code == 409
    assert client.put(f"/v1/nkmt/producers/{r['id']}", headers=AUTH,
                      json={"name": "X", "inn": "123"}).status_code == 400
    assert client.put(f"/v1/nkmt/producers/{r['id']}", headers=AUTH,
                      json={"name": "  "}).status_code == 400
    assert client.put("/v1/nkmt/producers/999", headers=AUTH,
                      json={"name": "Y"}).status_code == 404
    assert client.put(f"/v1/nkmt/producers/{r['id']}", headers=AUTH_RO,
                      json={"name": "Y"}).status_code == 403


def test_rules_update(db, client):
    """PUT /rules/{id}: смена условия/декларации/подстановок; дубль условия
    «кроме себя», валидации POST, FK RESTRICT жив после правки."""
    d1 = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    d2 = client.post("/v1/nkmt/declarations", headers=AUTH,
                     json={**DECL, "doc_date": "2026-02-02"}).json()["id"]
    r = client.post("/v1/nkmt/rules", headers=AUTH,
                    json={"brand": "YCPB", "product_types": ["ШАПКА"],
                          "declaration_id": d1, "producer": "ИП Байкулов"}).json()["id"]
    out = client.put(f"/v1/nkmt/rules/{r}", headers=AUTH,
                     json={"brand": "YCPB", "product_types": ["ШАПКА", "КЕПКА"],
                           "declaration_id": d2, "producer": "ООО Рога",
                           "fields": {"size": "ONE SIZE"}})
    assert out.status_code == 200 and out.json()["id"] == r
    lst = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()
    assert lst[0]["product_types"] == ["ШАПКА", "КЕПКА"]
    assert lst[0]["declaration_id"] == d2 and lst[0]["declaration_number"] == \
        client.get("/v1/nkmt/declarations", headers=AUTH_RO).json()[1]["doc_number"]
    assert lst[0]["fields"] == {"size": "ONE SIZE"}
    # собственное условие в другом регистре — не дубль сам с собой
    assert client.put(f"/v1/nkmt/rules/{r}", headers=AUTH,
                      json={"brand": "ycpb", "product_types": ["шапка", "КЕПКА"],
                            "declaration_id": d1}).status_code == 200
    r2 = client.post("/v1/nkmt/rules", headers=AUTH,
                     json={"brand": "Адель", "declaration_id": d1}).json()["id"]
    assert client.put(f"/v1/nkmt/rules/{r}", headers=AUTH,
                      json={"brand": "АДЕЛЬ", "declaration_id": d1}).status_code == 409
    assert client.put(f"/v1/nkmt/rules/{r}", headers=AUTH,
                      json={"declaration_id": d1}).status_code == 400
    assert client.put(f"/v1/nkmt/rules/{r}", headers=AUTH,
                      json={"brand": "X", "declaration_id": 99999}).status_code == 404
    assert client.put(f"/v1/nkmt/rules/{r}", headers=AUTH,
                      json={"brand": "X", "declaration_id": d1,
                            "fields": {"article": "N"}}).status_code == 400
    assert client.put("/v1/nkmt/rules/99999", headers=AUTH,
                      json={"brand": "X", "declaration_id": d1}).status_code == 404
    assert client.put(f"/v1/nkmt/rules/{r}", headers=AUTH_RO,
                      json={"brand": "X", "declaration_id": d1}).status_code == 403
    assert client.delete(f"/v1/nkmt/declarations/{d1}", headers=AUTH).status_code == 409


def test_agent_context_lists_update_endpoints(db, client):
    """Карта эндпоинтов контекста НК знает про PUT-правку справочников."""
    eps = client.get("/v1/nkmt/context", headers=AUTH_RO).json()["endpoints"]
    for key in ("declarations_update", "producers_update", "rules_update"):
        assert key in eps and "PUT" in eps[key]


def test_resolve_endpoint(db, client):
    from marko.nkmt.models import Rule
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    db.add(Rule(brand="YCPB", product_types=["ФУТБОЛКА", "ШАПКА"],
                declaration_id=d, producer=""))
    db.commit()
    # правило матчится по любому виду из списка; бренд «введён» (как файловый)
    r = client.post("/v1/nkmt/resolve", headers=AUTH_RO,
                    json={"brand": "YCPB", "product_type": "ШАПКА"})
    assert r.status_code == 200
    keys = r.json()
    assert keys["declaration_number"] == {"value": DECL["doc_number"], "src": "rule"}
    assert keys["brand"] == {"value": "YCPB", "src": "file"}
    assert keys["techreg"]["src"] == "default" and keys["techreg"]["value"]
    # вид вне списка → декларация остаётся дефолтной (пустой — kv не задан)
    r2 = client.post("/v1/nkmt/resolve", headers=AUTH_RO,
                     json={"brand": "YCPB", "product_type": "КЕПКА"}).json()
    assert r2["declaration_number"] == {"value": "", "src": "default"}
