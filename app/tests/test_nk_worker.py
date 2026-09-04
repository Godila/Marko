"""Воркер-цикл НКМТ: moderation → refresh, signing с notsigned-карточками → sign.

nkmt_cycle строит клиент/токен сам — в тестах патчим mpmt.worker.NkClient и
manager.get_token; service-функции подменяем записью id в список.
"""
import inspect

import mpmt.worker as worker
from mpmt.nkmt.models import Batch, Card


def seed_batches(db):
    """moderation-батч, signing-батч с notsigned-карточкой, new-батч (нетронут)."""
    mod = Batch(source_filename="mod.xlsx", status="moderation", feed_id="42")
    sgn = Batch(source_filename="sgn.xlsx", status="signing", feed_id="43")
    new = Batch(source_filename="new.xlsx", status="new")
    db.add_all((mod, sgn, new))
    db.flush()
    db.add(Card(article="MOD", gtin="4630520699970", batch_id=mod.id,
                tnved="6109100000", name="Футболка mod", status="fed"))
    db.add(Card(article="SGN", gtin="4630520699971", batch_id=sgn.id,
                tnved="6109100000", name="Футболка sgn", status="notsigned"))
    db.add(Card(article="NEW", gtin="", batch_id=new.id,
                tnved="6109100000", name="Футболка new", status="ok"))
    db.commit()
    return mod, sgn, new


def patch_service(monkeypatch, refresh=None, sign=None):
    """Заглушки клиента/токена; refresh/sign пишут id в списки (или рейзят)."""
    monkeypatch.setattr(worker, "NkClient", lambda base: object())
    monkeypatch.setattr("mpmt.connector_mt.manager.get_token", lambda _db: "T")
    calls = {"refresh": [], "sign": []}
    monkeypatch.setattr(worker, "refresh_batch",
                        refresh or (lambda db, bid, client, token:
                                    calls["refresh"].append(bid)))
    monkeypatch.setattr(worker, "sign_batch",
                        sign or (lambda db, bid, client, token:
                                 calls["sign"].append(bid)))
    return calls


def test_nkmt_cycle_refreshes_and_signs(db, monkeypatch):
    mod, sgn, new = seed_batches(db)
    calls = patch_service(monkeypatch)
    worker.nkmt_cycle(db)  # не рейзит
    assert calls["refresh"] == [mod.id]  # ровно moderation-батч
    assert calls["sign"] == [sgn.id]     # ровно signing-батч; new не тронут


def test_nkmt_cycle_swallows_exceptions(db, monkeypatch):
    mod, sgn, new = seed_batches(db)

    def boom(db, bid, client, token):
        raise RuntimeError("nk upstream down")

    calls = patch_service(monkeypatch, refresh=boom)
    worker.nkmt_cycle(db)  # исключение refresh не роняет цикл
    assert calls["sign"] == [sgn.id]  # следующий батч всё равно обработан


def test_signing_batch_without_notsigned_skipped(db, monkeypatch):
    mod, sgn, new = seed_batches(db)
    db.query(Card).filter(Card.batch_id == sgn.id).one().status = "published"
    db.commit()  # signing-батч без notsigned — дёргать sign нечем
    calls = patch_service(monkeypatch)
    worker.nkmt_cycle(db)
    assert calls["refresh"] == [mod.id]
    assert calls["sign"] == []


def test_main_runs_nkmt_loop_thread():
    # поток-обвязка main() — бойлерплейт, проверяем ссылкой в исходнике
    assert "_nkmt_loop" in inspect.getsource(worker.main)
