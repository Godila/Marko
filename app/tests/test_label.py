"""Этикетка КиЗ 58×40 мм: GS1-DataMatrix ПОЛНОГО кода (sgtin с криптохвостом).

Превью + PDF: парсинг (GS/'!'-формы), guard по decision из кэша закреплений
WB и по cis_status журнала, каскад имени НК→журнал→GTIN, golden-roundtrip
(декод sgtin из готового PDF байт-в-байт, включая GS-разделители).
"""
import io
import time

import pytest
from fastapi.testclient import TestClient
from marko.api.app import create_app
from marko.journal.trace import GS
from marko.platform.models import (PlatformAudit, PlatformKV, PlatformPrincipal,
                                   PlatformToken, hash_token)
from sqlalchemy.dialects.postgresql import insert as pg_insert

KM2 = "0104630520676025215UKsE;hVmAtad"     # прод-формат КМ, 31 символ
GTIN = "04630520676025"
FULL = KM2 + GS + "91EE16" + GS + "92dGVzdA=="   # полный КиЗ с криптохвостом
AUTH = {"Authorization": "Bearer t1"}
AUTH_RO = {"Authorization": "Bearer t-ro"}


@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="user", name="owner"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t1"), scopes="read,docs:submit"))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t-ro"), scopes="read"))
    db.commit()
    return TestClient(create_app())


def _kv_meta(db, sgtin, decision, order_id=77, ts=None):
    """Кэш закреплений wb_meta_cache с решением WB по этому sgtin."""
    value = {"by_id": {str(order_id): {"fetched_at": ts if ts is not None else time.time(),
                                       "sgtins": [{"sgtin": sgtin, "decision": decision}]}}}
    db.execute(pg_insert(PlatformKV).values(key="wb_meta_cache", value=value))
    db.commit()


# ---- чистые функции модуля ----

def test_parse_full_sgtin_groups():
    from marko.label import gs1_notation, parse_sgtin
    p = parse_sgtin(FULL)
    assert p["km"] == KM2 and p["gtin"] == GTIN and p["serial"] == "5UKsE;hVmAtad"
    assert gs1_notation(FULL) == "[01]04630520676025[21]5UKsE;hVmAtad[91]EE16[92]dGVzdA=="


def test_matrix_accepts_parens_in_serial():
    """Прод-серийники содержат круглые скобки ('5(>(mSo?WUebg', инцидент
    25.09: круглая нотация ломала zint 'brackets don't match'). Квадратная
    нотация держит их как данные; decode — байт-в-байт, ]d2."""
    import io

    import zxingcpp
    from PIL import Image, ImageOps
    from marko.label import matrix, parse_sgtin
    full = "01" + GTIN + "21" + "5(>(mSo?WUebg" + GS + "91EE12" + GS + \
           "92Fe7YBxYK0Z2rAU4/9aTAj/aIKu7oybdQ9jUq/F0vhJU="
    p = parse_sgtin(full)
    from marko.label import gs1_notation
    size, rows = matrix(gs1_notation(p["sgtin"]))
    pil = Image.new("L", (size, size))
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            pil.putpixel((x, y), 0 if ch == "1" else 255)
    big = ImageOps.expand(pil.resize((size * 8, size * 8), Image.NEAREST), border=24, fill=255)
    buf = io.BytesIO(); big.save(buf, format="PNG")
    res = zxingcpp.read_barcodes(Image.open(buf))
    assert res and res[0].symbology_identifier == "]d2"
    assert res[0].bytes.decode() == full


def test_square_bracket_in_sgtin_refused(db, client):
    """Квадратные скобки — разделители нотации: код с ними не поддерживается
    (честный отказ лучше битой этикетки)."""
    from marko.label import LabelError, normalize_sgtin
    with pytest.raises(LabelError):
        normalize_sgtin(KM2 + GS + "91EE[16" + GS + "92dGVzdA==")


def test_short_km_rejected_needs_crypto():
    """Короткий КМ (31, без 91/92) для печати не годится: касса проверяет
    крипточасть — этикетка была бы «правильной по виду, мёртвой по факту»."""
    from marko.label import LabelError, parse_sgtin
    with pytest.raises(LabelError) as e:
        parse_sgtin(KM2)
    assert "полный" in str(e.value)


def test_bang_form_normalized_to_gs():
    """Печатная '!'-форма (GS заменён на '!') принимается как полный код."""
    from marko.label import normalize_sgtin
    assert normalize_sgtin(KM2 + "!91EE16!92dGVzdA==") == FULL


def test_bang_inside_serial_of_gs_form_preserved():
    """GS-канон с '!' внутри серийника: '!' — данные серийника, не разделитель
    (подмена резала бы код → этикетка несуществующего КиЗ)."""
    from marko.label import parse_sgtin
    km_bang = f"01{GTIN}21" + "5UK!91xyz"
    p = parse_sgtin(km_bang + GS + "91EE16" + GS + "92dGVzdA==")
    assert p["km"] == km_bang and p["serial"] == "5UK!91xyz"


def test_matrix_square_and_deterministic():
    from marko.label import gs1_notation, matrix
    size, rows = matrix(gs1_notation(FULL))
    assert size == len(rows) and all(len(r) == size for r in rows)
    assert set("".join(rows)) <= {"0", "1"} and "1" in "".join(rows)
    assert matrix(gs1_notation(FULL)) == (size, rows)          # детерминизм


def test_wrap_name_max_four_lines():
    from marko.label import wrap_name
    lines = wrap_name("Свитшот " * 30)
    assert 1 <= len(lines) <= 4 and lines[-1].endswith("…")


def test_name_cascade_card_journal_gtin(db):
    from marko.label import name_of
    from marko.journal.models import Item
    from marko.nkmt.models import Batch, Card
    b = Batch(source_filename="x.xlsx"); db.add(b); db.flush()
    db.add(Card(batch_id=b.id, article="7008_белый_теплый_44", gtin=GTIN,
                tnved="6104620000", name="Свитшот с карманом Vilui"))
    db.commit()
    # карточка НК: наименование_артикул — формат этикетки WB
    assert name_of(db, GTIN, KM2) == ("Свитшот с карманом Vilui_7008_белый_теплый_44", "card")
    db.delete(db.query(Card).first()); db.commit()
    # журнал: имя из карточки Честного ЗНАКа (cis-sync)
    db.add(Item(km=KM2, last_event={}, cis_product_name="Свитшот с карманом Vilui (ЧЗ)"))
    db.commit()
    assert name_of(db, GTIN, KM2) == ("Свитшот с карманом Vilui (ЧЗ)", "journal")
    db.delete(db.query(Item).first()); db.commit()
    assert name_of(db, GTIN, KM2) == (GTIN, "gtin")


# ---- HTTP: превью ----

def test_preview_ok_fields(db, client):
    r = client.get("/v1/label/preview", params={"sgtin": FULL}, headers=AUTH)
    assert r.status_code == 200
    b = r.json()
    assert b["km"] == KM2 and b["gtin"] == GTIN and not b["blocked"]
    assert b["modules_size"] == len(b["modules"]) > 0
    assert b["human"] == [f"01{GTIN}", "215UKsE;hVmAtad"]
    assert b["decision"] is None            # кэш пуст — решения WB нет, печать ок
    assert len(b["lines"]) >= 1


def test_preview_blocked_decision_200_with_reason(db, client):
    """Блок — не ошибка: карточка показывает причину (код мёртв)."""
    _kv_meta(db, FULL, "sgtinRetired")
    b = client.get("/v1/label/preview", params={"sgtin": FULL}, headers=AUTH).json()
    assert b["blocked"] and "выбыл" in b["block_reason"]
    assert b["decision"] == "sgtinRetired" and b["decision_ts"]


def test_preview_blocked_by_cis_status(db, client):
    from marko.journal.models import Item
    db.add(Item(km=KM2, last_event={}, cis_status="written_off"))
    db.commit()
    b = client.get("/v1/label/preview", params={"sgtin": FULL}, headers=AUTH).json()
    assert b["blocked"] and "списан" in b["block_reason"]


def test_preview_short_km_422(db, client):
    r = client.get("/v1/label/preview", params={"sgtin": KM2}, headers=AUTH)
    assert r.status_code == 422 and "полный" in r.json()["detail"]


# ---- HTTP: PDF ----

def test_print_pdf_headers_and_mediabox(db, client):
    r = client.get("/v1/label/print", params={"sgtin": FULL}, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="kiz-{GTIN}.pdf"'
    pdf = r.content
    assert pdf[:5] == b"%PDF-"
    # MediaBox сжат в object-stream — размер страницы проверяем парсером
    fitz = pytest.importorskip("fitz")
    page = fitz.open(stream=pdf, filetype="pdf")[0]
    assert abs(page.rect.width - 164.41) < 1    # 58 мм
    assert abs(page.rect.height - 113.39) < 1   # 40 мм


def test_print_pdf_roundtrip_full_sgtin(db, client):
    """Golden-тест: декодированный из PDF код байт-в-байт равен исходному
    sgtin (включая GS-разделители) — главный тест качества этикетки."""
    fitz = pytest.importorskip("fitz")
    import zxingcpp
    from PIL import Image
    r = client.get("/v1/label/print", params={"sgtin": FULL}, headers=AUTH)
    page = fitz.open(stream=r.content, filetype="pdf")[0]
    pix = page.get_pixmap(dpi=600)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    res = zxingcpp.read_barcodes(img)
    assert res, "DataMatrix из PDF не декодируется"
    assert res[0].symbology_identifier == "]d2"          # GS1 FNC1 в первой позиции
    assert res[0].bytes.decode() == FULL


def test_print_blocked_decision_422(db, client):
    _kv_meta(db, FULL, "sgtinWrittenOff")
    r = client.get("/v1/label/print", params={"sgtin": FULL}, headers=AUTH)
    assert r.status_code == 422 and "списан" in r.json()["detail"]


def test_print_blocked_by_cis_status_422(db, client):
    from marko.journal.models import Item
    db.add(Item(km=KM2, last_event={}, cis_status="retired"))
    db.commit()
    r = client.get("/v1/label/print", params={"sgtin": FULL}, headers=AUTH)
    assert r.status_code == 422 and "выбыл" in r.json()["detail"]


def test_stale_decision_ignored(db, client):
    """Протухшее решение (кэш 10 мин) не блокирует: код мог вернуться в оборот."""
    _kv_meta(db, FULL, "sgtinRetired", ts=time.time() - 3600)
    b = client.get("/v1/label/preview", params={"sgtin": FULL}, headers=AUTH).json()
    assert not b["blocked"] and b["decision"] is None


def test_print_audits_and_ro_allowed(db, client):
    r = client.get("/v1/label/print", params={"sgtin": FULL}, headers=AUTH_RO)
    assert r.status_code == 200                       # печать — чтение, RO-токену можно
    acts = db.query(PlatformAudit).filter_by(action="label.printed").all()
    assert len(acts) == 1
    assert acts[0].detail["km"] == KM2 and acts[0].detail["gtin"] == GTIN


def test_print_gs_in_query_survives(db, client):
    """GS (%1D) и спецсимволы серийника проходят query-энкодинг до PDF."""
    r = client.get("/v1/label/print", params={"sgtin": FULL}, headers=AUTH)
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
