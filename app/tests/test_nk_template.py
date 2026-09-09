"""Шаблон выгрузки: parse.SPEC — единый источник для шапки, констант и инструкции."""
import io

import openpyxl


def test_spec_derived_constants():
    from marko.nkmt.parse import COLUMNS, DEFAULTED_KEYS, REQUIRED_ROW_KEYS, SPEC
    assert REQUIRED_ROW_KEYS == ["article", "tnved", "name", "product_type", "color",
                                 "composition", "size"]
    assert DEFAULTED_KEYS == ["product_type", "brand", "target_gender", "size_system",
                              "declaration_number", "declaration_date", "producer",
                              "country"]
    for s in SPEC:
        if s.title:
            assert COLUMNS[s.title.casefold()] == s.key
    assert set(COLUMNS.values()) == {s.key for s in SPEC if s.title}


def test_build_template():
    from marko.nkmt.parse import SPEC
    from marko.nkmt.template import build_template
    wb = openpyxl.load_workbook(io.BytesIO(build_template()))
    assert wb.sheetnames == ["Выгрузка", "Инструкция"]
    ws = wb["Выгрузка"]
    titles = [s.title for s in SPEC if s.title]
    assert [c.value for c in ws[1]] == titles
    assert ws.cell(row=2, column=titles.index("Артикул") + 1).value == "AB-1001"
    info = wb["Инструкция"]
    assert info.cell(row=1, column=1).value == "Колонка"
    assert info.max_row >= len(titles)   # по строке на колонку + абзацы правил
