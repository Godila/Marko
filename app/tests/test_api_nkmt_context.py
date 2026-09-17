"""GET /v1/nkmt/context — self-describing контур НК для внешних агентов:
колонки шаблона, приоритет подстановок, дефолты, правила, справочники."""
from tests.test_api_nkmt_dicts import AUTH, AUTH_RO, DECL, client  # noqa: F401


def test_context_structure(db, client):
    from marko.nkmt.models import BrandCache, Producer, Rule
    from marko.nkmt.parse import SPEC
    from marko.platform.models import PlatformKV
    db.add(PlatformKV(key="nk_attrs:6109100000", value={"m": [
        {"attr_id": 12, "attr_name": "Вид товара",
         "attr_preset": ["ФУТБОЛКА", "ШАПКА"]}], "r": []}))
    db.add(BrandCache(name="ycpb", brand_id=2102811))
    db.add(Producer(name="ИП Байкулов Д. А.", inn="090201471350", kind="entrepreneur"))
    decl = client.post("/v1/nkmt/declarations", headers=AUTH,
                       json={**DECL, "title": "Лёгпром до 2027"}).json()
    db.add(Rule(brand="", product_types=["ШАПКА"], declaration_id=decl["id"],
                fields={"size": "ONE SIZE"}))
    db.commit()
    r = client.get("/v1/nkmt/context", headers=AUTH_RO)
    assert r.status_code == 200
    ctx = r.json()
    assert ctx["priority"] == ["file", "rule", "default"]
    # колонки шаблона = SPEC: единый источник, флаги rule_field согласованы
    assert [c["title"] for c in ctx["template"]["columns"]] == [s.title for s in SPEC]
    by_key = {c["key"]: c for c in ctx["template"]["columns"]}
    assert by_key["article"]["required"] is True
    assert by_key["size"]["rule_field"] is True
    assert by_key["tnved"]["rule_field"] is True    # маппинг «изделие → ТН ВЭД»
    assert by_key["producer"]["defaultable"] is True
    assert ctx["template"]["download"] == "/v1/nkmt/import/template"
    # дефолты и правила с реквизитами декларации + fields
    assert ctx["defaults"]["brand"] == "YCPB"
    rule = ctx["rules"][0]
    assert rule["fields"] == {"size": "ONE SIZE"}
    assert rule["declaration_title"] == "Лёгпром до 2027"
    assert rule["declaration_number"] == DECL["doc_number"]
    # справочники и карта эндпоинтов для агента
    assert "ШАПКА" in ctx["dicts"]["product_types"]
    assert ctx["dicts"]["producers"] == ["ИП Байкулов Д. А."]
    assert set(ctx["endpoints"]) >= {"preview", "import", "resolve_check"}
    assert ctx["generated_at"]


def test_context_read_scope_enough(db, client):
    assert client.get("/v1/nkmt/context").status_code == 401
