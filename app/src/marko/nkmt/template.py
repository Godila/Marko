"""Шаблон выгрузки xlsx: шапка, строка-пример и «Инструкция» — из parse.SPEC.

Единый источник колонок: рассинхрон шаблона с парсером невозможен по
построению (шапка листа = [s.title for s in SPEC if s.title]).
"""
import io

import openpyxl

from marko.nkmt.parse import SPEC

# строка-пример по ключам SPEC (ключи без title в строку не попадают)
EXAMPLE = {
    "article": "AB-1001", "tnved": "6109100000", "name": "Футболка хлопковая",
    "product_type": "ФУТБОЛКА", "color": "БЕЛЫЙ", "composition": "100% хлопок",
    "size": "M", "model": "AB-1001-M", "gtin": "",
    # brand/декларация/пол/система размеров — пусто: придут из правил РД и дефолтов
}

# явные имена для подставляемых полей без колонки (вместо парсинга hint)
NO_TITLE_NAME = {"producer": "Производитель", "country": "Страна производства"}


def build_template() -> bytes:
    """Лист «Выгрузка» (шапка + пример) и лист «Инструкция» (колонка,
    обязательность, подстановка, описание + правила игры)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Выгрузка"
    cols = [s for s in SPEC if s.title]
    ws.append([s.title for s in cols])
    ws.append([EXAMPLE.get(s.key, "") for s in cols])

    info = wb.create_sheet("Инструкция")
    info.append(["Колонка", "Обязательная", "Подставляется", "Описание"])
    for s in SPEC:
        if s.title:
            info.append([s.title, "да" if s.required else "нет",
                         "да" if s.defaultable else "—", s.hint])
        else:
            info.append([NO_TITLE_NAME.get(s.key, s.key), "—",
                         "да" if s.defaultable else "—", s.hint])
    info.append([])
    info.append(["Порядок подстановки: значение из файла > правило РД (бренд × вид товара) "
                 "> дефолты из «Справочников».", "", "", ""])
    info.append(["Повторный импорт артикула обновляет карточку; дубль артикула внутри "
                 "файла — ошибка строки.", "", "", ""])
    info.append(["Ошибки строки видны в предпросмотре до импорта и в карточке после."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
