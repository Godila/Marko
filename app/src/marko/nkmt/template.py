"""Шаблон выгрузки xlsx: шапка, строки-примеры и «Инструкция» — из parse.SPEC.

Единый источник колонок: рассинхрон шаблона с парсером невозможен по
построению (шапка листа = [s.title for s in SPEC]). Форматирование читабельное:
закреплённая шапка, жирные заголовки, ширины колонок по контенту, перенос
длинных описаний в «Инструкции».
"""
import io
import math

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from marko.nkmt.parse import SPEC

# строки-примеры по ключам SPEC: футболка — одежда с размером из справочника,
# шапка — типовой «one size» (подставляется правилом РД, если в файле пусто);
# бренд/декларация/пол/система размеров пусты — придут из правил и дефолтов
EXAMPLES = [
    {"article": "AB-1001", "tnved": "6109100000", "name": "Футболка хлопковая",
     "product_type": "ФУТБОЛКА", "color": "БЕЛЫЙ", "composition": "100% хлопок",
     "size": "M", "model": "AB-1001-M"},
    {"article": "GH-2001", "tnved": "6505009000", "name": "Шапка Мокко",
     "product_type": "ШАПКА", "color": "ОЛИВА", "composition": "50% шерсть 50% акрил",
     "size": "ONE SIZE", "model": "GH-2001"},
]

# правила игры для листа «Инструкция»: метка + текст в колонке описания
NOTES = [
    ("Подстановка", "Порядок: значение из файла > правило РД (бренд × вид товара) > "
                    "дефолты из «Справочников». Правило и дефолт заполняют только "
                    "пустые ячейки строки."),
    ("Повторный импорт", "Артикул из прошлых батчей обновляется на месте; дубль "
                         "артикула внутри файла — ошибка строки."),
    ("Ошибки", "Видны в предпросмотре до импорта и в карточке после."),
    ("Техрегламент", "Системный: подставляется всегда из дефолтов консоли, "
                     "колонки в файле нет."),
]

INFO_WIDTHS = {"A": 22, "B": 14, "C": 16, "D": 84}  # Колонка/Обязательная/Подставляется/Описание
_D_COL_CHARS = INFO_WIDTHS["D"] - 4                  # символов в строке переноса


def build_template() -> bytes:
    """Лист «Выгрузка» (шапка + примеры) и лист «Инструкция» (колонка,
    обязательность, подстановка, описание + правила игры)."""
    wb = openpyxl.Workbook()
    head_font = Font(bold=True)

    ws = wb.active
    ws.title = "Выгрузка"
    cols = [s for s in SPEC if s.title]
    ws.append([s.title for s in cols])
    for ex in EXAMPLES:
        ws.append([ex.get(s.key, "") for s in cols])
    for cell in ws[1]:
        cell.font = head_font
        cell.alignment = Alignment(vertical="center")
    for i, s in enumerate(cols, start=1):
        longest = max([len(s.title)]
                      + [len(str(ex.get(s.key, ""))) for ex in EXAMPLES])
        ws.column_dimensions[get_column_letter(i)].width = min(34, max(12, longest * 0.9 + 4))
    ws.freeze_panes = "A2"

    info = wb.create_sheet("Инструкция")
    info.append(["Колонка", "Обязательная", "Подставляется", "Описание"])
    for s in SPEC:
        info.append([s.title, "да" if s.required else "нет",
                     "да" if s.defaultable else "—", s.hint or "—"])
    for label, text in NOTES:
        info.append([label, "", "", text])
    for cell in info[1]:
        cell.font = head_font
    for col, width in INFO_WIDTHS.items():
        info.column_dimensions[col].width = width
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in info.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrap
    for r in range(2, info.max_row + 1):
        text = str(info.cell(row=r, column=4).value or "")
        info.row_dimensions[r].height = max(15, 13.5 * math.ceil(
            max(len(text), 1) / _D_COL_CHARS))
    info.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
