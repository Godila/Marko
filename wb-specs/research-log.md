# Прод-ресерч по живому токену WB — лог

Дата: 2026-09-01. Токен: `secrets/WBtoken.txt`.
Тип (по JWT): **Базовый**, только чтение (бит 30), все категории, до 02.03.2027.
Продавец: ИП Байкулов Д. А. (ИНН 090201471350, ТМ YCPB project).
Лимиты базового токена: excise-report 2 req/24ч (интервал 12ч), sales 1 req/2ч, marketplace 300/мин (4XX = 10 запросов).
Фикстуры: `wb-specs/fixtures/prod/` (seller-info, orders_v3_90d, excise-report, sales).

## Вызовы и результаты

| Метод | HTTP | Результат |
|---|---|---|
| ping × 3 (statistics, marketplace, seller-analytics) | 200 | все сервисы живы |
| `GET /api/v1/seller-info` (common-api) | 200 | продавец идентифицирован |
| `GET /api/v3/warehouses` | 200 | 2+ FBS-склада: «МСК», «Юг» (FBS настроен) |
| `GET /api/v3/orders?next=0&limit=…` (30д и 90д) | 200 | **заказов FBS нет** — продажи на FBS ещё не стартовали |
| `POST /api/v1/analytics/excise-report` (01.06–01.09, RU) | 200 | **1022 строки** (982 op=1, 40 op=2) |
| `GET /api/v1/supplier/sales` (с 30.08) | 200 | 21 строка, все «Склад WB» (FBW) |

Особенности вызова `/api/v3/orders`: рабочая комбинация `?next=0&limit=N` (next обязателен, 0 — старт). `dateFrom/dateTo` в паре с ним в моих вариантах давали 400 IncorrectParameter — на живых FBS-данных пересмотреть (возможно, конфликт с limit). `/api/v3/supplies` тоже вернул 400 — параметры отличаются, проверить отдельно.

## Главные инсайты

1. **excise-report работает и наполнен**: 1022 операции за 3 мес. Все строки — FBW-продажи (FBS нет) → отчёт покрывает обе модели → **для пайплайна обязателен фильтр по FBS** (через sales.warehouseType = «Склад продавца» или membership rid в FBS-заказах). Повторный вывод FBW-кодов продавцом невозможен и вреден (код уже RETIRED — LK_RECEIPT упадёт).
2. **Возвраты в excise есть**: op=2 = 40 строк, с фискальными данными чека возврата.
3. **Связка продажа↔возврат — только по коду**: у возврата ВСЕГДА другой srid (0/50 совпадений). Журнал строить по ключу КМ (код), srid — атрибут события.
4. **excise_short = короткий КМ, 31 символ** (`01`+GTIN+`21`+серийник), без GS-разделителей и криптохвоста. → Для ЧЗ: либо ЧЗ принимает короткий cis (проверить на тест-стенде), либо брать полный код с криптохвостом из `orders/meta` (sgtin, закреплённый фулфилментом, — WB с 03.06.2026 требует только полный код). Рекомендация каркаса: первичный источник кода — meta-слепок, excise — реестр операций.
5. **Хронология событий нетривиальна**: пример «возврат 04.06 → продажа 09.06» (перепродажа после возврата; первая продажа была до окна). Паттерны на код: (1,2)×23, (1,1)×21, (1,2,2)×2, (1,1,2)×2 и др. → окно поллинга с запасом; повторная op=1 по выведенному коду — аномалия для FBS (в FBW так бывает из-за ретейла WB).
6. **srid**: 99% новый формат `eXX.<uuid32>.N.M`; rid в excise-строках нет. Фискальные данные есть у 87% строк (133 продажи без чека — дозаполнение/особенности).
7. `price` — рубли с НДС → `product_cost = round(price × 100)` (копейки).

## Открытые вопросы
1. Допустимость короткого cis (31 симв.) в LK_RECEIPT/LP_RETURN — проверить на тест-контуре ЧЗ (`markirovka.sandbox.crptech.ru`).
2. Появление строк excise после выкупа FBS: задержка неизвестна (нет FBS-данных). Настроить days_back=7 и мониторинг.
3. orders/meta и orders/status на живых FBS-заказах — не проверено (заказов нет).
4. Параметры `/api/v3/supplies` (400) — уточнить при реализации каркаса.

## Израсходованные дорогие вызовы (на 2026-09-01)
- excise-report: 1 из 2/24ч. sales: 1 из 1/2ч. Следующий excise — не раньше чем через 12ч (интервал).

---

# Дополнение: полный прогон песочницы (2026-09-01, тестовый токен)

Токен: `secrets/WBtoken-test.txt` (тестовый, acc=2, до 03.03.2027). Скрипт: `scripts/sandbox_scenario.py`.
Результат: **весь жизненный цикл FBS пройден**: склад → карточка → остатки → 2 тест-заказа → поставка → закрепление КМ → доставка → close → ПВЗ → выкуп (wbStatus=sold) и отказ (canceled_by_client). Фикстуры: `wb-specs/fixtures/sandbox/`.

## Порядок операций (критично!)
1. `POST /api/v3/warehouses` {name, officeId из GET /api/v3/offices} — песочница пустая, склад создаём сами.
2. Карточка: `POST /content/v2/cards/upload` — формат вложенный: `[{subjectID, variants:[{vendorCode, brand, kizMarked:true, sizes:[{techSize, price, skus:[EAN13}], characteristics:[{id,value}]}]}]}`. Цена задаётся прямо в sizes (отдельный prices-API не обязателен). Список: `POST /content/v2/get/cards/list` {settings:{filter:{},cursor:{},limit}} (cursor — объект!).
3. Остатки: **PUT** `/api/v3/stocks/{warehouseId}` {stocks:[{chrtId, amount}]} → 204. (POST → 400 IncorrectRequest.)
4. Заказы: `POST /api/v3/test/fbs/orders/make` {orders:[{sku=баркод, amount:1}]}. Одинаковые позиции одной корзины схлопываются в одно задание → для N заданий N вызовов. Новое задание видно в `GET /api/v3/orders/new?next=0`.
5. Поставка: `POST /api/v3/supplies` {name} → id; `PATCH /api/marketplace/v3/supplies/{id}/orders` **{orders:[ids]}** (объект, не массив!) → 204; `PATCH /api/v3/supplies/{id}/deliver` → 204.
6. **sgtin только после добавления к поставке** (статус confirm): `PUT /api/v3/orders/{id}/meta/sgtin` {sgtins:[полный КМ]} → 204. Раньше — 409 FailedToUpdateMeta «order must be in Processing».
7. Эмуляция WB: `PATCH /api/v3/test/fbs/supplies/{id}/close` (sorted) → `PATCH /api/v3/test/fbs/orders/{id}/deliver` (ready_for_pickup) → `.../receive` (sold) / `.../reject` (canceled_by_client). Пропуск close/deliver → 409 StatusMismatch.

## Наблюдения
- `orders/new`: `price` в КОПЕЙКАХ (150000 = 1500 руб); `optionalMeta` содержит sgtin (проверка ЧЗ для кабинета не включена — sgtin=optional).
- `orders/meta` в песочнице: `orders[].meta.sgtin.value[]` = полный КМ с GS-разделителями (`\u001d`) и криптохвостом 91…/92… — подходит для `cis` в ЧЗ. Поля decision в песочнице нет (появится при включённой проверке).
- **statistics/sales в песочнице НЕ наполняется из тест-заказов** (за 5+ мин продажа не появилась; только старые сгенерированные строки, все «Склад WB»). excise в песочнице отсутствует. → песочница годится для отладки заказов/статусов/meta/sgtin, но НЕ источников продаж; их проверять на проде.
- Цены: `/api/v2/upload` — 404; `/api/v2/upload/task` c массивом — 400 «expected object: body» (нужен объект-обёртка); не разбирал — цена задаётся в карточке.
- 4XX в песочнице съедает лимит ×10 → после ошибок паузы 4–6 c, ретрай на 429.

---

# Ключевые источники (URL) — зафиксировано перед compact 2026-09-02

**WB портал разработчика (dev.wildberries.ru):**
- Swagger-спеки (за антиботом, после JS-challenge): `/api/swagger/yaml/ru/NN-<section>.yaml`; разделы: 01-general, 02-items (карточки/склады/остатки), 03-orders-fbs, 04-orders-dbw, 05-dbs, 06-in-store-pickup, 07-orders-fbw, 09-communications (claims), 11-analytics, 12-reports (sales/excise/goods-return), 13-finances
- `/knowledge-base/articles/019e9273-118b-7b69-a25a-ea1d756f05d9/rabota-s-markirovkoi-po-modeli-fbs` — идентификаторы маркировки и статусы
- `/sandbox` и `/docs/openapi-other/sandbox-environment` — тестовый контур и test-методы эмуляции FBS
- `/news/317/daidzhest-wb-api-mai-2026`, `/news/324/wb-api-digest-june-2026` — проверка кодов маркировки с 03.06.2026
- `/docs/openapi/api-information` — типы/категории токенов, битовая маска s

**WB справочный центр продавца (seller.wildberries.ru/instructions/ru/ru/material/):**
- `how-to-sell-cim-labeled-items` — «Модель Маркетплейс: выводите КИЗ из оборота сами; FBW — автоматически»
- `items-labeling-in-fbs` — «вывод из оборота вместе с фактической отгрузкой; при возврате — вернуть в оборот»
- `mandatory-labeling-items-report` — отчёт «какие из кодов были проданы»
- `how-to-sale-labeled-items-to-legal-entities-and-sole-proprietors` — B2B-вариант

**ЦРПТ / Честный знак (markirovka.ru — официальный портал сообщества):**
- `knowledge/tovarnye-gruppy/upakovannaya-voda/distantsionnaya-torgovlya-upakovannoy-vodoy-na-marketpleysakh-skhemy-fbo-fbs` — каноническая схема FBS (комментарии = официальные ответы ЦРПТ: 3 раб. дня, возврат «по аналогичной причине»)
- `knowledge/tovarnye-gruppy/obschie-voprosy-gis/skhema-raboty-distantsionnoy-torgovli-pri-razreshitelnom-rezhime`
- `knowledge/tovarnye-gruppy/upakovannaya-voda/onlayn-torgovlya-internet-magazin-vyvod-iz-oborota-voda` (п.92 ПП 841)
- `knowledge/tovarnye-gruppy/legkaya-promishlennost/vozvrat-v-oborot-legprom`, `.../kak-osushchestvit-vozvrat-tovara-kotoryy-byl-otgruzhen-marketpleysu`

**True API ГИС МТ:** `https://docs.crpt.ru/gismt/True_API/` (публична!): разделы «Вывод из оборота» (4.1.8, LK_RECEIPT), «Возврат в оборот» (4.1.5, LP_RETURN), «Единый метод создания документов» (4.1.1), справочники «Причины выбытия»/«Причины возврата...»; полный дамп: wb-specs/trueapi-page-all.txt. Auth по УКЭП: habr.com/ru/articles/721622/

**Артефакты сессии (не терять):** PLAN-wb-skeleton.md; docs/superpowers/specs/2026-09-02-mp-gis-mt-platform-design-draft.md (DRAFT, блок 3 не согласован); scripts/sandbox_scenario.py; secrets/WBtoken.txt (базовый прод, read-only, до 02.03.2027), secrets/WBtoken-test.txt (тестовый, до 03.03.2027, только *-sandbox); фикстуры wb-specs/fixtures/{prod,sandbox}/.
