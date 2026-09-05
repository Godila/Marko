"""Воркер-цикл НКМТ: moderation → refresh, signing с notsigned-карточками → sign.

nkmt_cycle строит клиент/токен сам — в тестах патчим marko.worker.NkClient и
manager.get_token; service-функции подменяем записью id в список.
"""
import inspect

import marko.worker as worker
from marko.nkmt.models import Batch, Card


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
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
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


def test_signing_batch_with_error_sign_signed(db, monkeypatch):
    """signing-батч с error_sign-карточкой — повод для повторного подписания."""
    mod, sgn, new = seed_batches(db)
    db.query(Card).filter(Card.batch_id == sgn.id).one().status = "error_sign"
    db.commit()
    calls = patch_service(monkeypatch)
    worker.nkmt_cycle(db)
    assert calls["sign"] == [sgn.id]


def _patch_send(monkeypatch):
    sent = []

    async def fake_send(text):
        sent.append(text)

    monkeypatch.setattr(worker, "send", fake_send)
    return sent


def _set_status(status):
    def effect(db, bid, client, token):
        db.get(Batch, bid).status = status
        db.commit()
    return effect


def test_nkmt_cycle_notifies_on_terminal(db, monkeypatch):
    """Терминальный переход батча (published/error) → TG: id + статус + счёт карточек."""
    mod, sgn, new = seed_batches(db)
    sent = _patch_send(monkeypatch)
    patch_service(monkeypatch, refresh=_set_status("error"))
    worker.nkmt_cycle(db)  # moderation → error: уведомление
    patch_service(monkeypatch, sign=_set_status("published"))
    worker.nkmt_cycle(db)  # sgn signing → published: уведомление (mod уже error)
    assert len(sent) == 2
    assert any(f"батч {mod.id}" in m and "error" in m and "fed=1" in m for m in sent)
    assert any(f"батч {sgn.id}" in m and "published" in m for m in sent)


def test_nkmt_cycle_no_notify_without_transition(db, monkeypatch):
    """Без терминального перехода (moderation → signing / без изменений) — тишина."""
    mod, sgn, new = seed_batches(db)
    sent = _patch_send(monkeypatch)
    patch_service(monkeypatch, refresh=_set_status("signing"))
    worker.nkmt_cycle(db)  # Moderated: signing — не терминальный
    patch_service(monkeypatch)  # refresh/sign без смены статуса
    worker.nkmt_cycle(db)
    assert sent == []


def test_main_runs_nkmt_loop_thread():
    # поток-обвязка main() — бойлерплейт, проверяем ссылкой в исходнике
    assert "_nkmt_loop" in inspect.getsource(worker.main)
