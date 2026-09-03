import io
import openpyxl


def make_xlsx(header, rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(header)
    for r in rows: ws.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


HDR = ["Артикул", "ТНВЭД", "Наименование", "Вид товара", "Цвет", "Состав", "Размер",
       "Модель/артикул", "Бренд", "Пол", "Размерная система", "Декларация", "Дата декларации"]
ROW = ["GH8460", "6505009000", "Шапка Мокко", "ШАПКА", "олива", "50% шерсть 50% акрил",
       "one size", "Мокко", "", "", "", "", ""]


def test_parse_and_defaults():
    from mpmt.nkmt.parse import apply_defaults, parse_xlsx
    rows = parse_xlsx(make_xlsx(HDR, [ROW, [None] * len(HDR)]))
    assert len(rows) == 1 and rows[0]["article"] == "GH8460" and rows[0]["tnved"] == "6505009000"
    out = apply_defaults(rows, {"brand": "YCPB", "target_gender": "ЖЕНСКИЙ",
                                "size_system": "МЕЖДУНАРОДНЫЙ", "declaration_number": "Д-1",
                                "declaration_date": "2026-01-01",
                                "techreg": 'ТР ТС 017/2011 "..."', "country": "РОССИЯ",
                                "producer": "ИП Байкулов"})
    r = out[0]
    assert r["brand"] == "YCPB" and r["target_gender"] == "ЖЕНСКИЙ" and r["techreg"].startswith("ТР ТС 017")
    assert r["country"] == "РОССИЯ" and r["declaration_number"] == "Д-1"
