"""Стейт-машина журнала КМ.

Журнал — очередь обязательств, не хроника ЧЗ/WB. Перепродажа возврата —
штатный цикл WB FBS (возврат/невыкуп → автовозврат продавцу → WB перевыставляет
единицу → повторная продажа), поэтому обязательство вывода идемпотентно
продаже: «к выводу» остаётся «к выводу» (свежий чек уходит в last_event),
«выведенный» остаётся «выведенным» — двойной вывод ЧЗ не допускает, а подачу
прикрывают пре-флят cises/info и wb_withdraw_guard. Аномалии — только события,
чью причину автоматика восстановить не может: возврат без известной продажи,
повторный возврат, прочие неизвестные пары.
"""

RULES = {
    ("NEW", "sale"): "PENDING_WITHDRAW",
    ("NEW", "return"): "ANOMALY_NO_RECEIPT",
    ("PENDING_WITHDRAW", "sale"): "PENDING_WITHDRAW",   # перепродажа возврата
    ("PENDING_WITHDRAW", "return"): "PENDING_RETURN",
    ("WITHDRAWN", "sale"): "WITHDRAWN",                 # выведенный остаётся выведенным
    ("WITHDRAWN", "return"): "PENDING_RETURN",
    ("PENDING_RETURN", "sale"): "PENDING_WITHDRAW",
    ("PENDING_RETURN", "return"): "ANOMALY_RERETURN",
    ("RETURNED", "sale"): "PENDING_WITHDRAW",
    # emitter-действия (подаётся через journal.log_action, state ставит emitter напрямую)
    ("PENDING_WITHDRAW", "withdraw"): "WITHDRAWN",
    ("PENDING_RETURN", "return_apply"): "RETURNED",
    # клиентский возврат (финансовый след sales R): ЧЗ-операции нет — по
    # непроведённому выводу продажа отменена (код в обороте, товар у
    # продавца) → обязательство снято; после НАШЕГО вывода код выбыл →
    # к возврату в ЧЗ. Вывод WB ('wb') применением не трогают — зона WB.
    ("PENDING_WITHDRAW", "client_return"): "RETURNED",
    ("WITHDRAWN", "client_return"): "PENDING_RETURN",
}

# состояния, которые cis-sync авто-переводит в WITHDRAWN/'wb' при retired в ЧЗ
# без нашей активной претензии: штатные «к выводу» и легаси-ANOMALY_RESALE —
# по построению оба созданы ПОКАЗАНОЙ продажей, retired там = вывод чеком ККТ.
# ANOMALY_UNKNOWN_TRANSITION сюда НЕ входит: он бывает рождён и возвратом
# (например, op=2 поверх легаси-RESALE), где retired — ожидаемое состояние
# «код ждёт возврата в оборот»; аномалии возврата — только человек.
TRANSLATE_ON_RETIRED = ("PENDING_WITHDRAW", "ANOMALY_RESALE")


def transition(state: str, kind: str) -> str:
    return RULES.get((state, kind), "ANOMALY_UNKNOWN_TRANSITION")
