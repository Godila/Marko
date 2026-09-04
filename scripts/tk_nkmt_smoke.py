"""Ручной smoke NKMT-конвейера (карточки НК) против песочницы ЧЗ (ТК), по образцу scripts/tk_e2e.py.

Прогоняет живые куски конвейера публикаций НК на стенде ТК: справочники
(/nk/attributes, /nk/categories, /nk/brands) -> /nk/feed (moderation:1) ->
/nk/feed-status до Moderated -> /nk/feed-product-document -> подпись xml через
прод-гейтвей (doc_sign, detached CAdES) -> /nk/feed-product-sign-pkcs ->
feed-status = Signed. Флоу проще service.sign_batch (без БД-статусов), но
зеркалит его: спаривание xml по gtin, items {goodId, base64Xml, signature}.

GTIN передаются вручную: generate-gtins в ТК заблокирован настройкой организации
(«генерация GTIN доступна только в системе ГС1» — живой ресерч 2026-09-03);
без gtin шаги фида пропускаются с пометкой.

Запуск на VM (скрипт целиком из файла, одной строкой):
    cat /root/tk_nkmt_smoke.py | docker exec -i deploy-api-1 python - "gtin1,gtin2" [--dry]

Локальный офлайн-прогон (вне контейнера, canned-данные, без сети/БД/зависимостей):
    python scripts/tk_nkmt_smoke.py --dry
(импорты mpmt ленивые — на --dry не нужны; живой запуск работает только внутри
deploy-api-1, где /app/src добавлен в sys.path).

Скрипт не пишет в прод-БД ничего, кроме задач подписи (sign.tasks, безвредно).
Токен ТК живёт в памяти процесса, kv не трогаем — manager.get_token здесь
неприменим: его кэш platform.kv продовый, он вернул бы прод-токен (401 на ТК)
или затёр бы его ТК-токеном; поэтому паттерн tk_e2e.py (tk_token ниже).
Токен и подписи НЕ печатаем — только длины. Результаты — в прогресс-леджер
.superpowers/sdd/progress.md.
"""
import argparse
import base64
import json
import sys
import time

TK3 = "https://markirovka.sandbox.crptech.ru/api/v3/true-api"   # NkClient — база /nk/*
TK4 = "https://markirovka.sandbox.crptech.ru/api/v4/true-api"   # auth ЧЗ (auth_key/sign_in)
TNVED = "6109100000"          # футболки/майки — образец из живого ресерча
BRAND_NAME = "YCPB"           # бренд строкой (name) — решено по дампу, проверить фактом
ATTR_FULL_NAME = 2478         # «Полное наименование товара», обязательный (фикстура nk-attrs-6109100000-m)
ATTR_COMPOSITION = 2483       # «Состав», обязательный
POLL_INTERVAL = 10            # сек между опросами feed-status
POLL_ITERATIONS = 60          # ~10 минут на модерацию
FINAL_STATUSES = ("Moderated", "Rejected", "Signed")
DRY_GTINS = ["20000000000001", "20000000000002"]

sys.path.insert(0, "/app/src")   # внутри deploy-api-1; локально пути нет — не мешает


def _mpmt():
    """Ленивые импорты mpmt: живой путь исполняется только в контейнере."""
    from mpmt.connector_mt.client import MtClient
    from mpmt.connector_mt.manager import _sign_via_gateway
    from mpmt.db import SessionLocal
    from mpmt.nkmt.client import NkClient, NkHttpError
    from mpmt.settings import settings
    return MtClient, _sign_via_gateway, SessionLocal, NkClient, NkHttpError, settings


class Steps:
    """Шаги с OK/FAIL/SKIP; FAIL немедленно печатает итог и роняет скрипт."""

    def __init__(self):
        self.items = []

    def ok(self, name, detail=""):
        self.items.append((name, True))
        print(f"[OK] {name}" + (f": {detail}" if detail else ""))

    def skip(self, name, note=""):
        self.items.append((name, None))
        print(f"[SKIP] {name}" + (f": {note}" if note else ""))

    def fail(self, name, err):
        self.items.append((name, False))
        print(f"[FAIL] {name}: {err}")
        self.print()
        raise SystemExit(1)

    def print(self):
        print("\nИтог smoke:")
        for name, st in self.items:
            mark = "OK  " if st else ("SKIP" if st is None else "FAIL")
            print(f"  {mark}  {name}")
        if any(s is False for _, s in self.items):
            print("SMOKE: FAIL")
        elif any(s is None for _, s in self.items):
            print("SMOKE: OK (с пропусками)")
        else:
            print("SMOKE: OK")


def tk_token(db, c, sign, settings) -> str:
    """Токен ТК в памяти процесса — паттерн tk_e2e.py (см. докстринг модуля)."""
    pair = c.auth_key()
    sig = sign(db, "auth_sign", {"data": pair["data"]})
    resp = c.sign_in(pair["uuid"], sig, settings.mt_inn)
    token = resp.get("token") or resp.get("uuidToken")
    if not token:
        raise RuntimeError(f"no token: {json.dumps(resp)[:300]}")
    print(f"[auth] OK, expire={resp.get('expireDate')}, token_len={len(token)}")
    return token


def feed_entry(gtin: str, cat_id) -> dict:
    """Минимальная валидная entry /nk/feed для 6109100000 (зеркалит service._feed_entry)."""
    return {"gtin": gtin, "good_name": f"SMOKE {gtin}", "tnved": TNVED,
            "brand": BRAND_NAME, "categories": [int(cat_id)], "moderation": 1,
            "good_attrs": [
                {"attr_id": ATTR_FULL_NAME, "attr_value": f"SMOKE {gtin}"},
                {"attr_id": ATTR_COMPOSITION, "attr_value": "100% хлопок"}]}


def poll_feed(client, token, feed_id, iterations, interval, label):
    """/nk/feed-status до финального статуса; возвращает (status, raw)."""
    last = None
    for _ in range(iterations):
        raw = client.feed_status(token, feed_id) or {}
        st = raw.get("status", "")
        if st != last:
            last = st
            print(f"[poll] {label}: {st}")
        if st in FINAL_STATUSES:
            return st, raw
        time.sleep(interval)
    raise TimeoutError(
        f"{label}: за {iterations * interval}s статус {last} не дошёл до финального")


def rejected_error(raw: dict) -> str:
    """Компактный срез позиционных ошибок Rejected (errors/goodErrors — что вернёт)."""
    for key in ("errors", "goodErrors"):
        if raw.get(key):
            return json.dumps(raw[key], ensure_ascii=False)[:600]
    return json.dumps(raw, ensure_ascii=False)[:600]


# --------------------------------------------------------------------- live

def run_live(gtins: list[str], args) -> None:
    MtClient, sign, SessionLocal, NkClient, NkHttpError, settings = _mpmt()
    steps = Steps()
    with SessionLocal() as db:
        try:
            token = tk_token(db, MtClient(base_v3=TK3, base_v4=TK4), sign, settings)
        except Exception as e:
            steps.fail("token/auth ТК", e)
        steps.ok("token/auth ТК", f"token_len={len(token)} (не печатаем)")
        client = NkClient(TK3)
        steps.ok("NkClient", f"base={TK3}")

        try:
            attrs = client.attributes(token, TNVED, "m")
            steps.ok("attributes", f"tnved={TNVED} attr_type=m -> {len(attrs)} записей")
        except NkHttpError as e:
            steps.fail("attributes", e)

        try:
            cats = client.categories(token, TNVED)
            if not cats:
                steps.fail("categories", f"пусто для {TNVED}")
            first = cats[0].get("cat_id")
            steps.ok("categories", f"{len(cats)} шт, first cat_id={first} "
                                   f"({cats[0].get('cat_name')})")
        except NkHttpError as e:
            steps.fail("categories", e)

        try:
            brands = client.brands(token, BRAND_NAME)
            exact = next((b for b in brands if b.get("name") == BRAND_NAME), None)
            detail = f"name~{BRAND_NAME} -> {len(brands)} шт"
            if exact:
                detail += f", точное совпадение brand_id={exact.get('brand_id')}"
            steps.ok("brands", detail)
        except NkHttpError as e:
            steps.fail("brands", e)

        if not gtins:
            steps.skip("feed..sign (весь хвост)",
                       "нет gtin: generate-gtins в ТК заблокирован (только ГС1), "
                       "gtin указываются вручную")
            steps.print()
            return

        entries = [feed_entry(g, first) for g in gtins]  # noqa: F821 — first есть: cats не пусто
        try:
            feed_id = client.feed(token, entries)["feed_id"]
            if not feed_id:
                raise RuntimeError("feed без feed_id")
            steps.ok("feed", f"feed_id={feed_id}, entries={len(entries)} "
                             f"(moderation:1, brand='{BRAND_NAME}')")
        except Exception as e:
            steps.fail("feed", e)

        try:
            st, raw = poll_feed(client, token, feed_id, args.poll_iterations,
                                args.poll_interval, "feed-status")
        except Exception as e:
            steps.fail("feed-status poll", e)
        if st == "Rejected":
            steps.fail("feed-status poll", f"Rejected: {rejected_error(raw)}")
        if st == "Signed":  # фид уже подписан кем-то ранее — хвост не нужен
            steps.skip("sign", f"feed сразу Signed (gtin уже публиковались)")
            steps.print()
            return
        steps.ok("feed-status poll", f"{st}")

        # Moderated: документ на подпись -> gateway doc_sign -> sign-pkcs (зеркало sign_batch)
        try:
            doc = client.feed_product_document(token, gtins) or {}
            xmls = doc.get("xmls") or []
            by_gtin = {str(e.get("gtin") or ""): e for e in xmls
                       if isinstance(e, dict) and e.get("xml")}
            items = []
            for g in gtins:
                entry = by_gtin.get(g)
                if entry is None:
                    raise RuntimeError(f"нет xml для gtin {g} "
                                       f"(errors={json.dumps(doc.get('errors'))[:300]})")
                data_b64 = base64.b64encode(entry["xml"].encode("utf-8")).decode("ascii")
                sig = sign(db, "doc_sign", {"data_b64": data_b64})
                items.append({"goodId": entry["goodId"], "base64Xml": data_b64,
                              "signature": sig})
                print(f"[sign] gtin={g} goodId={entry['goodId']} "
                      f"xml_b64_len={len(data_b64)} sig_len={len(sig)}")
            steps.ok("feed-product-document + doc_sign",
                     f"{len(items)} xml, подписи через прод-гейтвей (длины выше)")
        except Exception as e:
            steps.fail("feed-product-document + doc_sign", e)

        try:
            resp = client.feed_product_sign_pkcs(token, items) or {}
            steps.ok("feed-product-sign-pkcs",
                     f"signed={resp.get('signed')} errors={json.dumps(resp.get('errors'))[:300]}")
        except NkHttpError as e:
            steps.fail("feed-product-sign-pkcs", e)

        try:
            st2, raw2 = poll_feed(client, token, feed_id, args.poll_iterations,
                                  args.poll_interval, "final feed-status")
        except Exception as e:
            steps.fail("final feed-status poll", e)
        if st2 != "Signed":
            steps.fail("final feed-status poll", f"{st2}: {rejected_error(raw2)}")
        steps.ok("final feed-status poll", "Signed")
        steps.print()


# ---------------------------------------------------------------------- dry

def run_dry(gtins: list[str], args) -> None:
    """Офлайн-прогон тех же шагов на canned-данных: без сети, БД и mpmt."""
    steps = Steps()
    token = "DRY." + "t" * 59
    print(f"[auth] OK (dry), expire=2099-01-01T00:00:00, token_len={len(token)}")
    steps.ok("token/auth ТК", f"token_len={len(token)} (canned, не печатаем)")
    steps.ok("NkClient", f"base={TK3} (dry — сеть не трогаем)")

    attrs = [{"attr_id": i} for i in (13933, 36, ATTR_FULL_NAME, ATTR_COMPOSITION,
                                      2504, 3959, 13914, 14013, 35, 13836, 12)]
    steps.ok("attributes", f"tnved={TNVED} attr_type=m -> {len(attrs)} записей (canned: "
                           "фикстура nk-attrs-6109100000-m, 11 обязательных)")
    first = 30728  # «Одежда» из фикстуры категорий — любое валидное int
    cats = [{"cat_id": first, "cat_name": "Одежда (dry)"}]
    steps.ok("categories", f"{len(cats)} шт, first cat_id={first} ({cats[0]['cat_name']})")
    brands = [{"brand_id": 2102811, "name": BRAND_NAME}]
    steps.ok("brands", f"name~{BRAND_NAME} -> {len(brands)} шт, точное совпадение "
                       f"brand_id={brands[0]['brand_id']} (canned)")

    if not gtins:
        steps.skip("feed..sign (весь хвост)", "нет gtin")
        steps.print()
        return

    entries = [feed_entry(g, first) for g in gtins]
    print(f"[data] entry[0]: {json.dumps(entries[0], ensure_ascii=False)}")
    feed_id = "DRY-FEED-0001"
    steps.ok("feed", f"feed_id={feed_id}, entries={len(entries)} (moderation:1, "
                     f"brand='{BRAND_NAME}')")
    for st in ("Received", "Processing", "Moderated"):
        print(f"[poll] feed-status: {st}")
    steps.ok("feed-status poll", "Moderated")

    items = []
    for g in gtins:
        xml = (f"<product><gtin>{g}</gtin><good_name>SMOKE {g}</good_name>"
               f"</product>")
        data_b64 = base64.b64encode(xml.encode("utf-8")).decode("ascii")
        sig = "DRY." + "s" * 343  # detached CAdES ~3-4 КБ base64
        items.append({"goodId": f"DRY-GOOD-{g[-4:]}", "base64Xml": data_b64,
                      "signature": sig})
        print(f"[sign] gtin={g} goodId=DRY-GOOD-{g[-4:]} "
              f"xml_b64_len={len(data_b64)} sig_len={len(sig)}")
    steps.ok("feed-product-document + doc_sign",
             f"{len(items)} xml, подписи canned (длины выше)")
    steps.ok("feed-product-sign-pkcs", "signed=1 (canned), errors=null")
    print("[poll] final feed-status: Signed")
    steps.ok("final feed-status poll", "Signed")
    steps.print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Smoke NKMT-конвейера на ТК (песочница ЧЗ); детали — докстринг модуля")
    ap.add_argument("gtins", nargs="?", default="",
                    help="gtin через запятую (по 14 цифр); без них шаги фида пропускаются "
                         "(--dry подставляет canned)")
    ap.add_argument("--dry", action="store_true",
                    help="офлайн-прогон на canned-данных: без сети, БД и mpmt")
    ap.add_argument("--poll-iterations", type=int, default=POLL_ITERATIONS,
                    help=f"итераций опроса feed-status (default {POLL_ITERATIONS})")
    ap.add_argument("--poll-interval", type=int, default=POLL_INTERVAL,
                    help=f"пауза между опросами, сек (default {POLL_INTERVAL})")
    args = ap.parse_args()

    gtins = [g.strip() for g in args.gtins.split(",") if g.strip()]
    bad = [g for g in gtins if len(g) != 14 or not g.isdigit()]
    if bad:
        raise SystemExit(f"битые gtin {bad}: ожидается 14 цифр через запятую")
    if args.dry and not gtins:
        gtins = DRY_GTINS
        print(f"[dry] gtin не заданы — canned: {gtins}")

    (run_dry if args.dry else run_live)(gtins, args)


if __name__ == "__main__":
    main()
