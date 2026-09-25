"""Этикетка КиЗ 58×40 мм для термо-принтера: GS1-DataMatrix ПОЛНОГО кода.

Сценарий — перепечатка бирки невыкупа в «повторной поставке»: код тот же,
экземпляр тот же, статус в ЧЗ не меняется (повреждённый КИЗ приравнивается
к отсутствующему — маркировку нужно переделать, WB-док). Один КиЗ = один
экземпляр товара; дублирование на другой товар — обход маркировки.

Стейтлес: имя — из каталога НК / журнала, решение WB — из кэша закреплений
(kv wb_meta_cache, 10 мин); сеть не трогаем. Полный sgtin с криптохвостом
(GS + AI 91/92) существует только в orders/meta — короткий КМ журнала для
печати не годится (касса проверяет крипточасть).

Геометрия и шрифт — по шаблону WB: лист 58×40 мм, матрица 23×23 мм (допуск
лёгпрома 15,2–24,4 мм, строго квадрат), имя 9pt слева, человекочитаемые
строки 7pt под матрицей, Roboto Regular (Apache 2.0, вендорен рядом).
"""
import io
from pathlib import Path

from sqlalchemy.orm import Session

from marko.journal.models import Item
from marko.journal.trace import GS, TraceError, _SPACES_RE, _card, normalize_km

FONT = Path(__file__).with_name("Roboto-Regular.ttf")
FONT_NAME = "RobotoKiz"            # регистрация идемпотентна, имя внутреннее
LABEL_W_MM, LABEL_H_MM = 58.0, 40.0
DM_MM = 23.0                       # сторона DataMatrix на этикетке
MAX_NAME_LINES = 4                 # левый блок: 29 мм × 9pt, дальше «…»


def _ensure_font() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT)))


class LabelError(ValueError):
    """Невалидный ввод оператора → роут отдаёт 422 с человеческим текстом."""


# решение WB (orders/meta, кэш закреплений) → причина блокировки печати
BLOCKED_DECISIONS = {
    "sgtinRetired": "код уже выбыл (продан) — решение Wildberries, перепечатка "
                    "запрещена: этикетка мертва, на другую единицу её клеить нельзя",
    "sgtinWrittenOff": "код списан — решение Wildberries, перепечатка запрещена",
    "sgtinNotFound": "код не найден в Честном знаке — решение Wildberries, "
                     "проверьте КиЗ в «Трассировке»",
}
# локальный факт ЧЗ (cis-sync журнала) — страховка при холодном кэше закреплений
BLOCKED_CIS = {
    "retired": "по последней проверке ЧЗ код выбыл — перепечатка запрещена",
    "written_off": "по последней проверке ЧЗ код списан — перепечатка запрещена",
}


def normalize_sgtin(raw: str) -> str:
    """Любая форма полного КиЗ → GS-канон: пробелы вырезаются, печатная
    '!'-замена GS валидна только перед AI крипточасти (91/92) — зеркало
    правила normalize_km. Хвост без всех групп 91/92 — LabelError: короткий
    КМ для печати не годится."""
    s = _SPACES_RE.sub("", raw or "")
    if not s:
        raise LabelError("пустой ввод: вставьте полный КиЗ (01… 21… 91… 92…)")
    # '!'-замена — только для печатной формы целиком (GS в ней отсутствует);
    # в GS-каноне '!' перед 91/92 — данные серийника, подмена резала бы код
    if GS not in s:
        s = "".join(GS if ch == "!" and s[i + 1:i + 3] in ("91", "92") else ch
                    for i, ch in enumerate(s))
    try:
        normalize_km(s)
    except TraceError as e:
        raise LabelError(str(e))
    if "[" in s or "]" in s:
        raise LabelError("в коде квадратные скобки — они служат разделителями "
                         "внутри генератора; такой КиЗ не поддерживается, "
                         "распечатайте этикетку из СУЗ")
    tail = s.split(GS)[1:]
    if not tail or not all(g[:2] in ("91", "92") for g in tail):
        raise LabelError(
            "нужен полный КиЗ с криптохвостом (91… 92…): короткий КМ для печати "
            "не годится — касса проверяет крипточасть. Полный код закреплён за "
            "заказом: «Идентификаторы WB» → закреплённые КиЗ → «Этикетка»")
    return s


def parse_sgtin(raw: str) -> dict:
    sgtin = normalize_sgtin(raw)
    km = normalize_km(sgtin)
    return {"sgtin": sgtin, "km": km, "gtin": km[2:16], "serial": km[18:]}


def gs1_notation(sgtin: str) -> str:
    """GS-канон → квадратная GS1-нотация zint: '[01]…[21]…[91]…[92]…'.
    Квадратные скобки — разделители (zxing включает GS1PARENS_MODE только
    когда строка НЕ начинается с '['), поэтому круглые скобки в серийнике
    остаются данными — прод-серийники вида '5(>(mSo?WUebg' валидны.
    Валидацию групп сделал normalize_sgtin — здесь чистая сборка."""
    head = sgtin.split(GS)[0]
    parts = [f"[01]{head[2:16]}", f"[21]{head[18:]}"]
    parts += [f"[{g[:2]}]{g[2:]}" for g in sgtin.split(GS)[1:]]
    return "".join(parts)


def matrix(notation: str) -> tuple[int, list[str]]:
    """GS1-DataMatrix (FNC1 в первой позиции, строго квадрат) → (size, rows):
    '1' — тёмный модуль. zxing-cpp берёт размер сам (36 только для этого
    объёма криптохвоста) — вызывающие читают size, не хардкодят."""
    import zxingcpp
    try:
        bc = zxingcpp.create_barcode(notation, zxingcpp.DataMatrix,
                                     gs1=True, force_square=True)
    except ValueError as e:     # zint-валидатор кидает, а не возвращает код
        raise LabelError(f"не удалось собрать GS1-DataMatrix: {e}")
    if not bc.valid or bc.symbology_identifier != "]d2":
        raise LabelError(f"не удалось собрать GS1-DataMatrix: "
                         f"{bc.error or bc.symbology_identifier}")
    img = bc.to_image(scale=1, add_quiet_zones=False)
    h, w = img.shape[:2]
    raw = bytes(memoryview(img))
    rows = ["".join("1" if raw[y * w + x] < 128 else "0" for x in range(w))
            for y in range(h)]
    return w, rows


def name_of(db: Session, gtin: str, km: str) -> tuple[str, str]:
    """(имя для этикетки, источник): карточка НК «наименование_артикул»
    (формат WB-шаблона) → cis_product_name журнала (карточка ЧЗ) → GTIN."""
    card = _card(db, gtin)
    if card and card.get("name"):
        art = card.get("article") or ""
        return (f"{card['name']}_{art}" if art else card["name"], "card")
    it = db.get(Item, km)
    if it and it.cis_product_name:
        return it.cis_product_name, "journal"
    return gtin, "gtin"


def wrap_name(text: str) -> list[str]:
    """Перенос имени по ширине левого блока (29 мм при 9pt), не по символам:
    кириллица разной ширины, переполнение на термо-этикетке фатально.
    Потолок — 4 строки, дальше «…»."""
    _ensure_font()
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    limit = 29 * mm

    def fits(s):
        return stringWidth(s, FONT_NAME, 9) <= limit

    lines: list[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if fits(cand):
            cur = cand
            continue
        if cur:
            lines.append(cur)
        # слово-гигант шире блока — жёсткий посимвольный нарез
        while not fits(word) and word:
            cut = ""
            for ch in word:
                if fits(cut + ch):
                    cut += ch
                else:
                    break
            lines.append(cut)
            word = word[len(cut):].lstrip()
        cur = word
    if cur:
        lines.append(cur)
    if len(lines) > MAX_NAME_LINES:
        lines = lines[:MAX_NAME_LINES]
        last = lines[-1]
        while last and not fits(last + "…"):
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines or [text[:1]]


def decision_of(db: Session, sgtin: str) -> tuple[str | None, float | None]:
    """(решение WB по этому sgtin, fetched_at) из кэша закреплений —
    без сети. Схему кэша читает владелец (journal.identify), guard
    печати остаётся тонким потребителем."""
    from marko.journal.identify import meta_decision
    return meta_decision(db, sgtin)


def prepare(db: Session, raw: str) -> dict:
    """Оркестратор: парсинг → имя → guard → матрица. Превью отдаётся и при
    блокировке (blocked + причина); PDF-роут блок превращает в 422."""
    p = parse_sgtin(raw)
    name, source = name_of(db, p["gtin"], p["km"])
    decision, dts = decision_of(db, p["sgtin"])
    blocked, reason = False, ""
    if decision in BLOCKED_DECISIONS:
        blocked, reason = True, BLOCKED_DECISIONS[decision]
    else:
        it = db.get(Item, p["km"])
        cis = (it.cis_status if it else "") or ""
        if cis in BLOCKED_CIS:
            blocked, reason = True, BLOCKED_CIS[cis]
    size, rows = matrix(gs1_notation(p["sgtin"]))
    return {"sgtin": p["sgtin"], "km": p["km"], "gtin": p["gtin"],
            "serial": p["serial"], "name": name, "name_source": source,
            "lines": wrap_name(name),
            "human": [f"01{p['gtin']}", f"21{p['serial']}"],
            "modules_size": size, "modules": rows,
            "decision": decision, "decision_ts": dts,
            "blocked": blocked, "block_reason": reason}


def build_pdf(p: dict) -> bytes:
    """Этикетка 58×40 мм: имя слева (9pt), DataMatrix 23×23 справа сверху
    (векторные модули), под ним человекочитаемые строки (7pt). Кириллица —
    Roboto (subset встраивается автоматически), печать без сглаживания."""
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
    _ensure_font()
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(LABEL_W_MM * mm, LABEL_H_MM * mm))
    c.setTitle(f"КИЗ {p['gtin']}")
    c.setAuthor("МАРКО")
    c.setFillGray(0)
    c.setFont(FONT_NAME, 9)
    t = c.beginText(2 * mm, 32.5 * mm)             # как в шаблоне WB
    t.setLeading(3.7 * mm)
    for line in p["lines"]:
        t.textLine(line)
    c.drawText(t)
    size = p["modules_size"]
    mod = DM_MM * mm / size                         # сторона модуля, мм
    for yy, row in enumerate(p["modules"]):
        for xx, ch in enumerate(row):
            if ch == "1":
                c.rect(32 * mm + xx * mod, (LABEL_H_MM - 25) * mm
                       + (size - 1 - yy) * mod, mod, mod, stroke=0, fill=1)
    c.setFont(FONT_NAME, 7)
    c.drawString(32 * mm, 12.9 * mm, p["human"][0])
    c.drawString(32 * mm, 10.0 * mm, p["human"][1])
    c.showPage()
    c.save()
    return buf.getvalue()
