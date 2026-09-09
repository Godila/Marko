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


def test_parse_excel_date_cells():
    """Дата-ячейка xlsx (openpyxl отдаёт date/datetime) → ISO 'YYYY-MM-DD',
    а не str(datetime) '2026-01-01 00:00:00', который валит DATE_RE."""
    from datetime import date, datetime
    from marko.nkmt.parse import parse_xlsx
    row = list(ROW); row[12] = date(2026, 1, 1)
    assert parse_xlsx(make_xlsx(HDR, [row]))[0]["declaration_date"] == "2026-01-01"
    row = list(ROW); row[0], row[12] = "GH8461", datetime(2026, 2, 3, 13, 45)
    assert parse_xlsx(make_xlsx(HDR, [row]))[0]["declaration_date"] == "2026-02-03"


def test_parse_and_defaults():
    from marko.nkmt.parse import apply_defaults, parse_xlsx
    rows = parse_xlsx(make_xlsx(HDR, [ROW, [None] * len(HDR)]))
    assert len(rows) == 1 and rows[0]["article"] == "GH8460" and rows[0]["tnved"] == "6505009000"
    out = apply_defaults(rows, {"brand": "YCPB", "target_gender": "ЖЕНСКИЙ",
                                "size_system": "МЕЖДУНАРОДНЫЙ", "declaration_number": "Д-1",
                                "declaration_date": "2026-01-01",
                                "techreg": 'ТР ТС 017/2011 "..."', "country": "RU",
                                "producer": "ИП Байкулов"})
    r = out[0]
    assert r["brand"] == "YCPB" and r["target_gender"] == "ЖЕНСКИЙ" and r["techreg"].startswith("ТР ТС 017")
    assert r["country"] == "RU" and r["declaration_number"] == "Д-1"


def test_apply_defaults_product_type():
    """Вид товара дефолтуем: пустой ← дефолт, файловый цел (условия правил
    матчатся по эффективному значению)."""
    from marko.nkmt.parse import apply_defaults
    rows = [{"product_type": ""}, {"product_type": "ШАПКА"}]
    out = apply_defaults(rows, {"product_type": "ФУТБОЛКА"})
    assert out[0]["product_type"] == "ФУТБОЛКА" and out[1]["product_type"] == "ШАПКА"
