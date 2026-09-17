"""Обогащение реестра деклараций из ЧЗ (rd/list): матчинг номер+дата,
разбор ТНВЭД-списка, «не найдена» честным нулем."""
from marko.nkmt.models import Declaration
from marko.nkmt.rd import enrich_declarations


class FakeRd:
    def __init__(self, documents, errors=None):
        self.documents, self.errors = documents, errors or []
        self.payloads = []

    def rd_list(self, token, documents):
        self.payloads.append(documents)
        return {"documents": self.documents, "errors": self.errors}


def DECL_RD(number="ЕАЭС N RU Д-RU.РА04.В.02095/26", date="2026-05-13"):
    return {"type": "CONFORMITY_DECLARATION", "number": number, "dateFrom": date,
            "dateTo": "2031-05-12", "status": "Действует",
            "productName": "Головные уборы трикотажные для взрослых",
            "productTnved": "6505003000, 6505009000",
            "productTechRegulations": "ТР ТС 017/2011 О безопасности продукции легкой промышленности",
            "applicantProductName": "БАЙКУЛОВ ДИНИСЛАМ АХМАТОВИЧ",
            "applicantProductType": "Индивидуальный предприниматель",
            "manufacturerProductName": "БАЙКУЛОВ ДИНИСЛАМ АХМАТОВИЧ",
            "manufacturerProductType": "Индивидуальный предприниматель"}


def test_enrich_fills_rich_fields(db):
    d = Declaration(doc_number="ЕАЭС N RU Д-RU.РА04.В.02095/26", doc_date="2026-05-13")
    db.add(d); db.commit()
    out = enrich_declarations(db, FakeRd([DECL_RD()]), "T", [d])
    assert out["found"] == 1 and not out["not_found"]
    db.refresh(d)
    assert d.status == "Действует" and d.date_to == "2031-05-12"
    assert d.tnved_list == ["6505003000", "6505009000"]
    assert d.manufacturer == "БАЙКУЛОВ ДИНИСЛАМ АХМАТОВИЧ"
    assert d.techregs.startswith("ТР ТС 017") and d.checked_at is not None


def test_enrich_not_found_keeps_record_clean(db):
    """ЧЗ не знает пару → декларация остаётся без rich-полей, номер в not_found."""
    d = Declaration(doc_number="ХХ-999", doc_date="2026-01-01")
    db.add(d); db.commit()
    out = enrich_declarations(db, FakeRd([]), "T", [d])
    assert out == {"checked": 1, "found": 0, "not_found": ["ХХ-999"], "updated": []}
    db.refresh(d)
    assert d.status == "" and d.tnved_list == [] and d.checked_at is None


def test_enrich_matches_by_number_and_date(db):
    """Один номер с разными датами (uq_doc_pair): обогащается та пара,
    чья дата совпала с dateFrom ответа; регистр номера не важен."""
    d1 = Declaration(doc_number="Д-1", doc_date="2026-01-01")
    d2 = Declaration(doc_number="д-1", doc_date="2027-02-02")   # тот же номер, др. дата
    db.add_all([d1, d2]); db.commit()
    out = enrich_declarations(db, FakeRd([
        {"type": "CONFORMITY_DECLARATION", "number": "Д-1", "dateFrom": "2027-02-02",
         "status": "Действует", "productTnved": "6109100000"}]), "T", [d1, d2])
    assert out["found"] == 1
    db.refresh(d1); db.refresh(d2)
    assert d1.status == "" and d2.status == "Действует" and d2.tnved_list == ["6109100000"]


def test_enrich_certificate_type_mapping(db):
    """doc_type certificate → CONFORMITY_CERTIFICATE в запросе к ЧЗ."""
    d = Declaration(doc_number="С-1", doc_date="2026-03-01", doc_type="certificate")
    db.add(d); db.commit()
    fake = FakeRd([{"type": "CONFORMITY_CERTIFICATE", "number": "С-1",
                    "dateFrom": "2026-03-01", "status": "Действует"}])
    enrich_declarations(db, fake, "T", [d])
    assert fake.payloads[0][0]["type"] == "CONFORMITY_CERTIFICATE"
    assert fake.payloads[0][0]["dateFrom"] == "2026-03-01"
