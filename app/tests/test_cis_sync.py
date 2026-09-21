"""Синк статусов КИЗ с ЧЗ: колонки cis_*, авто-перевод «вывел WB» с
дискриминатором нашей активной претензии, идемпотентность, ошибки ЧЗ."""
import pytest

from marko.connector_mt import manager
from marko.emitter.batch import withdraw_batch
from marko.journal import apply_event
from marko.journal.cis import sync_cis_status
from marko.journal.models import Event, Item
from marko.mt.models import MtDoc

INN = "090201471350"


class FakeCz:
    """cises_info по словарю km → элемент ответа ЧЗ."""

    def __init__(self, answers: dict):
        self.answers, self.calls = answers, []

    def cises_info(self, token, cises):
        self.calls.append(list(cises))
        return [self.answers[km] for km in cises]


def _sale(db, km, ev="s"):
    apply_event(db, source="wb_excise", source_event_id=f"{ev}:{km}", kind="sale",
                km=km, srid="s", payload={"price": 1, "fiscal_dt": "2026-09-01"})


@pytest.fixture
def mt(db, monkeypatch):
    monkeypatch.setattr(manager, "get_token", lambda d, c=None: "T")
    return manager


def test_retired_translates_to_wb(db, mt):
    km = "0104630520676025215CIS001"
    _sale(db, km)                       # NEW → PENDING_WITHDRAW
    cz = FakeCz({km: {"cisInfo": {"status": "RETIRED", "productName": "Шапка",
                                   "withdrawReason": "DISTANCE"}}})
    res = sync_cis_status(db, client=cz)
    assert res == {"checked": 1, "translated": 1, "errors": 0,
                   "statuses": {"retired": 1}}
    it = db.get(Item, km)
    assert it.state == "WITHDRAWN" and it.withdrawn_by == "wb"
    assert it.cis_status == "retired" and it.cis_product_name == "Шапка"
    assert it.cis_checked_at is not None
    assert db.query(Event).filter_by(source="cz", kind="withdraw").one() \
        .source_event_id == f"cz_retired:{km}"
    # идемпотентность: повторный синк — уже не переводит (state сменился)
    res2 = sync_cis_status(db, client=cz)
    assert res2["translated"] == 0 and res2["checked"] == 1


def test_our_claim_blocks_translation(db, mt):
    """RETIRED после НАШЕГО поданного вывода при незакрытом возврате —
    код ждёт нашего нового вывода, пометка 'wb' была бы ложной (P0 ревью)."""
    km = "0104630520676025215CIS002"
    _sale(db, km)
    doc_id = withdraw_batch(db, INN)
    db.get(MtDoc, doc_id).status = "submitted"; db.commit()
    apply_event(db, source="wb_excise", source_event_id=f"ret:{km}", kind="return",
                km=km, srid="s", payload={"price": 1})     # → PENDING_RETURN
    _sale(db, km, ev="s2")                                  # → PENDING_WITHDRAW
    cz = FakeCz({km: {"cisInfo": {"status": "RETIRED", "productName": "Шапка"}}})
    res = sync_cis_status(db, client=cz)
    assert res["translated"] == 0
    it = db.get(Item, km)
    assert it.state == "PENDING_WITHDRAW" and it.withdrawn_by == "us"
    assert it.cis_status == "retired"      # колонка обновилась, состояние нет


def test_claim_closes_after_submitted_return(db, mt):
    """Поданный LP_RETURN гасит претензию: следующий RETIRED — внешний (WB)."""
    km = "0104630520676025215CIS003"
    _sale(db, km)
    wd = withdraw_batch(db, INN)
    db.get(MtDoc, wd).status = "submitted"
    apply_event(db, source="wb_excise", source_event_id=f"ret:{km}", kind="return",
                km=km, srid="s", payload={"price": 1, "fiscal_dt": "2026-09-02",
                                          "fiscal_doc_number": "5"})
    from marko.emitter.batch import return_batch
    docs, blocked = return_batch(db, INN)
    assert docs == 1 and blocked == 0
    db.query(MtDoc).filter(MtDoc.type == "LP_RETURN").one().status = "submitted"
    _sale(db, km, ev="s3")                                  # перепродажа
    cz = FakeCz({km: {"cisInfo": {"status": "RETIRED"}}})
    assert sync_cis_status(db, client=cz)["translated"] == 1
    assert db.get(Item, km).withdrawn_by == "wb"


def test_withdrawn_us_not_remarked(db, mt):
    """WITHDRAWN/'us' не перепомечаем: наш вывод мог дойти между синками."""
    km = "0104630520676025215CIS004"
    _sale(db, km)
    withdraw_batch(db, INN)                                 # draft, WITHDRAWN/'us'
    cz = FakeCz({km: {"cisInfo": {"status": "RETIRED"}}})
    res = sync_cis_status(db, client=cz)
    assert res["translated"] == 0
    it = db.get(Item, km)
    assert it.state == "WITHDRAWN" and it.withdrawn_by == "us"


def test_error_element_keeps_columns(db, mt):
    km = "0104630520676025215CIS005"
    _sale(db, km)
    ok = FakeCz({km: {"cisInfo": {"status": "INTRODUCED", "productName": "Шапка"}}})
    sync_cis_status(db, client=ok)
    checked_at = db.get(Item, km).cis_checked_at
    assert checked_at is not None
    # «КИ не найден» (HTTP 200 + errorMessage) — позиция не тронута
    bad = FakeCz({km: {"errorMessage": "КИ не найден", "errorCode": "404"}})
    res = sync_cis_status(db, client=bad)
    assert res["errors"] == 1 and res["checked"] == 0
    it = db.get(Item, km)
    assert it.cis_status == "introduced" and it.cis_checked_at == checked_at


def test_length_mismatch_raises(db, mt):
    km = "0104630520676025215CIS006"
    _sale(db, km)

    class Short(FakeCz):
        def cises_info(self, token, cises):
            return []          # ЧЗ потерял элементы — прогон отменяется целиком
    with pytest.raises(RuntimeError):
        sync_cis_status(db, client=Short({}))


def test_explicit_kms_only(db, mt):
    km1, km2 = "0104630520676025215CIS007", "0104630520676025215CIS008"
    _sale(db, km1); _sale(db, km2)
    cz = FakeCz({km1: {"cisInfo": {"status": "INTRODUCED"}}})
    res = sync_cis_status(db, kms=[km1], client=cz)
    assert res["checked"] == 1 and cz.calls == [[km1]]
    assert db.get(Item, km2).cis_status == ""      # не запрошен — не тронут


def test_echo_matching_survives_reorder(db, mt):
    """ЧЗ эхает код (cisInfo.cis): ответ в обратном порядке не должен путать
    статусы — иначе молчаливый ложный перевод «вывел WB» (P1 ревью)."""
    km1, km2 = "0104630520676025215CIS009", "0104630520676025215CIS010"
    _sale(db, km1); _sale(db, km2)

    class Rev(FakeCz):
        def cises_info(self, token, cises):
            return list(reversed([self.answers[km] for km in cises]))
    cz = Rev({km1: {"cisInfo": {"cis": km1, "status": "RETIRED"}},
              km2: {"cisInfo": {"cis": km2, "status": "INTRODUCED"}}})
    sync_cis_status(db, client=cz)
    assert db.get(Item, km1).state == "WITHDRAWN" \
        and db.get(Item, km1).cis_status == "retired"
    assert db.get(Item, km2).state == "PENDING_WITHDRAW" \
        and db.get(Item, km2).cis_status == "introduced"


def test_unknown_kms_return_infos_without_items(db, mt):
    """Явные kms с кодами вне журнала: разовый срез ЧЗ без записи позиций —
    проверка «чужого» кода из трассировки (empty-state «Проверить в ЧЗ»)."""
    km = "0104630520676025215CIS011"
    _sale(db, km)
    other = "0104630520676025215CIS012"
    cz = FakeCz({km: {"cisInfo": {"status": "INTRODUCED"}},
                 other: {"cisInfo": {"cis": other, "status": "RETIRED",
                                      "productName": "Шапка"}}})
    res = sync_cis_status(db, kms=[km, other], client=cz)
    assert res["checked"] == 1 and cz.calls == [[km, other]]
    assert res["infos"] == [{"km": other, "status": "retired",
                             "product_name": "Шапка"}]
    assert db.get(Item, other) is None                  # позиция не создаётся
    assert db.get(Item, km).cis_status == "introduced"  # штатный путь жив
    # без неизвестных кодов ключ infos не появляется (совместимость контракта)
    res2 = sync_cis_status(db, kms=[km], client=FakeCz(
        {km: {"cisInfo": {"status": "INTRODUCED"}}}))
    assert "infos" not in res2
    # поэлементная ошибка ЧЗ по неизвестному коду — информативна, не потеряна
    bad = "0104630520676025215CIS013"
    res3 = sync_cis_status(db, kms=[bad], client=FakeCz(
        {bad: {"errorMessage": "КИ не найден"}}))
    assert res3["infos"] == [{"km": bad, "error": "КИ не найден"}]
