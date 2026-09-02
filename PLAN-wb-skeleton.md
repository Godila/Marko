# Предварительный план каркаса: WB-сторона (сбор реестра проданных КИЗ)

Проект: MP-GIS_MT · ТГ: Лёгкая промышленность (`lp`), одежда второго слоя · Модель WB: FBS.
Область каркаса: от WB API до готовых батчей документов (заготовки LK_RECEIPT / LP_RETURN).
Сторона ЧЗ (True API + УКЭП) — следующий этап, в каркасе только интерфейс-заглушка.

Дата: 2026-08-31. Источники: спеки в `wb-specs/`, docs.crpt.ru, dev.wildberries.ru (ссылки в чате).

---

## 1. Что даёт Sandbox WB (выводы ресерча)

### Доступно в тестовом контуре
| Категория | Тестовый домен | Что покрывает для нас |
|---|---|---|
| Маркетплейс (FBS) | `marketplace-api-sandbox.wildberries.ru` | заказы, статусы (wbStatus), meta/sgtin, поставки; лимит 1 rps суммарно |
| Статистика | `statistics-api-sandbox.wildberries.ru` | `GET /api/v1/supplier/sales`, `orders`; окно дат −4 месяца |
| Контент | `content-api-sandbox.wildberries.ru` | карточки для тестовых заказов (баркоды) |
| Цены и скидки | `discounts-prices-api-sandbox.wildberries.ru` | цена ≠ 0 нужна для создания тест-заказа |

### Тестовые методы эмуляции (только в песочнице!) — спека `sandbox-environment`
- `POST /api/v3/test/fbs/orders/make` — создать тестовые сборочные задания (до 10; нужны остатки на FBS-складе и цена).
- `PATCH /api/v3/test/fbs/orders/{orderId}/receive` — **эмуляция выкупа** (wbStatus→`sold`)
- `PATCH /api/v3/test/fbs/orders/{orderId}/reject` — отказ в ПВЗ (возврат невыкупа)
- `PATCH /api/v3/test/fbs/orders/{orderId}/deliver` — прибытие в ПВЗ
- `PATCH /api/v3/test/fbs/orders/{orderId}/defect` — брак
- `PATCH /api/v3/test/fbs/orders/{orderId}/decline` — отмена покупателем в 1-й час
- `PATCH /api/v3/test/fbs/supplies/{supplyId}/close` — «WB принял» (sorted)

### Чего в Sandbox НЕТ
`excise-report`, `goods-return` (seller-analytics-api), `claims` (returns-api), реализация (finance-api) — **только прод**. Официальная позиция WB: «тестирование возможно только в боевой среде с Базовым токеном на реальных данных». Все эти методы read-only.

### Токены
- **Тестовый токен**: создаёт ТОЛЬКО владелец ЛК WB Партнёры (организация → «Интеграции по API» → + Создать токен → «Для интеграции вручную» → «Тестовый токен»). Срок 180 дней, все категории песочницы, чтение+запись, показывается один раз. Персональный/Сервисный/Базовый с тестовыми доменами НЕ работают.
- **Для прода (read-only)**: персональный токен с категориями «Маркетплейс», «Статистика», «Аналитика» (метод-маппинг ниже).
- Итого нужно два токена: тестовый (песочница) + персональный (прод, read-only этап).

### Категории и лимиты ключевых методов (прод)
| Метод | Домен | Категория токена | Лимит (персональный) |
|---|---|---|---|
| `POST /api/marketplace/v3/orders/meta` | marketplace-api | Маркетплейс | 300/мин, интервал 200 мс; 4XX = 10 запросов |
| `GET /api/v3/orders`, `/status` | marketplace-api | Маркетплейс | 300/мин |
| `GET /api/v1/supplier/sales` | statistics-api | Статистика | 1/мин |
| `POST /api/v1/analytics/excise-report` | seller-analytics-api | Аналитика | 10 за 5 ч, интервал 30 мин (базовый без секрета: 2/24 ч!) |
| `GET /api/v1/analytics/goods-return` | seller-analytics-api | Аналитика | 1/мин |

---

## 2. Архитектура каркаса

Стек (предложение): Python 3.12, httpx, SQLite (журнал), pydantic (схемы), typer (CLI). Причина: быстрая итерация, готовые фикстуры-тесты; заменяемо.

```
mp_gis_mt/
├── config/
│   ├── settings.py            # стенды, токены из env, расписания, окна дат
│   └── endpoints.yaml         # карта методов → (домен prod, домен sandbox, категория, лимит)
├── wb/
│   ├── client.py              # httpx-обёртка: per-method rate limiter, retry/backoff (429/5xx),
│   │                          # учёт «4XX=10 запросов», логирование requestId
│   ├── excise_report.py       # POST excise-report (окно −7 дней, countries=[RU]), пагинации нет
│   ├── sales.py               # GET supplier/sales (опц. источник событий, flag=0 курсор по lastChangeDate)
│   ├── order_meta.py          # POST marketplace/v3/orders/meta — слепок orderId→sgtin (батчи ≤100)
│   ├── order_status.py        # POST /api/v3/orders/status (батчи ≤1000) — опц.
│   └── goods_return.py        # GET analytics/goods-return (опц., для дат выдачи возвратов)
├── journal/
│   ├── db.py                  # SQLite, WAL
│   ├── events_raw.sql         # сырые строки источников (idempotent upsert)
│   ├── items.sql              # стейт-машина по (srid, kiz)
│   └── runs.sql               # журнал прогонов (окно дат, счётчики, ошибки)
├── engine/
│   ├── state_machine.py       # правила переходов (см. §4)
│   └── reconcile.py           # сшивка источников, детект гонок/аномалий
├── emit/
│   ├── batches.py             # группировка в LK_RECEIPT / LP_RETURN (см. §5)
│   ├── sink.py                # интерфейс Sink: FileSink (этап WB) | TrueApiSink (заглушка, этап 2)
│   └── schemas.py             # pydantic-модели тел документов (по докам True API)
├── sandbox/
│   ├── seed.py                # подготовка данных: карточка (content-sandbox) → цена (discounts-sandbox)
│   │                          # → остаток (marketplace-sandbox) → make-заказы
│   └── lifecycle.py           # прогон: make → sgtin attach → supply → deliver → receive/reject
├── cli.py                     # команды: poll / snapshot-meta / emit / scenario / status / replay
└── tests/
    ├── fixtures/excise/*.json # записи реальных ответов (контрактные тесты)
    └── ...
```

Ключевые решения:
- **Один источник истины по операциям — excise-report** (есть и код, и тип операции, и чек); sales/status — только опережающие триггеры и самопроверка, отключаемы конфигом.
- **Всё read-only**: каркас ничего не пишет в WB (закрепление sgtin — зона фулфилмента).
- **Recorded mode**: каждый реальный ответ WB сохраняется в fixtures — тесты против воспроизведённых данных (компенсирует отсутствие excise в песочнице).
- **Идемпотентность**: upsert по натуральному ключу события; повторный прогон окна ничего не дублирует.

## 3. Конфигурация стендов

```yaml
target: sandbox | prod
windows:
  excise: { days_back: 7 }          # перекрытие для дозаполняющихся строк
  sales:  { minutes_back: 120, cursor_field: lastChangeDate }
schedule:
  excise_poll_cron: "30 6,18 * * *" # 2 раза в день (лимит 10/5ч позволяет чаще)
emit:
  withdraw: { action: DISTANCE, batch_max: 2000 }   # один LK_RECEIPT на батч
  return:   { return_type: REMOTE_SALE_RETURN, paid: true, primary_doc_per_item: true }
```

## 4. Журнал и стейт-машина

`items` — по ключу (`srid`, `kiz`):
```
state: IN_STOCK → SOLD_PENDING  → WITHDRAWN  → RETURN_PENDING → REINTRODUCED → (может снова SOLD_PENDING)
event_log: (ts, source, operation, payload_hash)
```

Правила:
1. excise `operation_type_id=1` (продано): если `IN_STOCK/REINTRODUCED` → SOLD_PENDING (в очередь на вывод). Если уже WITHDRAWN — дубликат, пропуск (идемпотентность).
2. excise `operation_type_id=2` (возврат): если WITHDRAWN → RETURN_PENDING (в очередь на возврат). Если IN_STOCK (не выводили, невыкуп) → **no-op**, пометка `returned_unsold`.
3. Гонка «выкуп и возврат в одном окне»: обрабатывать в хронологии появления строк; возврат до вывода блокирует вывод (пере-проверка статусов при эмите).
4. Цена `product_cost = price × 100` (копейки, из excise `price`); `paid=true` при возврате (всегда для возврата после выкупа).
5. Первичка: вывод — опционально (`RECEIPT` из `fiscal_*` excise-строки); возврат — обязательна при paid=true, на уровне items (у каждого возврата свой чек).

Аномалии в отдельный отчёт: код не найден в слепке meta; строка без fiscal-данных; двойная продажа одного kiz; возврат без предшествующей продажи.

## 5. Выходные батчи (заготовки документов ЧЗ)

- **LK_RECEIPT**: `{inn, action: "DISTANCE", action_date: <дата выкупа/чека>, products: [{cis: <полный КМ>, product_cost: <копейки>}]}` — один документ на прогон (все коды одной ТГ и причины).
- **LP_RETURN**: `{trade_participant_inn, return_type: "REMOTE_SALE_RETURN", paid: true, products_list: [{ki, primary_document_type: "RECEIPT", primary_document_number: <fiscal_doc_number>, primary_document_date: <fiscal_dt>}]}`.
- FileSink пишет `out/YYYY-MM-DD/withdraw.json` / `return.json` + манифест (счётчики, srid-список). TrueApiSink (этап 2): подпись УКЭП → `POST /lk/documents/create?pg=lp` → `GET /doc/{id}/info`.

## 6. Тестовая стратегия

| Что | Где | Как |
|---|---|---|
| HTTP-клиент, лимиты, retry | unit + mock | virtual clock, генерация 429 |
| Жизненный цикл FBS-заказа, meta/sgtin, статусы | **Sandbox** | `sandbox/lifecycle.py` сценарий с test-методами |
| excise/goods-return | прод read-only + fixtures | Recorded mode (1 прогон прод-токеном → fixtures → контрактные тесты) |
| Стейт-машина, эмиттер | unit | синтетические последовательности (продажа/возврат/невыкуп/гонка/дубль) |

Сценарий песочницы (порядок): карточка в content-sandbox → цена в discounts-sandbox → остаток на FBS-складе → `POST /api/v3/test/fbs/orders/make` → закрепить sgtin (в песочнице необязательно, но отработаем API) → добавить в поставку → deliver → `receive` (выкуп) и параллельный кейс `reject` → снять `orders/status`, `meta`, `supplier/sales` (sandbox-statistics).

## 7. Фазы

- **Ф0 Доступы** (владелец ЛК): тестовый токен; персональный прод-токен (категории Маркетплейс/Статистика/Аналитика).
- **Ф1 Каркас клиента + песочница**: endpoints.yaml, клиент с лимитами, lifecycle-сценарий зелёный.
- **Ф2 Журнал + стейт-машина**: на синтетике и записях песочницы.
- **Ф3 Прод read-only**: первый excise-прогон (fixtures), сверка с ЛК-отчётом продавца; snapshot-meta заказов.
- **Ф4 Эмиттер**: батчи LK_RECEIPT/LP_RETURN → FileSink; ручная сверка полей с доками True API.
- **Ф5 (след. этап, ЧЗ)**: TrueApiSink: auth/key→simpleSignIn, подписание УКЭП, create, doc status, ретраи, финальная стейт-машина.

## 8. Открытые вопросы (не блокируют Ф1–Ф4)
1. Язык/стек окончательно? (предложен Python; если прод-рантайм будет 1С/C# — каркас останется референсом).
2. Как часто строки excise дозаполняются чеками — уточнить на проде (влияет на days_back).
3. Нужен ли в журналеoods-return/claims как источники дат выдачи возвратов (для контроля физического возврата до эмита LP_RETURN) — решить на Ф3 по реальным данным.
4. Полный КМ с криптохвостом из excise (`excise_short`) — проверить на прод-данных, что формат совпадает с `cis` ЧЗ (GS-разделители `\u001d`).
