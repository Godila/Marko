# -*- coding: utf-8 -*-
"""
Полный сценарий песочницы WB API (FBS): от карточки до выкупа.

Требует ТЕСТОВЫЙ токен в secrets/WBtoken-test.txt (создаёт владелец ЛК WB Партнёры:
Интеграции по API -> + Создать токен -> «Для интеграции вручную» -> «Тестовый токен»).

Шаги:
  1  ping marketplace-sandbox
  2  склады продавца (warehouseId)
  3  карточки (content-sandbox): nmID, chrtID, баркод
  4  цены (discounts-sandbox): проверка/установка цены != 0
  5  остатки на FBS-складе (chrtId, amount) — с мая 2026 sku отключён
  6  make: тестовые заказы-«покупатели» (2 шт: один на выкуп, один на отказ)
  7  orders/new: забрать orderId/rid
  8  PUT meta/sgtin: закрепить полный тестовый КМ (с GS и криптохвостом)
  9  поставка: create -> add orders -> deliver
 10  эмуляция WB: receive (выкуп) для первого, reject (отказ в ПВЗ) для второго
 11  orders/status: wbStatus (sold / canceled_by_client)
 12  orders/meta: sgtin + decision
 13  statistics-sandbox sales: строка продажи, srid == rid

Все ответы пишутся в wb-specs/fixtures/sandbox/. Лимит песочницы 1 rps — выдерживается.
Запуск: python scripts/sandbox_scenario.py [--skip-price] [--skip-stocks]
"""
import base64
import json
import random
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "secrets" / "WBtoken-test.txt"
OUT = ROOT / "wb-specs" / "fixtures" / "sandbox"
OUT.mkdir(parents=True, exist_ok=True)

MARKET = "https://marketplace-api-sandbox.wildberries.ru"
CONTENT = "https://content-api-sandbox.wildberries.ru"
PRICES = "https://discounts-prices-api-sandbox.wildberries.ru"
STATS = "https://statistics-api-sandbox.wildberries.ru"

PAUSE = 1.2  # песочница: максимум 1 запрос/с суммарно на все методы Маркетплейса


def req(method, base, path, body=None, query=""):
    url = base + path + query
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def once():
        r = urllib.request.Request(url, method=method)
        r.add_header("Authorization", read_token())
        r.add_header("Accept", "application/json")
        if data is not None:
            r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, data, timeout=40) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw[:400]
            return e.code, parsed

    st, payload = once()
    if st == 429:  # в песочнице 4XX считается за 10 запросов — даём остыть
        time.sleep(6)
        st, payload = once()
    if st >= 400:
        time.sleep(4)
    return st, payload


def read_token():
    if not TOKEN_FILE.exists():
        sys.exit(f"Нужен тестовый токен в {TOKEN_FILE}")
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def save(name, status, payload):
    p = OUT / f"{name}.json"
    p.write_text(json.dumps({"_http": status, "payload": payload},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"    -> сохранено {p.name}")


def step(n, title):
    print(f"\n[{n}] {title}")


def fail(status, payload):
    print(f"    !! HTTP {status}: {payload}")
    return status, payload


def gen_test_km(gtin="04630520676025"):
    """Полный тестовый КМ лёгпрома: 01+GTIN(14)+21+serial(13) GS 91+4 GS 92+44."""
    serial = "".join(random.choices(string.ascii_letters + string.digits, k=13))
    k91 = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    k92 = "".join(random.choices(string.ascii_letters + string.digits, k=43)) + "="
    gs = "\u001d"
    return f"01{gtin}21{serial}{gs}91{k91}{gs}92{k92}"


def main(skip_price=False, skip_stocks=False):
    # 1. ping
    step(1, "ping marketplace-sandbox")
    st, body = req("GET", MARKET, "/ping")
    print("   ", st, body)
    if st != 200:
        sys.exit("ping не прошёл — проверь токен (нужен именно ТЕСТОВЫЙ)")

    # 2. склады (создадим, если пусто)
    step(2, "склады продавца")
    st, whs = req("GET", MARKET, "/api/v3/warehouses")
    save("02-warehouses", st, whs)
    if st != 200:
        sys.exit(f"не удалось получить склады: {whs}")
    wh = whs[0] if whs else None
    if wh is None:
        print("    складов нет — создаём")
        time.sleep(PAUSE)
        st, offices = req("GET", MARKET, "/api/v3/offices")
        save("02-offices", st, offices)
        if st != 200 or not offices:
            sys.exit(f"нет списка офисов WB: {offices}")
        office_id = offices[0]["id"]
        print(f"    офис WB: id={office_id} ({offices[0].get('name')})")
        time.sleep(PAUSE)
        st, created = req("POST", MARKET, "/api/v3/warehouses",
                          {"name": "GISMT-TEST", "officeId": office_id})
        save("02-warehouse-create", st, created)
        if st not in (200, 201):
            fail(st, created)
            sys.exit("не удалось создать склад")
        time.sleep(PAUSE)
        st, whs = req("GET", MARKET, "/api/v3/warehouses")
        wh = whs[0] if whs else None
    if wh is None:
        sys.exit("склады недоступны")
    print(f"    склад: {wh['name']} (id={wh['id']})")

    # 3. карточки (создадим, если пусто; формат v2: subjectID + variants)
    step(3, "карточки (content-sandbox)")
    st, cards = req("POST", CONTENT, "/content/v2/get/cards/list",
                    {"settings": {"filter": {}, "cursor": {}, "limit": 10}})
    card = None
    if st == 200 and cards:
        for c in cards.get("cards", []):
            for s in c.get("sizes", []):
                if s.get("skus"):
                    card = {"nmID": c["nmID"], "chrtID": s["chrtID"],
                            "barcode": s["skus"][0], "vendorCode": c.get("vendorCode")}
                    break
            if card:
                break
    if card is None:
        print("    карточек нет — создаём (Пончо для малышей, kizMarked=true)")
        time.sleep(PAUSE)
        st, subs = req("GET", CONTENT, "/content/v2/object/all")
        subject_id = 4636
        if st == 200 and subs:
            for s in subs.get("data", subs):
                if s.get("subjectName") == "Пончо для малышей":
                    subject_id = s["subjectID"]
                    break
        barcode = "2030000123452"
        time.sleep(PAUSE)
        st, up = req("POST", CONTENT, "/content/v2/cards/upload", [{
            "subjectID": subject_id,
            "variants": [{
                "vendorCode": "GISMT-TEST-001",
                "brand": "GISMT",
                "title": "Пончо тест ГИСМТ",
                "description": "Тестовая карточка для сценария песочницы",
                "kizMarked": True,
                "sizes": [{"techSize": "92", "price": 1500, "skus": [barcode]}],
                "characteristics": [{"id": 54337, "value": "92"},
                                     {"id": 18769, "value": "демисезон"}],
            }],
        }])
        save("03-card-upload", st, up)
        if st != 200:
            fail(st, up)
            sys.exit("не удалось создать карточку")
        time.sleep(PAUSE * 3)  # карточка появляется не мгновенно
        st, cards = req("POST", CONTENT, "/content/v2/get/cards/list",
                        {"settings": {"filter": {}, "cursor": {}, "limit": 10}})
        if st == 200 and cards:
            for c in cards.get("cards", []):
                for s in c.get("sizes", []):
                    if s.get("skus"):
                        card = {"nmID": c["nmID"], "chrtID": s["chrtID"],
                                "barcode": s["skus"][0],
                                "vendorCode": c.get("vendorCode")}
                        break
                if card:
                    break
    if not card:
        sys.exit("карточки недоступны")
    save("03-cards", st, cards)
    print(f"    карточка: nmID={card['nmID']} chrtID={card['chrtID']} "
          f"barcode={card['barcode']} ({card['vendorCode']})")

    # 4. цена
    if not skip_price:
        step(4, "цена (discounts-sandbox)")
        st, goods = req("POST", PRICES, "/api/v2/list/goods/filter",
                        {"sort": {"cursor": {"limit": 100, "nmID": 0, "offset": 0},
                                  "filter": {}, "sortColumns": {}}})
        save("04-prices", st, goods)
        price_ok = False
        if st == 200 and goods:
            for g in goods.get("data", {}).get("listGoods", []):
                if g.get("nmID") == card["nmID"]:
                    sz = (g.get("sizes") or [{}])[0]
                    price_ok = bool(sz.get("price") or sz.get("discountedPrice"))
                    break
        if price_ok:
            print("    цена уже стоит")
        else:
            time.sleep(PAUSE)
            st, up = req("POST", PRICES, "/api/v2/upload/task",
                         [{"nmID": card["nmID"], "price": 1500, "discount": 0}])
            print("    upload/task price:", st, up)

    # 5. остатки
    if not skip_stocks:
        step(5, f"остатки на складе {wh['id']} (PUT, chrtId)")
        st, up = req("PUT", MARKET, f"/api/v3/stocks/{wh['id']}",
                     {"stocks": [{"chrtId": card["chrtID"], "amount": 10}]})
        save("05-stocks", st, up)
        print("    ", st, json.dumps(up, ensure_ascii=False)[:200] if up else "(нет тела — ок для 204)")

    # 6-7. два независимых тестовых заказа (одинаковые позиции в одной корзине
    #      схлопываются в одно задание, поэтому два отдельных make)
    def fetch_new():
        st, new = req("GET", MARKET, "/api/v3/orders/new", query="?next=0")
        if st == 200 and new:
            return [o for o in new.get("orders", []) if o.get("price")]
        return []

    step(6, "make: два тестовых заказа (раздельно)")
    known = set(o["id"] for o in fetch_new())
    st, body = req("POST", MARKET, "/api/v3/test/fbs/orders/make",
                   {"orders": [{"sku": card["barcode"], "amount": 1}]})
    if st not in (200, 204):
        fail(st, body)
        sys.exit("make #1 не прошёл")
    time.sleep(PAUSE * 2)
    got = [o for o in fetch_new() if o["id"] not in known]
    if not got:
        sys.exit("make #1: новое задание не появилось")
    o_sold = got[0]
    print(f"    sold-задание: id={o_sold['id']} rid={o_sold.get('rid')} "
          f"price={o_sold.get('price')}")

    time.sleep(PAUSE)
    known = set(o["id"] for o in fetch_new())
    st, body = req("POST", MARKET, "/api/v3/test/fbs/orders/make",
                   {"orders": [{"sku": card["barcode"], "amount": 1}]})
    if st not in (200, 204):
        fail(st, body)
        sys.exit("make #2 не прошёл")
    time.sleep(PAUSE * 2)
    got = [o for o in fetch_new() if o["id"] not in known]
    if not got:
        sys.exit("make #2: новое задание не появилось")
    o_rej = got[0]
    print(f"    reject-задание: id={o_rej['id']} rid={o_rej.get('rid')}")

    step(7, "orders/new снят")
    st, new = req("GET", MARKET, "/api/v3/orders/new", query="?next=0")
    save("07-orders-new", st, new)

    # 8-9. поставка: create -> add orders (статус confirm) -> sgtin -> deliver
    step(9, "поставка: create -> add orders (confirm) -> sgtin -> deliver")
    st, sup = req("POST", MARKET, "/api/v3/supplies", {"name": "GISMT-TEST-002"})
    save("09-supply-create", st, sup)
    if st not in (200, 201) or not (sup or {}).get("id"):
        fail(st, sup)
        sys.exit("не удалось создать поставку")
    supply_id = sup["id"]
    print("    поставка:", supply_id)
    time.sleep(PAUSE)
    st, body = req("PATCH", MARKET,
                   f"/api/marketplace/v3/supplies/{supply_id}/orders",
                   {"orders": [o_sold["id"], o_rej["id"]]})
    print("    add orders:", st, body)
    if st not in (200, 204):
        sys.exit("не удалось добавить задания к поставке")

    # sgtin закрепляется только у задания в статусе confirm (на сборке)
    step(8, "закрепить тестовый КМ за заданием (статус confirm)")
    km = gen_test_km()
    save("08-test-km", 0, {"orderId": o_sold["id"], "km": km})
    time.sleep(PAUSE)
    st, body = req("PUT", MARKET, f"/api/v3/orders/{o_sold['id']}/meta/sgtin",
                   {"sgtins": [km]})
    print("    PUT meta/sgtin:", st, body)

    time.sleep(PAUSE)
    st, body = req("PATCH", MARKET, f"/api/v3/supplies/{supply_id}/deliver")
    print("    deliver:", st, body)
    time.sleep(PAUSE)
    st, body = req("PATCH", MARKET, f"/api/v3/test/fbs/supplies/{supply_id}/close")
    print("    close supply (sorted):", st, body)

    # 10. эмуляция WB: сначала "поступил в ПВЗ" (ready_for_pickup), затем выкуп/отказ
    step(10, "эмуляция WB: deliver→ПВЗ, receive (выкуп), reject (отказ)")
    st, body = req("PATCH", MARKET, f"/api/v3/test/fbs/orders/{o_sold['id']}/deliver")
    print("    [sold] test deliver:", st, body)
    time.sleep(PAUSE)
    st, body = req("PATCH", MARKET, f"/api/v3/test/fbs/orders/{o_sold['id']}/receive")
    print("    [sold] receive:", st, body)
    time.sleep(PAUSE)
    st, body = req("PATCH", MARKET, f"/api/v3/test/fbs/orders/{o_rej['id']}/deliver")
    print("    [rej] test deliver:", st, body)
    time.sleep(PAUSE)
    st, body = req("PATCH", MARKET, f"/api/v3/test/fbs/orders/{o_rej['id']}/reject")
    print("    [rej] reject:", st, body)

    # 11. статусы
    step(11, "orders/status")
    st, stat = req("POST", MARKET, "/api/v3/orders/status",
                   {"orders": [o_sold["id"], o_rej["id"]]})
    save("11-orders-status", st, stat)
    if st == 200 and stat:
        for o in stat.get("orders", []):
            print(f"    id={o['id']} supplier={o.get('supplierStatus')} "
                  f"wb={o.get('wbStatus')}")

    # 12. meta
    step(12, "orders/meta (sgtin + decision)")
    st, meta = req("POST", MARKET, "/api/marketplace/v3/orders/meta",
                   {"orders": [o_sold["id"]]})
    save("12-orders-meta", st, meta)
    if st == 200 and meta:
        for o in meta.get("orders", []):
            for d in (o.get("metaDetails") or []):
                v = (d.get("value") or "")[:40]
                print(f"    id={o['id']} key={d.get('key')} decision={d.get('decision')} value={v}…")

    # 13. продажи в statistics-sandbox
    step(13, "statistics sales (srid == rid)")
    date_from = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
    st, sales = req("GET", STATS, "/api/v1/supplier/sales",
                    query="?" + urllib.parse.urlencode({"dateFrom": date_from, "flag": 0}))
    save("13-sales", st, sales)
    if st == 200 and isinstance(sales, list):
        for s in sales[:5]:
            print(f"    saleID={s.get('saleID')} srid={s.get('srid')} "
                  f"wh={s.get('warehouseType')} nm={s.get('nmId')}")

    print("\nГОТОВО. Фикстуры в", OUT)


if __name__ == "__main__":
    main(skip_price="--skip-price" in sys.argv,
         skip_stocks="--skip-stocks" in sys.argv)
