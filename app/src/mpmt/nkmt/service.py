"""Сервис импорта выгрузки НК: parse → defaults → validate → upsert карточек.

Семантика пакета: повторный артикул ВНУТРИ файла — ошибка строки (первое
вхождение выигрывает, дубль идёт только в stats — article в cards UNIQUE);
артикул из прошлых батчей обновляется на месте: контент заново из выгрузки,
good_id/непустой gtin сохраняются (пустой gtin карточки дозаполняется
валидным gtin строки), карточка переезжает в новый батч; gtin, занятый
другой карточкой (другой артикул, включая уже записанные в этом батче), —
ошибка строки «gtin занят», отклонённый gtin в карточку не записывается.
"""
from mpmt.nkmt.dicts import get_defaults
from mpmt.nkmt.models import Batch, Card
from mpmt.nkmt.parse import apply_defaults, parse_xlsx
from mpmt.nkmt.validate import validate_rows


def import_batch(db, filename: str, data: bytes, client, token) -> int:
    """Выгрузка xlsx → батч + карточки; возвращает batch_id."""
    rows = apply_defaults(parse_xlsx(data), get_defaults(db))
    validated = validate_rows(db, client, token, rows)
    batch = Batch(source_filename=filename)
    db.add(batch)
    db.flush()  # id нужен карточкам для FK
    stats = {"ok": 0, "error": 0}
    seen: set[str] = set()
    for v in validated:
        article = v["article"]
        if article in seen:
            stats["error"] += 1  # дубль артикула в файле: карточка первого вхождения уже есть
            continue
        seen.add(article)
        status, error = ("ok", "") if v["ok"] else ("error", v["error"])
        gtin = v["gtin"]
        if gtin:
            clash = db.query(Card).filter(Card.gtin == gtin,
                                          Card.article != article).first()
            if clash:
                status = "error"
                error = f"{error}; gtin занят" if error else "gtin занят"
                gtin = ""  # отклонённый gtin не сохраняем
        card = db.query(Card).filter_by(article=article).first()
        if card is None:
            card = Card(article=article, gtin=gtin, batch_id=batch.id)
            db.add(card)
        elif gtin and not card.gtin:
            card.gtin = gtin  # дозаполняем только пустой gtin; непустой сохраняем
        card.batch_id = batch.id
        card.tnved, card.name, card.cat_id = v["tnved"], v["name"], v["cat_id"]
        card.attributes = v["attributes"]
        card.status, card.error_text = status, error
        stats["ok" if status == "ok" else "error"] += 1
    batch.status = "new" if stats["error"] == 0 else "partial"
    batch.stats = stats
    db.commit()
    return batch.id
