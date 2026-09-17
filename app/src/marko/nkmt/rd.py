"""Обогащение реестра деклараций данными Честного ЗНАКА (true-api/rd/list).

Номер+дату оператор вводит руками (списка «все декларации по ИНН» у ЧЗ нет —
проверено живьём); статус, срок действия, продукцию, список допустимых ТНВЭД,
техрегламенты, заявителя и изготовителя забираем по кнопке «Проверить в ЧЗ»
или сразу после добавления. Пишет только в nkmt.declarations.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from marko.nkmt.models import Declaration
from marko.platform.models import PlatformKV

# наш doc_type → тип разрешительного документа ЧЗ
RD_TYPES = {"declaration": "CONFORMITY_DECLARATION",
            "certificate": "CONFORMITY_CERTIFICATE"}

TOKEN_REFRESH_MARGIN = timedelta(minutes=10)


def cached_token(db: Session) -> str | None:
    """Свежий токен ЧЗ из kv БЕЗ сетевого обновления (обновление может ждать
    signer до 240 с — недопустимо в фоновом обогащении после добавления).
    Нет кэша/просрочен → None: enrichment пропускается, полный флоу — кнопка
    «Проверить в ЧЗ» (manager.get_token)."""
    kv = db.get(PlatformKV, "mt_token")
    if not kv:
        return None
    expires = kv.value.get("expires")
    if not expires:
        return None
    try:
        exp = datetime.fromisoformat(expires)
    except ValueError:
        return None
    if exp - TOKEN_REFRESH_MARGIN > datetime.now(timezone.utc).replace(tzinfo=None):
        return kv.value.get("token")
    return None


def _split_tnved(raw: str) -> list[str]:
    """"6505003000, 6505009000" → ["6505003000", "6505009000"] (порядок ЧЗ)."""
    return [t.strip() for t in str(raw or "").split(",") if t.strip()]


def enrich_declarations(db: Session, client, token: str,
                        decls: list[Declaration]) -> dict:
    """Обновить rich-поля деклараций из ЧЗ (NkClient.rd_list, чанки ≤25).

    Матчинг ответа — по номеру (casefold) и дате dateFrom == doc_date; ответ
    без пары — «не найдена». Возвращает {"checked", "found", "not_found":
    [номера], "updated": [Declaration.id]} и КОММИТИТ. Сетевые ошибки
    (NkHttpError) прокидываются вызывающему — здесь только честные 200-ответы.
    """
    payload = [{"type": RD_TYPES.get(d.doc_type, "CONFORMITY_DECLARATION"),
                "number": d.doc_number, "dateFrom": d.doc_date} for d in decls]
    resp = client.rd_list(token, payload) if payload else {"documents": [], "errors": []}
    by_pair = {(d.get("number", "").casefold(), d.get("dateFrom", "")): d
               for d in resp.get("documents", []) if isinstance(d, dict)}
    checked = found = 0
    updated: list[int] = []
    not_found: list[str] = []
    for d in decls:
        checked += 1
        rd = by_pair.get((d.doc_number.casefold(), d.doc_date))
        if rd is None:
            not_found.append(d.doc_number)
            continue
        found += 1
        d.status = str(rd.get("status", "") or "")
        d.date_to = str(rd.get("dateTo", "") or "")
        d.product_name = str(rd.get("productName", "") or "")
        d.tnved_list = _split_tnved(rd.get("productTnved", ""))
        d.techregs = str(rd.get("productTechRegulations", "") or "")
        d.applicant = str(rd.get("applicantProductName", "") or "")
        d.manufacturer = str(rd.get("manufacturerProductName", "") or "")
        d.checked_at = datetime.now(timezone.utc)
        updated.append(d.id)
    db.commit()
    return {"checked": checked, "found": found, "not_found": not_found,
            "updated": updated}
