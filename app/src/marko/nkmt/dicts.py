"""Служба справочников НК: кэш атрибутных моделей (kv, TTL 24ч), бренды, категории.

Атрибутная модель по ТН ВЭД меняется редко — кладём целиком в platform.kv
(«nk_attrs:{tnved}», fetched_at + m/r списки) и перевыбираем не чаще раза в сутки.
Бренды кэшируем в nkmt.brand_cache (имя — casefold), категории без
платформенного кэша: validate_rows передаёт cats_cache — один запрос
/nk/categories на ТН ВЭД на вызов импорта; при неоднозначностях нужна
подсказка оператора.
"""
import time

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marko.nkmt.models import BrandCache, Declaration, Rule
from marko.platform.models import PlatformKV

TTL = 24 * 3600
DEFAULTS_KEY = "nk_defaults"
DEFAULTS = {"brand": "YCPB", "techreg": 'ТР ТС 017/2011 "О безопасности продукции легкой промышленности"',
            "target_gender": "ЖЕНСКИЙ", "size_system": "МЕЖДУНАРОДНЫЙ",
            # live: attr 2630 — справочник ISOCountries, нужен код («RU»), не русское имя
            "country": "RU", "producer": "", "declaration_number": "", "declaration_date": ""}


class UnknownBrand(Exception):
    """Точного бренда нет ни в кэше, ни в НК; .args[0] = исходное имя."""


class AmbiguousCategory(Exception):
    """Несколько категорий по ТН ВЭД и hint не выбрал одну; .args[0] = list[dict(cat_id, cat_name)]."""


def _kv_put(db: Session, key: str, value: dict) -> None:
    db.execute(pg_insert(PlatformKV).values(key=key, value=value)
               .on_conflict_do_update(index_elements=[PlatformKV.key], set_={"value": value}))
    db.commit()


def attrs_model(db: Session, client, token: str, tnved: str) -> dict:
    """{"m": [...], "r": [...]} по ТН ВЭД; kv nk_attrs:{tnved}, просрочка > TTL."""
    key = f"nk_attrs:{tnved}"
    kv = db.get(PlatformKV, key)
    if kv and time.time() - kv.value.get("fetched_at", 0) <= TTL:
        return {"m": kv.value.get("m", []), "r": kv.value.get("r", [])}
    m = client.attributes(token, tnved, "m")
    r = client.attributes(token, tnved, "r")
    _kv_put(db, key, {"fetched_at": time.time(), "m": m, "r": r})
    return {"m": m, "r": r}


def resolve_brand(db: Session, client, token: str, name: str) -> int:
    """Точное совпадение имени (casefold): nkmt.brand_cache → client.brands → кэш; иначе UnknownBrand."""
    low = name.casefold()
    cached = db.execute(select(BrandCache).where(func.lower(BrandCache.name) == low)
                        ).scalar_one_or_none()
    if cached:
        return cached.brand_id
    # NkClient.brands — метод; в тестовых фейках атрибут-список затеняет метод
    found = client.brands(token, name) if callable(client.brands) else client.brands
    for b in found:
        if str(b.get("brand_name", "")).casefold() == low:
            brand_id = int(b["brand_id"])
            db.execute(pg_insert(BrandCache).values(name=low, brand_id=brand_id)
                       .on_conflict_do_update(index_elements=[BrandCache.name],
                                              set_={"brand_id": brand_id}))
            db.commit()
            return brand_id
    raise UnknownBrand(name)


def resolve_category(client, token: str, tnved: str, hint: str = "",
                     cats=None, cats_cache: dict | None = None) -> str:
    """cat_id (str) по ТН ВЭД; 0 категорий обычно = 404 → NkHttpError из клиента
    прокинется. cats — готовый список категорий: клиент не дёргается вообще.
    cats_cache — кэш вызывающего «раз на ТН ВЭД» (массовый импорт): первый
    вызов тянет /nk/categories, повторные ТН ВЭД-строки берут из словаря."""
    if cats is None:
        if cats_cache is not None and tnved in cats_cache:
            cats = cats_cache[tnved]
        else:
            cats = client.categories(token, tnved)
            if cats_cache is not None:
                cats_cache[tnved] = cats
    if len(cats) == 1:
        return str(cats[0]["cat_id"])
    if hint:
        h = hint.casefold().strip()
        for c in cats:
            if h in (str(c.get("cat_id", "")).casefold(), str(c.get("cat_name", "")).casefold()):
                return str(c["cat_id"])
    raise AmbiguousCategory([{"cat_id": c.get("cat_id"), "cat_name": c.get("cat_name")} for c in cats])


def get_defaults(db: Session) -> dict:
    kv = db.get(PlatformKV, DEFAULTS_KEY)
    return dict(kv.value) if kv else dict(DEFAULTS)


def set_defaults(db: Session, value: dict) -> None:
    _kv_put(db, DEFAULTS_KEY, value)


def get_rules(db: Session) -> list[dict]:
    """Активные правила РД с реквизитами декларации (join; FK RESTRICT гарантирует
    существование) — чистые dict'ы для resolve.match_rule, id по возрастанию."""
    rows = db.execute(select(Rule, Declaration.doc_number, Declaration.doc_date)
                      .join(Declaration, Rule.declaration_id == Declaration.id)
                      .order_by(Rule.id)).all()
    return [{"id": r.id, "brand": r.brand, "product_type": r.product_type,
             "declaration_id": r.declaration_id, "declaration_number": doc_number,
             "declaration_date": doc_date, "producer": r.producer}
            for r, doc_number, doc_date in rows]
