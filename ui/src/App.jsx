import React, { useCallback, useEffect, useRef, useState } from 'react'
import './marko.css'

/* ================= http ================= */
const errOf = async (r) => {
  let m = `${r.status}`
  try { const j = await r.json(); if (j && j.detail != null)
    m += ': ' + (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) } catch {}
  return new Error(m)
}
// сессия живёт в HttpOnly-cookie — браузер прикладывает её сам (same-origin).
// 401 в любом вызове — единое событие: App вернёт оператора на экран входа.
const send = async (path, opts = {}) => {
  const r = await fetch(path, opts)
  if (r.status === 401) window.dispatchEvent(new Event('marko:unauthorized'))
  if (!r.ok) throw await errOf(r)
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text()
}
// JSON-телу ставим Content-Type; FormData (импорт НКМТ) — нет, boundary ставит браузер
const api = (path, opts = {}) => send(path, { ...opts,
  headers: opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' } })
const dl = async (path, name) => {   // скачивание через cookie-сессию, не через URL
  const r = await fetch(path)
  if (r.status === 401) window.dispatchEvent(new Event('marko:unauthorized'))
  if (!r.ok) throw await errOf(r)
  const b = await r.blob(); const u = URL.createObjectURL(b)
  const a = document.createElement('a'); a.href = u; a.download = name; a.click()
  URL.revokeObjectURL(u)
}

/* ================= словари статусов ================= */
const ITEM_STATES = {
  NEW: ['новый', 'grey'], PENDING_WITHDRAW: ['к выводу', 'amber'], WITHDRAWN: ['выведен', 'green'],
  PENDING_RETURN: ['к возврату', 'blue'], RETURNED: ['возвращён', 'green'],
  ANOMALY_NO_RECEIPT: ['аномалия: возврат без продажи', 'red'], ANOMALY_RESALE: ['аномалия: вторичная продажа', 'red'],
  ANOMALY_RERETURN: ['аномалия: повторный возврат', 'red'],
  ANOMALY_UNKNOWN_TRANSITION: ['аномалия: неопознанное событие', 'red'],
}
const CHIP_ORDER = ['PENDING_WITHDRAW', 'PENDING_RETURN', 'WITHDRAWN', 'RETURNED',
  'ANOMALY_RESALE', 'ANOMALY_NO_RECEIPT', 'ANOMALY_RERETURN', 'ANOMALY_UNKNOWN_TRANSITION',
  'NEW']
// статус КИЗ по данным Честного ЗНАКа (cises/info); пусто → «—» (не проверялся)
const CIS_STATUS = { introduced: ['в обороте', 'blue'], in_circulation: ['в обороте', 'blue'],
  retired: ['выбыл', 'green'], written_off: ['списан', 'grey'] }
// состояние lookup заказа WB (журнал): пояснение для пустых/пограничных случаев
const ORDER_LOOKUP_STATUS = {
  found: 'Коды маркировки по заказу найдены в журнале.',
  lag: 'Заказ есть в реестре WB, но строки продаж по нему в журнал ещё не приходили. WB отдаёт их с лагом до ~2 недель — проверьте позже.',
  fbw: 'Заказ вне контура FBS: строки по нему приходили, но в журнал не попадают (FBW-остатки на складе WB).',
  unknown: 'Такого заказа нет ни в реестре WB, ни в журнале. Проверьте номер — возможно, это заказ другого кабинета или опечатка.',
}
const DOC_STATUS = { draft: ['черновик', 'grey'], signing: ['подписывается', 'blue'],
  submitted: ['подан', 'blue'], checked_ok: ['принят ЧЗ', 'green'], error: ['ошибка', 'red'] }
const CARD_STATUS = { ok: ['новая', 'grey'], fed: ['подана', 'blue'], moderation: ['модерация', 'amber'],
  notsigned: ['ждёт подписи', 'amber'], signing: ['подписывается', 'blue'], published: ['опубликована', 'green'],
  error: ['ошибка', 'red'], errors: ['ошибки', 'red'], error_sign: ['ошибка подписи', 'red'] }
const BATCH_STATUS = { new: ['новый', 'grey'], partial: ['частично', 'amber'], feeding: ['подача', 'blue'],
  moderation: ['модерация', 'amber'], signing: ['подпись', 'blue'], published: ['опубликован', 'green'],
  error: ['ошибка', 'red'] }
const GTIN_STATUS = { new: ['новый', 'grey'], update: ['обновится', 'blue'], conflict: ['конфликт', 'red'] }
const SRC_RU = { file: 'файл', rule: 'правило', default: 'дефолт' }
const WB_DELIVERY = { fbs: 'FBS (наша отгрузка)', fbo: 'FBW (склад WB)' }
// трассировка: система-источник события (бейдж) и цвет точки ленты по виду
// события. Система ≠ статус: цвета — из базовой пятёрки, красный системам
// не выдаётся (DESIGN.md §8: красное = требует человека)
const TRACE_SYSTEMS = { marko: ['МАРКО', 'grey'], cz: ['Честный знак', 'blue'], wb: ['Wildberries', 'amber'] }
const TRACE_DOT = {
  sale: 'var(--info)', return: 'var(--wait)', skip_fbw: 'var(--line-strong)',
  withdraw: 'var(--go)', return_apply: 'var(--go)',
  resolve: 'var(--line-strong)', revert: 'var(--line-strong)',
}
// статусы проверки sgtin на WB (orders/meta, официальная документация WB);
// неизвестное решение показывается как есть (mono) — словарь не молчаливый
const SGTIN_DECISION = {
  filled: 'закреплён, проверка не требуется', optional: 'не закреплён (необязателен)',
  deadlineExceeded: 'проверка не завершена', sgtinIntroduced: 'введён в оборот — допущен к продаже',
  sgtinSoldB2B: 'продан B2B, допущен повторно', required: 'обязателен, но не закреплён',
  pending: 'проверка продолжается', sgtinInvalidFormat: 'неверный формат кода',
  sgtinNoGS: 'нет GS-разделителя', sgtinHasInvalidSymbols: 'недопустимые символы',
  sgtinHasNonLatinSymbols: 'не-латинские символы', sgtinInvalidPattern: 'неверная структура кода',
  sgtinNotFound: 'не найден в Честном знаке', sgtinEmitted: 'выпущен, не введён в оборот',
  sgtinApplied: 'нанесён, не введён в оборот', sgtinWrittenOff: 'списан',
  sgtinRetired: 'уже продан (выбыл)', sgtinDisaggregated: 'агрегация снята',
  sgtinAppliedNotPaid: 'заказ на код не оплачен',
}
// подписи источников в «Проверке подстановок»: бренд оператора семантически = значение из файла
const RZ_SRC = { file: 'введено', rule: 'правило РД', default: 'дефолт' }
// ключ стадии пайплайна → какие batch-статусы она покрывает
const STAGES = [
  { key: '', l: 'все батчи', match: null },
  { key: 'new', l: 'новый', match: ['new'] },
  { key: 'feeding', l: 'подача', match: ['partial', 'feeding'] },
  { key: 'moderation', l: 'модерация', match: ['moderation'] },
  { key: 'signing', l: 'подпись', match: ['signing'] },
  { key: 'published', l: 'опубликован', match: ['published'] },
  { key: 'error', l: 'ошибка', match: ['error'] },
]
const DEF_FIELDS = [
  ['brand', 'Бренд'], ['product_type', 'Вид товара'], ['techreg', 'Техрегламент'],
  ['target_gender', 'Пол'], ['size_system', 'Система размеров'], ['country', 'Страна'],
  ['producer', 'Производитель'], ['declaration_number', 'Номер декларации'],
  ['declaration_date', 'Дата декларации'], ['size', 'Размер']]
// дополнительные поля, которые правило РД может подставить в пустые ячейки
// (зеркало бэкенд-константы RULE_FIELDS; ТН ВЭД — маппинг «изделие → код»,
// размер — общая для дефолта и правила)
const RULE_FIELD_LABELS = { tnved: 'ТН ВЭД', size: 'Размер', color: 'Цвет',
  composition: 'Состав', model: 'Модель/артикул', target_gender: 'Пол',
  size_system: 'Система размеров', country: 'Страна (ISO-код)' }
// порядок показа «Проверки подстановок»: дефолты + правило-поля без дефолтов
const RZ_FIELDS = [...DEF_FIELDS.filter(([k]) => k !== 'techreg'),
  ['color', 'Цвет'], ['composition', 'Состав'], ['model', 'Модель/артикул'],
  ['techreg', 'Техрегламент']]
// состояние декларации по данным ЧЗ (rd/list): производный статус для бейджа
const declState = (d) => {
  if (!d.checked_at && !d.status) return ['не проверялась', 'grey']
  // негативные статусы ЧЗ содержат «действ» («Не действует», «Истёк срок
  // действия») — проверяем их ДО позитивного теста
  if (/не\s*действ|истёк|истек|аннулир|прекращ/i.test(d.status || ''))
    return [d.status, 'red']
  if (!/действ/i.test(d.status || '')) return [d.status || 'нет данных', 'red']
  const days = d.date_to ? (new Date(d.date_to) - new Date()) / 864e5 : null
  if (days != null && days < 0) return ['не действует', 'red']
  if (days != null && days <= 60) return [`истекает ${d.date_to.split('-').reverse().join('.')}`, 'amber']
  return ['действует', 'green']
}
// эксайз-payload не несёт kind — вид события выводим из operation_type_id
const opRu = (ev) => ev.operation_type_id === 2 ? 'возврат'
  : ev.operation_type_id === 1 ? 'продажа' : '—'
// карточки разбора аномалий: что это / почему бывает / что делать + пресеты
// ручного разрешения (target — штатное состояние, новых статусов не вводим)
const ANOMALY_HELP = {
  ANOMALY_NO_RECEIPT: {
    what: 'WB сообщил возврат этого кода, но продажу его мы не видели. Вернуть непроданное физически невозможно — значит, событие продажи до нас не дошло.',
    why: ['продажа была до запуска контура (до сентября 2026);', 'строка продажи приехала позже 7-дневного окна опроса WB;', 'код ввели в оборот и продали мимо нашей интеграции.'],
    todo: 'Если товар реально продавался и вернулся — переведите код «к возврату»: LP_RETURN вернёт его в оборот. Если код не ваш или строки в ЛК WB нет — признайте строку ошибочной.',
    presets: [
      { label: 'Продажа была до запуска', target: 'PENDING_RETURN', note: 'продажа до запуска контура' },
      { label: 'Строка ошибочна', target: 'NEW', note: 'возврат ошибочен: дубль или чужая строка' },
    ],
  },
  ANOMALY_RESALE: {
    what: 'Код продан второй раз, пока первая продажа ждала вывода из оборота. Чаще всего это не подделка, а порядок событий: возврат покупателя приезжает в отчётах WB на 0–2 дня позже продажи.',
    why: ['возврат первой продажи ещё в пути (лаг отчёта WB 0–2 дня);', 'WB отдал ту же продажу повторно под другим номером;', 'редко: реальная переклейка кода — это вопрос к ЧЗ, не к «разобрать».'],
    todo: 'Проверьте код в карточке товара на WB: если это та же единица после возврата покупателя — примите событие, код снова уйдёт «к выводу». При подозрении на переклейку не разрешайте — разберите с ЧЗ.',
    presets: [
      { label: 'Принять перепродажу', target: 'PENDING_WITHDRAW', note: 'перепродажа принята: возврат в пути или дубль WB' },
    ],
  },
  ANOMALY_RERETURN: {
    what: 'Возврат этого кода пришёл дважды. Единица товара не может вернуться с ПВЗ два раза — почти всегда это дубль строки в отчёте WB.',
    why: ['WB отдал возврат повторно под новым номером документа;', 'повторная отдача после «зависшего» возврата без чека.'],
    todo: 'Обычно достаточно признать дубль — код останется «к возврату», документ LP_RETURN построится по нему один раз.',
    presets: [
      { label: 'Признать дублем', target: 'PENDING_RETURN', note: 'повторный возврат — дубль WB' },
    ],
  },
  ANOMALY_UNKNOWN_TRANSITION: {
    what: 'Событие пришло в состоянии, где машина состояний не знает, что с ним делать: например, продажа уже выведенного кода или событие поверх другой аномалии. Нужен взгляд человека.',
    why: ['продажа/возврат приехали после того, как код уже выведен или возвращён;', 'событие поверх неразобранной аномалии — исходная причина затёрта, история осталась в таблице событий.'],
    todo: 'Сверьте судьбу кода в ЛК ЧЗ (в обороте / выведен / выбыл) и выберите корректное состояние вручную.',
    presets: [
      { label: 'К выводу', target: 'PENDING_WITHDRAW', note: 'ручное решение: ожидает вывода' },
      { label: 'К возврату', target: 'PENDING_RETURN', note: 'ручное решение: ожидает возврата' },
      { label: 'Уже выведен', target: 'WITHDRAWN', note: 'ручное решение: событие проигнорировано' },
    ],
  },
}

/* ================= формат ================= */
const pad2 = (n) => String(n).padStart(2, '0')
// БД/бэкенд хранят naive-UTC; без суффикса зоны браузер считает строку
// локальным временем — приписываем Z, показываем в зоне пользователя (МСК)
const parseUtc = (s) => (typeof s === 'string' && /Z|[+-]\d\d:?\d\d$/.test(s) || s instanceof Date
  ? new Date(s) : new Date(s + 'Z'))
const fmtD = (s) => { if (!s) return '—'; const d = parseUtc(s); return isNaN(d) ? s :
  `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}` }
const fmtAgo = (ts) => { if (!ts) return '—'; const m = Math.round((Date.now() / 1000 - ts) / 60)
  if (m < 1) return 'только что'; if (m < 60) return `${m} мин назад`
  const h = Math.round(m / 60); if (h < 36) return `${h} ч назад`; return `${Math.round(h / 24)} дн назад` }
const fmtLeft = (iso) => { const ms = parseUtc(iso) - Date.now(); if (isNaN(ms)) return ''
  if (ms <= 0) return 'просрочен'; const h = ms / 36e5
  if (h >= 48) return `${Math.round(h / 24)} дн`; if (h >= 1) return `${Math.round(h)} ч`
  return `${Math.max(1, Math.round(ms / 6e4))} мин` }
const leftCls = (iso) => { const ms = parseUtc(iso) - Date.now()
  return ms <= 0 ? 'over' : ms <= 864e5 ? 'danger' : ms <= 1728e5 ? 'warn' : 'ok' }
const plural = (n, [one, few, many]) => { const a = n % 10, b = n % 100
  return b >= 11 && b <= 14 ? many : a === 1 ? one : a >= 2 && a <= 4 ? few : many }
const rub = (n) => `${Number(n || 0).toLocaleString('ru-RU')} ₽`
const fmtDay = (s) => {   // date-only fiscal_dt: без времени (UTC-полночь не показывать как 03:00)
  if (!s) return '—'
  if (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s)) return `${s.slice(8, 10)}.${s.slice(5, 7)}`
  return fmtD(s) }
const evLine = (it) => { const ev = it.last_event || {}
  const base = opRu(ev)
  return base + (ev.fiscal_dt ? ` · ${fmtDay(ev.fiscal_dt)}` : '')
    + (ev.fiscal_doc_number ? ` · чек ${ev.fiscal_doc_number}` : '')
    + (ev.price ? ` · ${rub(ev.price)}` : '') }

/* ================= иконки ================= */
// Знак МАРКО «Матрица-М»: L-искатель DataMatrix + «М» из модулей
// (концепция и лист вариантов — ui/logo-marko.html)
const M_ROWS = ['X...X', 'XX.XX', 'X.X.X', 'X...X', 'X...X']
const markSvg = (size, fg, accent) => {
  const pad = 0.5, total = 9 + pad * 2, u = size / total
  const px = (n) => ((pad + n) * u).toFixed(2)
  const cell = ([c, r], fill) => `<rect x="${px(c)}" y="${px(r)}" width="${u.toFixed(2)}" height="${u.toFixed(2)}" fill="${fill}"/>`
  const finder = `<rect x="${px(0)}" y="${px(0)}" width="${u.toFixed(2)}" height="${(9 * u).toFixed(2)}" fill="${fg}"/>` +
    `<rect x="${px(0)}" y="${px(8)}" width="${(9 * u).toFixed(2)}" height="${u.toFixed(2)}" fill="${fg}"/>`
  const timing = [[2, 0], [4, 0], [6, 0], [8, 2], [8, 4], [8, 6]].map((cr) => cell(cr, fg)).join('')
  const m = M_ROWS.flatMap((row, i) => [...row].map((ch, j) => ch === 'X' ? [2 + j, 2 + i] : null).filter(Boolean)).map((cr) => cell(cr, accent)).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" aria-hidden="true">${finder}${timing}${m}</svg>`
}
const Mark = ({ size = 34, fg = 'var(--rail-ink)', accent = '#E42B47' }) => (
  <span className="seal" style={{ display: 'inline-flex', lineHeight: 0 }}
    dangerouslySetInnerHTML={{ __html: markSvg(size, fg, accent) }} />
)
const I = {
  pulse: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M2 12h4l2.5-6 4 12 2.5-6h7" /></svg>,
  swap: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h13l-3-3M20 17H7l3 3" /></svg>,
  back: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 14L4 9l5-5" /><path d="M4 9h10a6 6 0 0 1 0 12h-3" /></svg>,
  grid: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>,
  book: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>,
  list: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" /></svg>,
  refresh: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" /></svg>,
  search: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>,
  copy: <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>,
  clock: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 3" /></svg>,
}

/* ================= примитивы ================= */
const Badge = ({ dict, v }) => { const [lbl, cls] = dict[v] || [v, 'grey']
  return <span className={`bdg ${cls}`}>{lbl}</span> }

const KmCell = ({ km }) => (
  <span className="km">{km}
    <button title="Скопировать" onClick={(e) => { e.stopPropagation()
      navigator.clipboard?.writeText(km).catch(() => {}) }}>{I.copy}</button></span>)

// раскрытые ell-ячейки: клик/Enter показывает текст целиком; клик не
// открывает карточку строки (stopPropagation против onClick у <tr>)
const EllCell = ({ title, mono, children }) => {
  const [open, setOpen] = useState(false)
  return <td tabIndex={0}
    className={`ell${open ? ' ell-open' : ''}${mono ? ' mono' : ''}`}
    title={open ? undefined : title || undefined}
    onClick={(e) => { e.stopPropagation(); setOpen(!open) }}
    onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setOpen(!open) } }}>
    {children}</td>
}

const Head = ({ title, sub, tools }) => (
  <div className="view-head">
    <div><h1>{title}</h1>{sub && <div className="sub">{sub}</div>}</div>
    {tools && <div className="head-tools">{tools}</div>}
  </div>)

const Sync = ({ tick }) => <span className="sync">обновлено {Math.max(0, Math.round((Date.now() - tick) / 1000))} с назад</span>

/* ================= документы ЧЗ (общая таблица) ================= */
function DocTable({ docs, ctx, empty }) {
  const { notify, confirm, openDrawer, bump } = ctx
  const submitDoc = (d) => confirm(`Подать документ №${d.id} в «Честный знак»?`,
    'Документ уйдёт в ЧЗ и будет подписан УКЭП автоматически (signer). Отменить подачу нельзя — только создать корректировку.',
    `${d.type}`, 'Подать в ЧЗ', async () => {
      try { const r = await api(`/v1/docs/${d.id}/submit`, { method: 'POST' })
        notify(`Документ №${d.id} подан`, `uuid ${r.external_id}`); bump()
      } catch (e) { notify('Подача не прошла', e.message, 'bad') }
    })
  const checkDoc = async (d) => { try {
      const r = await api(`/v1/docs/${d.id}/check`, { method: 'POST' })
      notify(`Документ №${d.id}: ${DOC_STATUS[r.status]?.[0] || r.status}`, r.mt_status || ''); bump()
    } catch (e) { notify('Проверка не удалась', e.message, 'bad') } }
  const deleteDoc = async (d) => {
    let n = '—'
    try { const full = await api(`/v1/docs/${d.id}`)
      n = (full.payload?.products || full.payload?.products_list || []).length } catch {}
    confirm(`Удалить черновик №${d.id}?`,
      `Документ будет удалён безвозвратно, позиции (${n}) вернутся в очередь журнала. Коды, ушедшие дальше по жизни (возврат, пометка «вывел WB»), не откатятся — их счёт придёт в ответе.`,
      `${d.type} · позиций ${n}`, 'Удалить', async () => {
        try { const r = await api(`/v1/docs/${d.id}`, { method: 'DELETE' })
          notify(`Черновик №${d.id} удалён`,
            `возвращено позиций: ${r.reverted}${r.skipped ? `, пропущено: ${r.skipped}` : ''}`)
          bump()
        } catch (e) { notify('Удаление не удалось', e.message, 'bad') } }) }
  const showPayload = async (d) => { try {
      const full = await api(`/v1/docs/${d.id}`)
      openDrawer(`Документ №${d.id} · ${d.type}`,
        <div><p>Так документ уходит в ЧЗ: base64(JSON) в product_document. Правка состава — только пересборкой черновика.</p>
          <pre>{JSON.stringify(full.payload, null, 2)}</pre></div>)
    } catch (e) { notify('Не удалось открыть состав', e.message, 'bad') } }
  if (!docs.length) return <div className="empty"><b>{empty || 'Документов пока нет'}</b>Они появятся после сбора из позиций журнала.</div>
  return <div className="twrap"><table className="t fit">
    <colgroup><col style={{ width: 44 }} /><col style={{ width: 104 }} /><col style={{ width: 118 }} />
      <col style={{ width: 480 }} /><col style={{ width: 90 }} /><col style={{ width: 250 }} /></colgroup>
    <thead><tr><th>№</th><th>Тип</th><th>Статус</th><th>uuid в ЧЗ</th><th>Создан</th><th></th></tr></thead>
    <tbody>{docs.map((d) => <tr key={d.id}>
      <td className="num">{d.id}</td>
      <td className="mono ell" title={d.type}>{d.type}</td>
      <td><Badge dict={DOC_STATUS} v={d.status} /></td>
      <td className="ell mono" title={d.external_id || ''}>{d.external_id || '—'}</td>
      <td className="mono">{fmtD(d.created_at)}</td>
      <td className="actions">
        {d.status === 'draft' && <button className="btn sm pri" onClick={() => submitDoc(d)}>Подать</button>}
        {d.status === 'draft' && <button className="btn sm" onClick={() => deleteDoc(d)}>Удалить</button>}
        {(d.status === 'submitted' || d.status === 'error')
          && <button className="btn sm" onClick={() => checkDoc(d)}>Проверить</button>}
        <button className="btn sm" onClick={() => showPayload(d)}>Состав</button>
      </td></tr>)}
    </tbody></table></div>
}

/* ================= обзор ================= */
function Overview({ ctx, pulse }) {
  const { notify, confirm, bump, inn, go } = ctx
  const [docs, setDocs] = useState(null)
  useEffect(() => { api('/v1/docs?limit=6').then(setDocs).catch(() => setDocs([])) }, [ctx.tick])
  if (!pulse) return null
  const s = pulse.stats || {}
  const mk = pulse.markers || {}
  const active = (ts, warnMin) => !ts || (Date.now() / 1000 - ts) > warnMin * 60
  const signerAgeH = mk.signer_last_seen ? (Date.now() / 1000 - mk.signer_last_seen.ts) / 3600 : null
  const vitals = [
    { t: 'WB-поллинг', warn: active(mk.wb_last_poll?.at, 780),
      v: mk.wb_last_poll ? fmtAgo(mk.wb_last_poll.at) : 'ещё не было',
      d: 'продажи/возвраты → журнал · 06:30 / 18:30 МСК' },
    { t: 'Signer · УКЭП', warn: active(mk.signer_last_seen?.ts, 10),
      v: mk.signer_last_seen ? fmtAgo(mk.signer_last_seen.ts) : 'не виделся',
      d: signerAgeH != null && signerAgeH > 2 ? `молчит > ${Math.floor(signerAgeH)} ч — подписи ЧЗ стоят` : 'подписи ЧЗ через агента' },
    { t: 'Цикл НКМТ', warn: active(mk.nkmt_loop_last?.ts, 20),
      v: mk.nkmt_loop_last ? fmtAgo(mk.nkmt_loop_last.ts) : 'ещё не было',
      d: 'модерация и подписи карточек · каждые 10 мин' },
    { t: 'Монитор возвратов', warn: active(mk.returns_loop_last?.ts, 90),
      v: mk.returns_loop_last ? fmtAgo(mk.returns_loop_last.ts) : 'ещё не было',
      d: pulse.returns.nearest_deadline ? `ближайший дедлайн: через ${fmtLeft(pulse.returns.nearest_deadline)}` : 'активных возвратов нет' },
  ]
  const drafts = (pulse.docs || {})['LK_RECEIPT:draft'] || 0
  const draftsR = (pulse.docs || {})['LP_RETURN:draft'] || 0
  const submitted = Object.entries(pulse.docs || {})
    .filter(([k]) => k.endsWith(':submitted')).reduce((a, [, v]) => a + v, 0)
  const anomalies = Object.entries(s).filter(([k]) => k.startsWith('ANOMALY'))
    .reduce((a, [, v]) => a + v, 0)
  const pendW = s.PENDING_WITHDRAW || 0
  const dl = pulse.returns.nearest_deadline
  const dlHot = dl && parseUtc(dl) - Date.now() <= 48 * 36e5
  const doWithdraw = () => { if (!pendW) return notify('Нет позиций к выводу', 'Журнал не содержит КМ в статусе «к выводу».', 'warn')
    confirm('Собрать вывод из оборота?',
      `Из ${pendW} КМ будет создан черновик LK_RECEIPT (без фискального чека — отдельным документом). КМ сразу перейдут в «Выведен», подача в ЧЗ — отдельным шагом.`,
      `ИНН ${inn}`, 'Собрать документ', async () => {
        try { const r = await api('/v1/batches/withdraw', { method: 'POST', body: JSON.stringify({ inn }) })
          if (r.doc_id === 0) notify('Нет позиций к выводу', '', 'warn')
          else notify(`Создан черновик LK_RECEIPT №${r.doc_id}`, 'Подайте его в разделе «Вывод из оборота».')
          bump()
        } catch (e) { notify('Ошибка сбора вывода', e.message, 'bad') } }) }
  const q = pulse.quota || {}
  const openPts = (dlHot ? 1 : 0) + (pendW ? 1 : 0) + (drafts + draftsR ? 1 : 0) + (submitted ? 1 : 0) + (anomalies ? 1 : 0)
  return <>
    <Head title="Обзор" sub="Сводка платформы. Сначала то, что требует решения, потом — что происходит само."
      tools={<><Sync tick={ctx.tick} /><button className="btn sm" onClick={ctx.bump}>{I.refresh} Обновить</button></>} />
    <div className="pulse" aria-label="Пульс платформы">
      <div className="pulse-grid">
        {vitals.map((x) => <div className="pulse-cell" key={x.t}>
          <div className="t">{x.t}</div>
          <div className="v"><span className={`pdot${x.warn ? ' warn' : ''}`} />{x.v}</div>
          <div className="d">{x.d}</div></div>)}
      </div>
      <div className="ekg" aria-hidden="true"><svg viewBox="0 0 480 26" preserveAspectRatio="none">
        <path d="M0 13h56l6-1 5-9 6 17 6-9 4 2h44l6-1 5-9 6 17 6-9 4 2h44l6-1 5-9 6 17 6-9 4 2h44l6-1 5-9 6 17 6-9 4 2h44l6-1 5-9 6 17 6-9 4 2h56" />
      </svg></div>
    </div>
    <div className="grid2">
      <div>
        <div className="card">
          <div className="card-h"><h2>Требует решения</h2>
            <span className="hint">{openPts} {plural(openPts, ['пункт', 'пункта', 'пунктов'])}</span></div>
          <div>
            {dlHot && <div className="act urg"><div className="bar" />
              <div className="tx"><b>Дедлайн забора возврата: через {fmtLeft(dl)}</b>
                <div className="sm">Заберите товар с ПВЗ до срока — иначе WB вернёт его на склад.</div></div>
              <button className="btn sm" onClick={() => go('returns')}>Открыть возвраты</button></div>}
            {pendW > 0 && <div className="act warn"><div className="bar" />
              <div className="tx"><b>{pendW} КМ готовы к выводу из оборота</b>
                <div className="sm">Продажи WB, ждущие сбора LK_RECEIPT</div></div>
              <button className="btn sm pri" onClick={doWithdraw}>Собрать вывод</button></div>}
            {drafts + draftsR > 0 && <div className="act"><div className="bar" />
              <div className="tx"><b>{drafts + draftsR} черновик(а) ждут подачи в ЧЗ</b>
                <div className="sm">LK_RECEIPT: {drafts} · LP_RETURN: {draftsR}</div></div>
              <button className="btn sm" onClick={() => go('withdraw')}>К документам</button></div>}
            {submitted > 0 && <div className="act info"><div className="bar" />
              <div className="tx"><b>{submitted} документ(а) поданы, ждут проверки ЧЗ</b>
                <div className="sm">Автопроверка доводит до «принят ЧЗ» каждые 10 минут</div></div>
              <button className="btn sm" onClick={() => go('withdraw')}>К документам</button></div>}
            {anomalies > 0 && <div className="act urg"><div className="bar" />
              <div className="tx"><b>Аномалии в журнале: {anomalies}</b>
                <div className="sm">Автоматика их не трогает — нужен ручной разбор</div></div>
              <button className="btn sm" onClick={() => go('journal', 'ANOMALY')}>Разобрать</button></div>}
            {openPts === 0 && <div className="empty"><b>Всё чисто</b>Новые события появятся после поллинга WB и циклов автоматики.</div>}
          </div>
        </div>
        <div className="card">
          <div className="card-h"><h2>Последние документы</h2><span className="hint">все типы</span></div>
          <ul className="feed">
            {(docs || []).map((d) => <li key={d.id}><time>{fmtD(d.created_at)}</time>
              <span className="dotsep" style={{ background: d.status === 'error' ? 'var(--stamp)'
                : d.status === 'checked_ok' ? 'var(--go)' : d.status === 'submitted' ? 'var(--info)' : '#93A0A6' }} />
              <span>Документ №{d.id} · {d.type} — <b>{DOC_STATUS[d.status]?.[0] || d.status}</b></span></li>)}
            {docs && !docs.length && <li><span>Документов ещё нет</span></li>}
          </ul>
        </div>
      </div>
      <div>
        <div className="card">
          <div className="card-h"><h2>Автоматика</h2><span className="hint">воркер платформы</span></div>
          <ul className="loops">
            <li><span className="nm"><span className={`pdot${active(mk.wb_last_poll?.at, 780) ? ' warn' : ''}`} />Экзайз-поллинг WB</span><span className="int">06:30 / 18:30 МСК</span></li>
            <li><span className="nm"><span className="pdot" />Проверка документов ЧЗ</span><span className="int">каждые 10 мин</span></li>
            <li><span className="nm"><span className={`pdot${active(mk.nkmt_loop_last?.ts, 20) ? ' warn' : ''}`} />НКМТ: модерация + подпись</span><span className="int">каждые 10 мин</span></li>
            <li><span className="nm"><span className={`pdot${active(mk.returns_loop_last?.ts, 90) ? ' warn' : ''}`} />Монитор возвратов WB</span><span className="int">каждый час</span></li>
            <li><span className="nm"><span className="pdot" />Сторож signer</span><span className="int">каждые 30 мин</span></li>
          </ul>
          <div className="card-b" style={{ borderTop: '1px solid var(--line)' }}>
            <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>Критичное дублируется в Telegram: новые возвраты, дедлайны ≤48 ч, принятые и отклонённые документы, публикация батчей, молчание signer.</p>
          </div>
        </div>
        <div className="card">
          <div className="card-h"><h2>Квоты внешних API</h2></div>
          <div className="card-b" style={{ fontSize: 13 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
              <span>WB goods-return</span>
              <b className="mono" style={{ color: q.goods_return_used ? 'var(--wait)' : 'var(--go)' }}>
                {q.goods_return_used || 0} из {q.goods_return_limit || 2} в час</b></div>
            <div style={{ height: 6, background: 'var(--surface-2)', border: '1px solid var(--line)', borderRadius: 99, overflow: 'hidden' }}>
              <div style={{ width: `${Math.min(100, 100 * (q.goods_return_used || 0) / (q.goods_return_limit || 2))}%`, height: '100%', background: '#D9A13B' }} /></div>
            <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8 }}>Ручной опрос возвратов расходует ту же квоту, что и часовой автоматический. Обычно кнопка не нужна.</p>
          </div>
        </div>
      </div>
    </div>
  </>
}

/* ================= вывод из оборота ================= */
function Withdraw({ ctx }) {
  const { notify, confirm, bump, inn } = ctx
  const [pend, setPend] = useState(null)
  const [docs, setDocs] = useState(null)
  useEffect(() => { api('/v1/journal?state=PENDING_WITHDRAW&limit=1000').then(setPend).catch(() => setPend([]))
    api('/v1/docs?limit=200').then(setDocs).catch(() => setDocs([])) }, [ctx.tick])
  const doWithdraw = () => { const n = pend ? pend.length : 0
    if (!n) return notify('Нет позиций к выводу', 'Журнал не содержит КМ в статусе «к выводу».', 'warn')
    confirm('Собрать вывод из оборота?',
      `Перед сбором коды проверяются в Честном Знаке: уже выведенные WB в документ не попадут (перейдут в «выведен (WB)»). Из остальных будет создан черновик LK_RECEIPT (позиции без фискального чека — отдельным документом «Иное»). КМ сразу перейдут в «Выведен»; подача в ЧЗ — отдельным шагом.`,
      `ИНН ${inn}`, 'Собрать документ', async () => {
        try { const r = await api('/v1/batches/withdraw', { method: 'POST', body: JSON.stringify({ inn }) })
          if (r.doc_id === 0) notify('Нет позиций к выводу', '', 'warn')
          else {
            const p = r.preflight || {}
            const extra = p.skipped
              ? 'ЧЗ был недоступен — проверка статусов пропущена.'
              : `ЧЗ: проверено ${p.checked ?? 0} · переведено «вывел WB» ${p.translated ?? 0}.`
            notify(`Создан черновик LK_RECEIPT №${r.doc_id}`, `${extra} Подайте его в ЧЗ — кнопкой «Подать» ниже.`)
          }
          bump()
        } catch (e) { notify('Ошибка сбора вывода', e.message, 'bad') } }) }
  const lk = (docs || []).filter((d) => d.type === 'LK_RECEIPT')
  return <>
    <Head title="Вывод из оборота" sub="Продажи FBS становятся позициями журнала; из них собирается документ LK_RECEIPT (дистанционная продажа) и подаётся в «Честный знак»."
      tools={<Sync tick={ctx.tick} />} />
    <div className="strip">
      <div className="kpi"><div className="n warn">{pend ? pend.length : '—'}</div><div className="l">к выводу</div></div>
      <div className="kpi"><div className="n">{lk.filter((d) => d.status === 'draft').length}</div><div className="l">черновик</div></div>
      <div className="kpi"><div className="n">{lk.filter((d) => d.status === 'submitted').length}</div><div className="l">подан</div></div>
      <div className="kpi"><div className="n good">{lk.filter((d) => d.status === 'checked_ok').length}</div><div className="l">принят ЧЗ</div></div>
      <div className="kpi"><div className="n bad">{lk.filter((d) => d.status === 'error').length}</div><div className="l">ошибок</div></div>
    </div>
    <div className="card">
      <div className="card-h"><h2>Готовы к выводу</h2><span className="hint">шт: {pend ? pend.length : '…'} · ИНН из «Справочников»</span></div>
      <div className="twrap"><table className="t fit">
        <colgroup><col style={{ width: 248 }} /><col style={{ width: 783 }} /><col style={{ width: 205 }} /></colgroup>
        <thead><tr><th>Код маркировки</th><th>Наименование</th><th>Последний сигнал</th></tr></thead>
        <tbody>{(pend || []).map((it) => <tr key={it.km}>
          <td><KmCell km={it.km} /></td>
          <EllCell title={it.cis_product_name || ''}>
            {it.cis_product_name || <span className="faint">—</span>}</EllCell>
          <EllCell title={evLine(it)}><span style={{ fontSize: 12.5 }}>{evLine(it)}</span></EllCell></tr>)}
          {pend && !pend.length && <tr><td colSpan={3}><div className="empty"><b>Всё выведено</b>Новые продажи появятся после поллинга WB — 06:30 и 18:30 МСК.</div></td></tr>}
        </tbody></table></div>
      <div className="card-b" style={{ borderTop: '1px solid var(--line)', display: 'flex', justifyContent: 'flex-end' }}>
        <button className="btn pri" onClick={doWithdraw}>Собрать вывод</button></div>
    </div>
    <div className="card">
      <div className="card-h"><h2>Документы LK_RECEIPT</h2><span className="hint">автопроверка статуса — каждые 10 мин</span></div>
      {docs && <DocTable docs={lk} ctx={ctx} empty="Документов вывода пока нет" />}
    </div>
  </>
}

/* ================= возвраты ================= */
function Returns({ ctx, pulse }) {
  const { notify, confirm, bump, inn } = ctx
  const [rows, setRows] = useState(null)
  const [docs, setDocs] = useState(null)
  const [stats, setStats] = useState(null)
  useEffect(() => { api('/v1/wb/returns').then(setRows).catch(() => setRows([]))
    api('/v1/docs?limit=200').then(setDocs).catch(() => setDocs([]))
    api('/v1/journal/stats').then(setStats).catch(() => {}) }, [ctx.tick])
  const sorted = (rows || []).slice().sort((a, b) => {
    if (!!a.completed_dt !== !!b.completed_dt) return a.completed_dt ? 1 : -1
    return (a.expired_dt || '').localeCompare(b.expired_dt || '') })
  const nearest = sorted.find((r) => !r.completed_dt && r.expired_dt)
  const hot = nearest && parseUtc(nearest.expired_dt) - Date.now() <= 48 * 36e5
  const poll = () => confirm('Опросить WB goods-return вручную?',
    'Ручной опрос расходует ту же квоту, что и часовой автоматический.',
    'GET goods-return · окно 7 дней', 'Опросить', async () => {
      try { const r = await api('/v1/wb/returns/poll', { method: 'POST' })
        notify('Опрос выполнен', `новых ${r.new}, обновлено ${r.updated}` + (r.alerts ? `, алертов ${r.alerts}` : ''))
        bump()
      } catch (e) { notify('Опрос не удался', e.message, 'bad') } })
  const doReturn = () => { const n = (stats || {}).PENDING_RETURN || 0
    if (!n) return notify('Нет позиций к возврату', 'КМ в статусе «к возврату» появятся, когда WB примет возврат (excise op=2).', 'warn')
    confirm('Собрать возврат продавца?',
      `Из ${n} КМ будет создан черновик LP_RETURN. Первичка — чеки из последних выводов; КМ без вывода будут пропущены.`,
      'LP_RETURN · REMOTE_SALE_RETURN · оплачено', 'Собрать документ', async () => {
        try { const r = await api('/v1/batches/return', { method: 'POST', body: JSON.stringify({ inn }) })
          if (!r.docs) notify('Возврат не собран', `${r.blocked} КМ без вывода из оборота`, 'warn')
          else notify('Создан черновик LP_RETURN', r.blocked ? `КМ без вывода пропущено: ${r.blocked}` : '')
          bump()
        } catch (e) { notify('Ошибка сбора возврата', e.message, 'bad') } }) }
  const lp = (docs || []).filter((d) => d.type === 'LP_RETURN')
  return <>
    <Head title="Возвраты" sub="Физическое движение: покупатель сдал товар на ПВЗ → забрать до дедлайна (WB хранит 7 дней) → КМ появляется в «к возврату» → собирается LP_RETURN."
      tools={<Sync tick={ctx.tick} />} />
    {hot && nearest && <div className="banner">{I.clock}
      <div className="b-tx"><b>Заказ {nearest.order_id}: забрать до {fmtD(nearest.expired_dt)} (через {fmtLeft(nearest.expired_dt)})</b>
        <div className="sm">{nearest.office || 'ПВЗ'} · просрочите — товар вернут на склад WB. Алерт уже ушёл в Telegram.</div></div></div>}
    <div className="strip">
      <div className="kpi"><div className="n">{(stats || {}).PENDING_RETURN || 0}</div><div className="l">к возврату в ЧЗ</div></div>
      <div className="kpi"><div className="n warn">{(rows || []).filter((r) => !r.completed_dt).length}</div><div className="l">активных на ПВЗ</div></div>
      <div className="kpi"><div className="n good">{(stats || {}).RETURNED || 0}</div><div className="l">возвращено</div></div>
    </div>
    <div className="card">
      <div className="card-h"><h2>Возвраты на ПВЗ (WB goods-return)</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="hint">квота {pulse?.quota?.goods_return_used ?? '—'}/{pulse?.quota?.goods_return_limit ?? 2} в час</span>
          <button className="btn sm" onClick={poll}>Обновить WB</button></div></div>
      <div className="twrap"><table className="t fit">
        <colgroup><col style={{ width: 84 }} /><col style={{ width: 330 }} /><col style={{ width: 170 }} />
          <col style={{ width: 150 }} /><col style={{ width: 132 }} /><col style={{ width: 100 }} />
          <col style={{ width: 180 }} /></colgroup>
        <thead><tr><th>Заказ</th><th>Предмет</th><th>Причина</th><th>Статус</th><th>Забрать до</th><th>Выдан</th><th>ПВЗ</th></tr></thead>
        <tbody>{sorted.map((r) => {
          const c = !r.completed_dt && r.expired_dt ? leftCls(r.expired_dt) : ''
          return <tr key={r.srid} className={c === 'danger' || c === 'over' ? 'rowhot' : ''}>
            <td className="num">{r.order_id}</td>
            <td className="ell" title={r.subject || r.srid}>{r.subject || r.srid}</td>
            <td className="ell" title={r.reason || ''}>{r.reason || '—'}</td>
            <td className="ell" title={r.status || ''}>{r.status}</td>
            <td>{r.expired_dt ? <><span className={`cd ${c}`}>{fmtLeft(r.expired_dt)}</span>
              <div className="faint mono" style={{ fontSize: 11, marginTop: 2 }}>{fmtD(r.expired_dt)}</div></> : '—'}</td>
            <td className="mono">{r.completed_dt ? fmtD(r.completed_dt) : '—'}</td>
            <td className="ell" title={r.office || ''}>{r.office || '—'}</td></tr> })}
          {rows && !rows.length && <tr><td colSpan={7}><div className="empty"><b>Возвратов нет</b>Появятся из часового опроса WB — или нажмите «Обновить WB».</div></td></tr>}
        </tbody></table></div>
      <div className="card-b" style={{ borderTop: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12.5, color: 'var(--muted)' }}>Товар, выданный продавцом, появляется в журнале как «к возврату».</span>
        <button className="btn pri" onClick={doReturn}>Собрать возврат</button></div>
    </div>
    <div className="card">
      <div className="card-h"><h2>Документы LP_RETURN</h2><span className="hint">первичка — чек из последнего вывода КМ</span></div>
      {docs && <DocTable docs={lp} ctx={ctx} empty="Документов возврата пока нет" />}
    </div>
  </>
}

/* ================= каталог НК ================= */
// маркер источника подстановки: компактная строка под значением ячейки
const SrcMark = ({ v, src, extra }) => (v && src && src !== 'file'
  ? <div className="faint" style={{ fontSize: 11 }}>← {SRC_RU[src] || src}{extra || ''}</div> : null)

// декларативный конфиг колонок предпросмотра (паттерн JOURNAL_COLUMNS):
// подстановочные поля несут маркер источника — правило видно до импорта;
// все колонки с шириной, сумма 1204 = контент wide-модалки без скролла
const PREVIEW_COLUMNS = [
  { key: 'article', label: 'Артикул', w: 88, mono: true,
    text: (r) => r.article, render: (r) => r.article },
  { key: 'name', label: 'Наименование', w: 128, ell: true,
    text: (r) => r.name, render: (r) => r.name || <span className="faint">—</span> },
  { key: 'pt', label: 'Вид', w: 92, ell: true,
    text: (r) => r.product_type, render: (r) => r.product_type || <span className="faint">—</span> },
  { key: 'brand', label: 'Бренд', w: 80,
    text: (r) => r.brand, render: (r) => <>{r.brand || '—'}<SrcMark v={r.brand} src={r.src.brand} /></> },
  { key: 'tnved', label: 'ТН ВЭД', w: 80, mono: true,
    text: (r) => r.tnved, render: (r) => <>{r.tnved || '—'}
      <SrcMark v={r.tnved} src={r.src.tnved} />
      {r.tnved_warning && <div title={r.tnved_warning}
        style={{ fontSize: 11, color: 'var(--wait)' }}>вне декларации</div>}</> },
  { key: 'size', label: 'Размер', w: 78,
    text: (r) => r.size, render: (r) => <>{r.size || <span className="faint">—</span>}<SrcMark v={r.size} src={r.src.size} />
      {r.size_warning && <div title={r.size_warning}
        style={{ fontSize: 11, color: 'var(--wait)' }}>вне справочника</div>}</> },
  { key: 'gender', label: 'Пол', w: 92, ell: true,
    text: (r) => r.target_gender, render: (r) => <>{r.target_gender || <span className="faint">—</span>}<SrcMark v={r.target_gender} src={r.src.target_gender} /></> },
  { key: 'decl', label: 'Декларация', w: 150,
    text: (r) => r.declaration_number, render: (r) => <>
      {r.declaration_number || '—'}
      {r.declaration_date && <div className="faint mono" style={{ fontSize: 11 }}>{r.declaration_date}</div>}
      <SrcMark v={r.declaration_number} src={r.src.declaration_number}
        extra={r.src.declaration_number === 'rule' && r.rule_id ? ` №${r.rule_id}` : ''} /></> },
  { key: 'producer', label: 'Производитель', w: 122,
    text: (r) => r.producer, render: (r) => <>{r.producer || '—'}<SrcMark v={r.producer} src={r.src.producer} /></> },
  { key: 'gtin', label: 'GTIN', w: 98, mono: true,
    text: (r) => r.gtin, render: (r) => <>{r.gtin || '—'}
      {r.gtin_status && <div style={{ marginTop: 3 }}><Badge dict={GTIN_STATUS} v={r.gtin_status} /></div>}</> },
  { key: 'res', label: 'Итог', w: 70,
    render: (r) => r.ok ? <span className="bdg green">ok</span> : <span className="bdg red">ошибка</span> },
  { key: 'err', label: 'Ошибка', w: 126, ell: true,
    text: (r) => r.error || '', render: (r) => r.error ? <span className="err-tx">{r.error}</span> : '' },
]

// Превью импорта: тот же разбор, что сделает импорт (файл → правило РД → дефолт),
// без записи; апрув в широкой модалке — повторная подача того же файла в /import.
function ImportPreview({ ctx, file, onDone }) {
  const { notify, closeWide, bump } = ctx
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => {
    setErr(''); setData(null)
    const fd = new FormData(); fd.append('file', file)
    api('/v1/nkmt/import/preview', { method: 'POST', body: fd })
      .then(setData).catch((e) => setErr(e.message))
  }, [file])
  useEffect(load, [load])
  const doImport = () => { if (busy) return
    setBusy(true)
    const fd = new FormData(); fd.append('file', file)
    api('/v1/nkmt/import', { method: 'POST', body: fd })
      .then((r) => { notify(`Импорт завершён: батч №${r.batch_id}`, `ok ${r.stats.ok}, ошибок ${r.stats.error}`)
        closeWide(); onDone(); bump() })
      .catch((e) => { notify('Импорт не удался', e.message, 'bad'); setBusy(false) }) }
  if (err) return <div className="empty"><b>Предпросмотр не удался</b>{err}
    <div style={{ marginTop: 12 }}><button className="btn sm" onClick={load}>Повторить</button></div></div>
  if (!data) return <div className="empty"><b>Разбираем файл…</b>Валидация строк, правила РД и справочники НК.</div>
  const s = data.stats
  return <div className="wide-fill">
    <div className="wide-scroll">
      <p style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 0 }}>
        Подстановка: значение из файла → правило РД → дефолты. Под каждым значением —
        источник, у правила его номер. Импорт повторит ровно этот разбор.</p>
          <div className="twrap"><table className="t small fit">
            <colgroup>{PREVIEW_COLUMNS.map((c) => <col key={c.key} style={{ width: c.w }} />)}</colgroup>
        <thead><tr>{PREVIEW_COLUMNS.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead>
        <tbody>{data.rows.map((r, i) => <tr key={i} className={r.ok ? undefined : 'rowhot'}>
          {PREVIEW_COLUMNS.map((c) => c.ell
            ? <EllCell key={c.key} title={c.text(r)} mono={c.mono}>{c.render(r)}</EllCell>
            : <td key={c.key} className={c.mono ? 'mono' : undefined}>{c.render(r)}</td>)}
        </tr>)}
      </tbody></table></div>
    </div>
    <div className="modal-f">
      <span className="hint">ok {s.ok} · ошибок {s.error} · GTIN: новых {s.new}, обновится {s.update}, конфликтов {s.conflict}</span>
      <span style={{ display: 'flex', gap: 8 }}>
        <button className="btn sm" onClick={closeWide}>Отмена</button>
        <button className="btn sm pri" disabled={busy || !s.ok} onClick={doImport}>Импортировать</button>
      </span>
    </div>
  </div>
}

function Catalog({ ctx }) {
  const { notify, confirm, bump, openDrawer } = ctx
  const [batches, setBatches] = useState(null)
  const [stage, setStage] = useState('')
  const [openId, setOpenId] = useState(null)
  const [cards, setCards] = useState(null)
  const [cardFilter, setCardFilter] = useState('')
  const [file, setFile] = useState(null)
  const [dragOn, setDragOn] = useState(false)
  const fileRef = useRef(null)
  useEffect(() => { api('/v1/nkmt/batches').then(setBatches)
    .catch((e) => { setBatches([]); notify('Не удалось загрузить батчи', e.message, 'bad') }) }, [ctx.tick])
  const stageMatch = STAGES.find((x) => x.key === stage)
  useEffect(() => { if (openId != null)
    api(`/v1/nkmt/batches/${openId}?card_status=${encodeURIComponent(cardFilter)}`)
      .then((r) => setCards(r.cards || []))
      .catch((e) => notify('Не удалось загрузить карточки', e.message, 'bad')) }, [openId, cardFilter])
  const toggle = (id) => { if (openId === id) { setOpenId(null); setCards(null); return }
    setOpenId(id); setCards(null) }
  const act = (id, kind, okMsg) => confirm(
    kind === 'feed' ? 'Подать фид в Национальный каталог?' : `Выполнить «${kind}» для батча №${id}?`,
    kind === 'feed' ? 'Карточки уйдут в НК; дальше модерация и подпись идут автоматически (воркер).' : 'Ручной прогон того же, что делает автоматика.',
    `/v1/nkmt/batches/${id}/${kind}`, kind === 'feed' ? 'Подать фид' : 'Выполнить',
    async () => { try { const r = await api(`/v1/nkmt/batches/${id}/${kind}`, { method: 'POST' })
        notify(okMsg(r), ''); bump()
      } catch (e) { notify('Не удалось', e.message, 'bad') } })
  const showPreview = (f) => { if (!f) return
    ctx.openWide(`Предпросмотр импорта · ${f.name}`,
      <ImportPreview ctx={ctx} file={f}
        onDone={() => { setFile(null); if (fileRef.current) fileRef.current.value = '' }} />) }
  // один вход и для выбора в проводнике, и для перетаскивания: не-xlsx отсекаем
  const pickFile = (f) => { if (!f) return
    if (!/\.xlsx$/i.test(f.name)) return notify('Нужен файл .xlsx', f.name, 'warn')
    setFile(f); showPreview(f) }
  const shown = (batches || []).filter((b) => stage === '' || (stageMatch?.match || [stage]).includes(b.status))
  return <>
    <Head title="Каталог НК" sub="Карточки Национального каталога: импорт выгрузки 1С → фид → модерация → подпись УКЭП → публикация → отчёт для 1С."
      tools={<Sync tick={ctx.tick} />} />
    <div className="pipeline" role="group" aria-label="Конвейер батчей">
      {STAGES.map((st) => { const n = st.key === '' ? (batches || []).length
        : (batches || []).filter((b) => (st.match || [st.key]).includes(b.status)).length
        return <button key={st.key} className={`stage${n ? ' has' : ''}`} aria-pressed={stage === st.key}
          onClick={() => setStage(st.key)}><span className="n">{n}</span><div className="l">{st.l}</div></button> })}
    </div>
    <div className="card">
      <div className="card-h"><h2>Импорт выгрузки</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="hint">.xlsx из 1С → предпросмотр → импорт</span>
          <button className="btn sm" onClick={() => dl('/v1/nkmt/import/template', 'nkmt-import-template.xlsx')
            .catch((e) => notify('Шаблон не скачался', e.message, 'bad'))}>Шаблон</button>
        </div></div>
      <div className="card-b">
        <label className={`drop${dragOn ? ' on' : ''}`} style={{ display: 'block' }}
          onDragOver={(e) => { e.preventDefault(); setDragOn(true) }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragOn(false) }}
          onDrop={(e) => { e.preventDefault(); setDragOn(false); pickFile(e.dataTransfer.files[0]) }}>
          <input ref={fileRef} type="file" accept=".xlsx" style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files[0] || null
              e.target.value = ''   // повторный выбор того же файла тоже даст change
              pickFile(f) }} />
          <b>Выберите или перетащите выгрузку .xlsx</b>
          <div style={{ fontSize: 12.5, marginTop: 2 }}>покажем предпросмотр: подстановки, правила РД и ошибки — до записи в базу</div>
          {file && <div className="file">{file.name}</div>}
        </label>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
          <button className="btn pri" disabled={!file} onClick={() => showPreview(file)}>Предпросмотр и импорт</button></div>
      </div>
    </div>
    <div className="card">
      <div className="card-h"><h2>Батчи</h2><span className="hint">модерация и подпись прогоняются автоматически каждые 10 мин</span></div>
      <div>{shown.map((b) => <div className="batch" key={b.id}>
        <div className="batch-h">
          <span className="id">№{b.id}</span><Badge dict={BATCH_STATUS} v={b.status} />
          <span className="fn" title={b.source_filename}>{b.source_filename}</span>
          <span className="hint mono">{fmtD(b.created_at)}</span>
          <span className={`bdg ${b.stats?.error ? 'red' : 'green'}`}>{b.stats ? `${b.stats.ok}/${b.stats.error}` : '—'}</span>
          <span className="sp" />
          <span className="btns">
            <button className="btn sm" onClick={() => toggle(b.id)}>{openId === b.id ? 'Скрыть' : 'Карточки'}</button>
            {b.status === 'new' && <button className="btn sm pri" onClick={() => act(b.id, 'feed', (r) => `Фид отправлен (id ${r.feed_id}) — дальше автоматика`)}>Подать фид</button>}
            {(b.status === 'moderation' || b.status === 'feeding') && <button className="btn sm" onClick={() => act(b.id, 'refresh', () => 'Модерация опрошена')}>Обновить</button>}
            {b.status === 'signing' && <button className="btn sm pri" onClick={() => act(b.id, 'sign', (r) => `Подписано ${r.signed}, ошибок ${r.failed}`)}>Подписать</button>}
            <button className="btn sm" onClick={() => dl(`/v1/nkmt/batches/${b.id}/report?format=xlsx`, `marko-batch-${b.id}.xlsx`).catch((e) => notify('Отчёт не сформирован', e.message, 'bad'))}>Отчёт 1С</button>
            <button className="btn sm" onClick={() => dl(`/v1/nkmt/batches/${b.id}/report?format=csv`, `marko-batch-${b.id}.csv`).catch((e) => notify('Отчёт не сформирован', e.message, 'bad'))}>csv</button>
          </span>
        </div>
        {openId === b.id && <div className="cards-wrap">
          <div className="tools">
            <select value={cardFilter} onChange={(e) => setCardFilter(e.target.value)}>
              <option value="">все статусы карточек</option>
              {Object.keys(CARD_STATUS).map((s2) => <option key={s2} value={s2}>{CARD_STATUS[s2][0]}</option>)}
            </select>
            <span className="hint">{cards ? `карточек: ${cards.length}` : 'загрузка…'}</span></div>
          <div className="twrap"><table className="t small fit">
            <colgroup><col style={{ width: 132 }} /><col style={{ width: 132 }} /><col style={{ width: 340 }} />
              <col style={{ width: 130 }} /><col style={{ width: 260 }} /></colgroup>
            <thead><tr><th>Артикул</th><th>GTIN</th><th>Наименование</th><th>Статус</th><th>Ошибка</th></tr></thead>
            <tbody>{(cards || []).map((c) => <tr key={c.id}>
              <td className="ell mono" title={c.article}>{c.article}</td>
              <td className="ell mono" title={c.gtin || ''}>{c.gtin || '—'}</td>
              <td className="ell" title={c.name}>{c.name}</td>
              <td><Badge dict={CARD_STATUS} v={c.status} /></td>
              <td className="ell err-tx" title={c.error_text || ''}>{c.error_text || ''}</td></tr>)}
            </tbody></table></div>
        </div>}
      </div>)}
      {batches && !shown.length && <div className="empty"><b>Батчей в этом статусе нет</b>Импортируйте выгрузку, чтобы начать новый.</div>}
      </div>
    </div>
  </>
}

/* карточка декларации: вся мета из ЧЗ + действия — клик по строке реестра */
function DeclarationCard({ d, ctx, onDel }) {
  const [st, setSt] = useState(d)
  const [busy, setBusy] = useState(false)
  const check = async () => { if (busy) return
    setBusy(true)
    try { const r = await api(`/v1/nkmt/declarations/${st.id}/check`, { method: 'POST' })
      setSt(r.declaration)
      const [lbl] = declState(r.declaration)
      ctx.notify(r.found ? `Декларация: ${lbl}` : 'ЧЗ не нашёл декларацию',
        r.found ? `ТН ВЭД: ${r.declaration.tnved_list.join(', ') || '—'}`
          : 'Проверьте номер и дату — пара должна совпадать с реестром ЧЗ.',
        r.found ? '' : 'warn')
      ctx.bump()
    } catch (e) { ctx.notify('Проверка не удалась', e.message, 'bad') } finally { setBusy(false) } }
  const [lbl, cls] = declState(st)
  return <div>
    <div style={{ marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
      <span className="mono" style={{ fontSize: 12.5, wordBreak: 'break-all' }}>{st.doc_number}</span>
      <span className={`bdg ${cls}`}>{lbl}</span>
    </div>
    <div className="row" style={{ gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
      <button className="btn" disabled={busy} onClick={check}>Проверить в ЧЗ</button>
      <button className="btn" onClick={onDel}>Удалить</button>
    </div>
    <b style={{ fontSize: 12.5 }}>Реестр ЧЗ</b>
    <div className="twrap" style={{ margin: '6px 0 10px' }}><table className="t small"><tbody>
      <tr><td className="faint" style={{ width: '40%' }}>Тип документа</td>
        <td>{st.doc_type === 'certificate' ? 'сертификат соответствия' : 'декларация о соответствии'}</td></tr>
      <tr><td className="faint">Дата регистрации</td><td className="mono">{st.doc_date}</td></tr>
      <tr><td className="faint">Действует до</td><td className="mono">{st.date_to || '—'}</td></tr>
      <tr><td className="faint">Продукция</td><td>{st.product_name || '—'}</td></tr>
      <tr><td className="faint">Допустимые ТН ВЭД</td>
        <td className="mono" style={{ fontSize: 12 }}>{(st.tnved_list || []).join(', ') || '—'}</td></tr>
      <tr><td className="faint">Техрегламенты</td><td>{st.techregs || '—'}</td></tr>
      <tr><td className="faint">Заявитель</td><td>{st.applicant || '—'}</td></tr>
      <tr><td className="faint">Изготовитель</td><td>{st.manufacturer || '—'}</td></tr>
      <tr><td className="faint">Проверено в ЧЗ</td>
        <td>{st.checked_at ? fmtD(st.checked_at) : 'ещё не было'}</td></tr>
    </tbody></table></div>
    {st.title && <div className="note" style={{ marginBottom: 10 }}>Название для себя: {st.title}</div>}
    <details>
      <summary style={{ fontSize: 12, color: 'var(--muted)', cursor: 'pointer' }}>Сырые данные</summary>
      <pre>{JSON.stringify(st, null, 2)}</pre>
    </details>
  </div>
}

/* ================= справочники ================= */
function Refs({ ctx }) {
  const { notify, confirm, inn, setInn } = ctx
  const [tab, setTab] = useState('fields')
  const [decls, setDecls] = useState(null)
  const [defs, setDefs] = useState(null)
  const [em, setEm] = useState(null)
  const [rules, setRules] = useState(null)
  const [producers, setProducers] = useState(null)
  const [checkBusy, setCheckBusy] = useState(false)
  const [dnum, setDnum] = useState(''); const [ddate, setDdate] = useState(''); const [dtype, setDtype] = useState('declaration')
  const [dtitle, setDtitle] = useState('')
  const [pname, setPname] = useState(''); const [pinn, setPinn] = useState('')
  const [pkind, setPkind] = useState(''); const [pnote, setPnote] = useState('')
  const [hints, setHints] = useState({ brands: [], product_types: [], producers: [] })
  const [rbrand, setRbrand] = useState(''); const [rdecl, setRdecl] = useState('')
  const [rtypes, setRtypes] = useState([]); const [rtypeInput, setRtypeInput] = useState('')
  const [rprod, setRprod] = useState('')
  const [rfields, setRfields] = useState({}); const [rfKey, setRfKey] = useState('size')
  const [rfVal, setRfVal] = useState('')
  const [rzBrand, setRzBrand] = useState(''); const [rzType, setRzType] = useState(''); const [rz, setRz] = useState(null)
  useEffect(() => { api('/v1/nkmt/declarations').then(setDecls).catch(() => setDecls([]))
    api('/v1/nkmt/rules').then(setRules).catch(() => setRules([]))
    api('/v1/nkmt/producers').then(setProducers).catch(() => setProducers([]))
    api('/v1/nkmt/dicts/hints').then(setHints).catch(() => {}) }, [ctx.tick])
  // формы дефолтов и эмиттера грузятся один раз при входе: 60-секундный тик
  // консоли не должен затирать несохранённые правки оператора
  useEffect(() => {
    api('/v1/nkmt/defaults').then(setDefs)
      .catch((e) => { setDefs({}); notify('Дефолты не загрузились', e.message, 'bad') })
    api('/v1/emitter/defaults').then(setEm).catch(() => setEm({ fias_id: '', primary_custom_name: '' }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const addDecl = () => { if (!dnum || !ddate) return notify('Заполните номер и дату', '', 'warn')
    api('/v1/nkmt/declarations', { method: 'POST', body: JSON.stringify({ doc_number: dnum, doc_date: ddate, doc_type: dtype, title: dtitle }) })
      .then((r) => { setDnum(''); setDdate(''); setDtitle('')
        notify('Декларация добавлена', r.found
          ? 'Данные из ЧЗ: статус, срок и ТН ВЭД подтянуты автоматически.'
          : 'ЧЗ не ответил — нажмите «Проверить в ЧЗ», чтобы подтянуть статус и ТН ВЭД.')
        ctx.bump() })
      .catch((e) => notify('Не добавлено', e.message, 'bad')) }
  const delDecl = (d) => confirm('Удалить декларацию?', d.doc_number, 'Карточки, где она уже подставлена, не изменятся.', 'Удалить',
    () => api(`/v1/nkmt/declarations/${d.id}`, { method: 'DELETE' })
      .then(() => { notify('Декларация удалена', ''); ctx.closeDrawer(); ctx.bump() })
      .catch((e) => notify('Не удалось удалить', e.message, 'bad')))
  // клик по строке реестра → карточка со всей метой из ЧЗ и действиями
  const openDecl = (d) => ctx.openDrawer(<>Декларация ·&nbsp;<span className="mono"
    style={{ fontSize: 12, color: 'var(--muted)' }}>№{d.id}</span></>,
    <DeclarationCard d={d} ctx={ctx} onDel={() => delDecl(d)} />)
  const checkAll = () => { if (checkBusy) return
    setCheckBusy(true)
    api('/v1/nkmt/declarations/check-all', { method: 'POST' })
      .then((r) => { notify('Декларации проверены в ЧЗ',
          `найдено ${r.found} из ${r.checked}` + (r.not_found?.length ? ` · не найдено: ${r.not_found.join(', ')}` : ''))
        ctx.bump() })
      .catch((e) => notify('Проверка не удалась', e.message, 'bad'))
      .finally(() => setCheckBusy(false)) }
  const addProducer = () => { if (!pname.trim()) return notify('Укажите наименование', 'Как оно должно попасть в карточку НК (атрибут «Производитель»).', 'warn')
    if (pinn && (!/^\d+$/.test(pinn) || ![10, 12].includes(pinn.length)))
      return notify('ИНН: 10 или 12 цифр', '', 'warn')
    api('/v1/nkmt/producers', { method: 'POST',
        body: JSON.stringify({ name: pname, inn: pinn, kind: pkind, note: pnote }) })
      .then(() => { setPname(''); setPinn(''); setPkind(''); setPnote('')
        notify('Производитель добавлен', 'Появится в подсказках правил и дефолтов.'); ctx.bump() })
      .catch((e) => notify('Не добавлено', e.message, 'bad')) }
  const delProducer = (p) => confirm('Удалить производителя?', p.name,
    'Подсказки исчезнут; уже подставленные в карточки и правила значения не изменятся.', 'Удалить',
    () => api(`/v1/nkmt/producers/${p.id}`, { method: 'DELETE' })
      .then(() => { notify('Производитель удалён', ''); ctx.bump() })
      .catch((e) => notify('Не удалось удалить', e.message, 'bad')))
  const addRule = () => { if (!rbrand.trim() && !rtypes.length)
    return notify('Заполните бренд или вид товара', 'Правило без условия не создаётся — оно подходило бы всем строкам.', 'warn')
    if (!rdecl) return notify('Выберите декларацию', '', 'warn')
    api('/v1/nkmt/rules', { method: 'POST',
        body: JSON.stringify({ brand: rbrand, product_types: rtypes, declaration_id: Number(rdecl), producer: rprod, fields: rfields }) })
      .then(() => { setRbrand(''); setRtypes([]); setRtypeInput(''); setRprod(''); setRfields({}); setRfVal(''); setRdecl('')
        notify('Правило добавлено', 'Сработает при следующем импорте.'); ctx.bump() })
      .catch((e) => notify('Не добавлено', e.message, 'bad')) }
  const addRtype = () => { const t = rtypeInput.trim()
    if (t && !rtypes.some((x) => x.toLowerCase() === t.toLowerCase())) setRtypes([...rtypes, t])
    setRtypeInput('') }
  const addRField = () => { const v = rfVal.trim()
    if (v && rfKey) setRfields({ ...rfields, [rfKey]: v })
    setRfVal('') }
  const delRule = (r) => confirm('Удалить правило РД?',
    `${r.brand || 'любой бренд'} × ${r.product_types?.length ? r.product_types.join(', ') : 'любой вид'}`,
    'Правило перестанет действовать при следующем импорте.', 'Удалить',
    () => api(`/v1/nkmt/rules/${r.id}`, { method: 'DELETE' })
      .then(() => { notify('Правило удалено', ''); ctx.bump() })
      .catch((e) => notify('Не удалось удалить', e.message, 'bad')))
  const saveDefs = () => api('/v1/nkmt/defaults', { method: 'PUT', body: JSON.stringify(defs || {}) })
    .then(() => notify('Дефолты сохранены', 'Подставятся при следующем импорте.'))
    .catch((e) => notify('Не сохранено', e.message, 'bad'))
  // пара номер+дата: реестр допускает одинаковые номера с разными датами
  const selDecl = (decls || []).find((d) => d.doc_number === ((defs || {}).declaration_number || '')
    && d.doc_date === ((defs || {}).declaration_date || ''))
  const setDecl = (v) => { if (v === 'manual') return   // значение вне реестра — выбирается только из справочника или очищается
    if (!v) return setDefs({ ...(defs || {}), declaration_number: '', declaration_date: '' })
    const d = (decls || []).find((x) => String(x.id) === v)
    if (d) setDefs({ ...(defs || {}), declaration_number: d.doc_number, declaration_date: d.doc_date }) }
  // управляемый состав дефолтов: убранное поле перестаёт подставляться;
  // декларация — парой номер+дата, техрегламент несъёмный (НК требует всегда)
  const delField = (k) => { const d = { ...(defs || {}) }
    delete d[k]
    if (k === 'declaration_number') delete d.declaration_date
    if (k === 'declaration_date') delete d.declaration_number
    setDefs(d) }
  const addField = (k) => { if (!k || !defs || k in defs) return
    const d = { ...defs, [k]: '' }
    if (k === 'declaration_number') d.declaration_date = d.declaration_date ?? ''
    if (k === 'declaration_date') d.declaration_number = d.declaration_number ?? ''
    setDefs(d) }
  const tryResolve = () => api('/v1/nkmt/resolve', { method: 'POST',
      body: JSON.stringify({ brand: rzBrand, product_type: rzType }) })
    .then(setRz).catch((e) => notify('Проверка не удалась', e.message, 'bad'))
  const saveEm = () => api('/v1/emitter/defaults', { method: 'PUT', body: JSON.stringify(em || {}) })
    .then(() => notify('Реквизиты эмиттера сохранены', 'Применятся к следующим черновикам LK_RECEIPT.'))
    .catch((e) => notify('Не сохранено', e.message, 'bad'))
  const tabs = [['fields', 'Поля'], ['decls', 'Декларации'],
    ['producers', 'Производители'], ['rules', 'Правила'], ['emitter', 'Эмиттер ЧЗ']]
  return <>
    <Head title="Справочники" sub="Значения для карточек НК и документов. Приоритет подстановки: файл → правило РД → дефолт."
      tools={<Sync tick={ctx.tick} />} />
    <div className="chiprow" style={{ marginBottom: 14 }}>
      {tabs.map(([k, l]) => <button key={k} className="chip" aria-pressed={tab === k}
        onClick={() => setTab(k)}>{l}</button>)}
    </div>
    <datalist id="hint-producers">
      {(hints.producers || []).map((p) => <option key={p} value={p} />)}</datalist>
    {tab === 'fields' && <>
      <div className="card">
        <div className="card-h"><h2>Поля и значения по умолчанию</h2>
          <span className="hint">подставляются, если в файле пусто и не сработало правило; состав полей можно менять</span></div>
        <div className="card-b" style={{ borderBottom: '1px solid var(--line)' }}>
          <ul className="loops">
            {defs && DEF_FIELDS.filter(([k]) => k in defs).map(([k, label]) => <li key={k}>
              <span className="nm">{label}{k === 'techreg'
                ? <span className="faint" style={{ fontSize: 11 }}> · системное</span> : null}</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {k === 'declaration_number'
                  ? <select style={{ maxWidth: 400 }} value={selDecl ? String(selDecl.id)
                      : ((defs || {}).declaration_number ? 'manual' : '')}
                      onChange={(e) => setDecl(e.target.value)}>
                      <option value="">— не задано —</option>
                      {(decls || []).map((d) => <option key={d.id} value={String(d.id)}>{d.doc_number} · {d.doc_date}</option>)}
                      {(defs || {}).declaration_number && !selDecl
                        && <option value="manual">{defs.declaration_number} (вне реестра)</option>}
                    </select>
                  : <input style={{ maxWidth: 400, textAlign: 'right', textOverflow: 'ellipsis' }}
                    title={(defs || {})[k] || ''}
                    list={k === 'producer' ? 'hint-producers' : undefined}
                    type={k === 'declaration_date' ? 'date' : 'text'}
                    value={(defs || {})[k] || ''}
                    onChange={(e) => setDefs({ ...(defs || {}), [k]: e.target.value })} />}
                {k !== 'techreg' && <button className="btn sm" title="Убрать поле из дефолтов"
                  onClick={() => delField(k)}>×</button>}
              </span>
            </li>)}
          </ul>
          <div className="frow" style={{ marginTop: 12 }}>
            <div className="field" style={{ flex: 1, maxWidth: 320 }}><label>Добавить поле из доступных</label>
              <select value="" onChange={(e) => addField(e.target.value)}>
                <option value="">— выберите поле —</option>
                {DEF_FIELDS.filter(([k]) => defs && !(k in defs))
                  .map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select></div>
            <button className="btn pri" style={{ alignSelf: 'flex-end' }}
              onClick={saveDefs}>Сохранить дефолты</button>
          </div>
          <div className="note">Убранное поле перестанет подставляться по умолчанию — значение должно прийти из файла или правила (состав применяется кнопкой «Сохранить дефолты»). Техрегламент системный: НК требует его всегда. Подстановку по условию (например, размер ONE SIZE только для шапок) делайте правилом РД на вкладке «Правила».</div>
        </div>
      </div>
      <div className="card">
        <div className="card-h"><h2>Проверка подстановок</h2>
          <span className="hint">что получит карточка для бренда и вида товара — без файла</span></div>
        <div className="card-b">
          <div className="frow">
            <div className="field" style={{ flex: 1 }}><label>Бренд</label>
              <input value={rzBrand} placeholder="пусто = дефолтный бренд"
                onChange={(e) => setRzBrand(e.target.value)} /></div>
            <div className="field" style={{ flex: 1 }}><label>Вид товара</label>
              <input value={rzType} placeholder="например, ШАПКА"
                onChange={(e) => setRzType(e.target.value)} /></div>
            <button className="btn pri" onClick={tryResolve}>Проверить</button>
          </div>
          {rz
            ? <ul className="loops" style={{ marginTop: 6 }}>
                {RZ_FIELDS.filter(([k]) => k in rz).map(([k, label]) => { const cell = rz[k]
                  return <li key={k}><span className="nm">{label}</span>
                    <span className="int" style={{ textAlign: 'right' }}>{cell.value || '—'}
                      {cell.src && <span style={{ display: 'block', fontSize: 11, color: 'var(--faint)' }}>
                        ← {RZ_SRC[cell.src] || cell.src}</span>}</span></li> })}
              </ul>
            : <div className="note">Введите бренд и вид товара и нажмите «Проверить» — увидите, какие декларация, производитель и поля карточки (например, размер для шапок) получит строка ещё до импорта файла. Удобно проверять правила сразу после их настройки.</div>}
        </div>
      </div>
    </>}
    {tab === 'decls' && <div className="card">
      <div className="card-h"><h2>Декларации соответствия</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="hint">номер + дата → данные из ЧЗ подтягиваются сами</span>
          <button className="btn sm" disabled={checkBusy || !(decls || []).length}
            onClick={checkAll}>Проверить в ЧЗ</button></div></div>
      <div className="card-b" style={{ borderBottom: '1px solid var(--line)' }}>
        <div className="frow">
          <div className="field" style={{ flex: 2, minWidth: 220 }}><label>Номер</label>
            <input value={dnum} placeholder="ЕАЭС N RU Д-…" onChange={(e) => setDnum(e.target.value)} /></div>
          <div className="field"><label>Дата</label><input type="date" value={ddate} onChange={(e) => setDdate(e.target.value)} /></div>
          <div className="field"><label>Тип</label><select value={dtype} onChange={(e) => setDtype(e.target.value)}>
            <option value="declaration">декларация</option><option value="certificate">сертификат</option></select></div>
          <button className="btn pri" onClick={addDecl}>Добавить</button></div>
        <div className="frow">
          <div className="field" style={{ flex: 1 }}><label>Название (для себя, опционально)</label>
            <input value={dtitle} placeholder="например, Шапки лёгпром до 2027"
              onChange={(e) => setDtitle(e.target.value)} /></div>
        </div>
      </div>
      <div className="twrap"><table className="t small fit">
        <colgroup><col style={{ width: 30 }} /><col style={{ width: 200 }} /><col style={{ width: 308 }} />
          <col style={{ width: 80 }} /><col style={{ width: 86 }} /><col style={{ width: 150 }} />
          <col style={{ width: 122 }} /><col style={{ width: 130 }} /><col style={{ width: 130 }} /></colgroup>
        <thead><tr><th>№</th><th>Номер</th><th>Продукция / название</th><th>Дата</th>
          <th>Действует до</th><th>Статус</th><th>ТН ВЭД</th><th>Техрегламенты</th>
          <th>Изготовитель</th></tr></thead>
        <tbody>{(decls || []).map((d) => { const [lbl, cls] = declState(d)
          return <tr key={d.id} style={{ cursor: 'pointer' }} onClick={() => openDecl(d)}>
          <td className="num">{d.id}</td>
          <td className="ell mono" style={{ fontSize: 12 }} title={d.doc_number}>{d.doc_number}</td>
          <td className="ell" title={d.product_name || d.title || ''}>
            {d.product_name || d.title || '—'}</td>
          <td className="mono">{d.doc_date}</td>
          <td className="mono">{d.date_to || '—'}</td>
          <td><span className={`bdg ${cls}`}>{lbl}</span></td>
          <td className="ell mono" style={{ fontSize: 11.5 }} title={(d.tnved_list || []).join(', ')}>
            {(d.tnved_list || []).join(', ') || '—'}</td>
          <td className="ell" style={{ fontSize: 12 }} title={d.techregs || ''}>{d.techregs || '—'}</td>
          <td className="ell" style={{ fontSize: 12 }} title={d.manufacturer || ''}>{d.manufacturer || '—'}</td></tr> })}
          {decls && !decls.length && <tr><td colSpan={9}><div className="empty"><b>Деклараций нет</b>Добавьте номер и дату — статус, срок и допустимые ТН ВЭД подтянутся из Честного ЗНАКА автоматически.</div></td></tr>}
        </tbody></table></div>
    </div>}
    {tab === 'producers' && <div className="card">
      <div className="card-h"><h2>Производители</h2>
        <span className="hint">каноническое наименование для карточек НК и подсказок правил</span></div>
      <div className="card-b" style={{ borderBottom: '1px solid var(--line)' }}>
        <div className="frow">
          <div className="field" style={{ flex: 2, minWidth: 220 }}><label>Наименование</label>
            <input value={pname} placeholder="как в атрибуте «Производитель» карточки"
              onChange={(e) => setPname(e.target.value)} /></div>
          <div className="field"><label>ИНН</label>
            <input className="mono" style={{ maxWidth: 160 }} value={pinn} placeholder="10 или 12 цифр"
              onChange={(e) => setPinn(e.target.value)} /></div>
          <div className="field"><label>Тип</label>
            <select value={pkind} onChange={(e) => setPkind(e.target.value)}>
              <option value="">— не указан —</option>
              <option value="entrepreneur">ИП / самозанятый</option>
              <option value="company">юрлицо</option></select></div>
          <div className="field" style={{ flex: 1 }}><label>Примечание</label>
            <input value={pnote} placeholder="опционально"
              onChange={(e) => setPnote(e.target.value)} /></div>
          <button className="btn pri" onClick={addProducer}>Добавить</button>
        </div>
        <div className="note">Производитель из этого справочника появится в подсказках полей «Производитель» (дефолты и правила РД) — каноническое написание попадёт во все карточки одинаково.</div>
      </div>
      <div className="twrap"><table className="t small fit">
        <colgroup><col style={{ width: 480 }} /><col style={{ width: 140 }} /><col style={{ width: 96 }} />
          <col style={{ width: 210 }} /><col style={{ width: 110 }} /></colgroup>
        <thead><tr><th>Наименование</th><th>ИНН</th><th>Тип</th><th>Примечание</th><th></th></tr></thead>
        <tbody>{(producers || []).map((p) => <tr key={p.id}>
          <td className="ell" title={p.name}>{p.name}</td>
          <td className="mono">{p.inn || '—'}</td>
          <td>{p.kind === 'entrepreneur' ? 'ИП' : p.kind === 'company' ? 'юрлицо' : '—'}</td>
          <td className="ell" title={p.note}>{p.note || '—'}</td>
          <td className="actions"><button className="btn sm" onClick={() => delProducer(p)}>Удалить</button></td></tr>)}
          {producers && !producers.length && <tr><td colSpan={5}><div className="empty"><b>Производителей нет</b>Добавьте наименование и ИНН — они появятся в подсказках правил и дефолтов.</div></td></tr>}
        </tbody></table></div>
    </div>}
    {tab === 'rules' && <div className="card">
      <div className="card-h"><h2>Правила РД</h2><span className="hint">условие → подстановка декларации/производителя</span></div>
      <div className="card-b" style={{ borderBottom: '1px solid var(--line)' }}>
        <datalist id="hint-brands">
          {(hints.brands || []).map((b) => <option key={b} value={b} />)}</datalist>
        <datalist id="hint-ptypes">
          {(hints.product_types || []).map((t) => <option key={t} value={t} />)}</datalist>
        <div className="faint" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 8 }}>Когда сработает</div>
        <div className="frow">
          <div className="field" style={{ flex: 1, minWidth: 160 }}><label>Бренд (пусто = любой)</label>
            <input list="hint-brands" value={rbrand} placeholder="вводите — будут подсказки"
              onChange={(e) => setRbrand(e.target.value)} /></div>
          <div className="field" style={{ flex: 2 }}><label>Виды товара — можно несколько (пусто = любой)</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input list="hint-ptypes" style={{ flex: 1, minWidth: 0 }} value={rtypeInput}
                placeholder="например, ШАПКА — Enter или +"
                onChange={(e) => setRtypeInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addRtype() } }} />
              <button className="btn sm" title="Добавить вид" onClick={addRtype}>+</button>
            </div>
            {rtypes.length > 0 && <div className="chiprow" style={{ marginTop: 8 }}>
              {rtypes.map((t) => <span key={t} className="bdg blue" style={{ cursor: 'default' }}>{t}
                <a title="Убрать вид" style={{ marginLeft: 5, cursor: 'pointer' }}
                  onClick={() => setRtypes(rtypes.filter((x) => x !== t))}>×</a></span>)}
            </div>}</div>
        </div>
        <div className="faint" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.06em', margin: '12px 0 8px' }}>Что подставить</div>
        <div className="frow">
          <div className="field" style={{ flex: 2, minWidth: 240 }}><label>Декларация</label>
            <select value={rdecl} onChange={(e) => setRdecl(e.target.value)}>
              <option value="">— выберите —</option>
              {(decls || []).map((d) => <option key={d.id} value={d.id}>
                {d.title ? `${d.title} · ${d.doc_number}` : `${d.doc_number} · ${d.doc_date}`}</option>)}
            </select></div>
          <div className="field" style={{ flex: 1 }}><label>Производитель (опционально)</label>
            <input list="hint-producers" value={rprod} placeholder="вводите — будут подсказки"
              onChange={(e) => setRprod(e.target.value)} /></div>
        </div>
        <div className="frow">
          <div className="field" style={{ flex: 2, minWidth: 260 }}><label>Поле карточки (опционально — например, размер «one size» для шапок)</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <select style={{ maxWidth: 170 }} value={rfKey} onChange={(e) => setRfKey(e.target.value)}>
                {Object.entries(RULE_FIELD_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <input style={{ flex: 1, minWidth: 0 }} value={rfVal} placeholder="значение — например, ONE SIZE"
                onChange={(e) => setRfVal(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addRField() } }} />
              <button className="btn sm" title="Добавить поле" onClick={addRField}>+</button>
            </div>
            {Object.keys(rfields).length > 0 && <div className="chiprow" style={{ marginTop: 8 }}>
              {Object.entries(rfields).map(([k, v]) => <span key={k} className="bdg blue" style={{ cursor: 'default' }}>
                {RULE_FIELD_LABELS[k] || k}: {v}
                <a title="Убрать поле" style={{ marginLeft: 5, cursor: 'pointer' }}
                  onClick={() => { const f = { ...rfields }; delete f[k]; setRfields(f) }}>×</a></span>)}
            </div>}
            <span className="hint">подставится в строку, только если ячейка в файле пустая</span></div>
          <button className="btn pri" style={{ alignSelf: 'flex-end' }} onClick={addRule}>Добавить правило</button>
        </div>
        <div className="note" style={{ marginTop: 10 }}>Как применяется: у строки файла берётся эффективный бренд и вид (из файла или дефолтов); правило подходит, если бренд и вид совпали (без учёта регистра) и вид входит в список. Из подошедших побеждает правило с большим числом условий. Подстановка действует только там, где значение не задано файлом — проверяйте её в «Проверке подстановок» ниже и в предпросмотре импорта.</div>
      </div>
      <div className="twrap"><table className="t small fit">
        <colgroup><col style={{ width: 120 }} /><col style={{ width: 320 }} /><col style={{ width: 216 }} />
          <col style={{ width: 168 }} /><col style={{ width: 186 }} /><col style={{ width: 112 }} /></colgroup>
        <thead><tr><th>Бренд</th><th>Виды товара</th><th>Декларация</th><th>Производитель</th><th>Поля</th><th></th></tr></thead>
        <tbody>{(rules || []).map((r) => { const fs = Object.entries(r.fields || {})
          .map(([k, v]) => `${RULE_FIELD_LABELS[k] || k}: ${v}`).join(' · ')
          return <tr key={r.id}>
          <td className="ell" title={r.brand}>{r.brand || 'любой'}</td>
          <td className="ell" title={r.product_types?.join(', ')}>
            {r.product_types?.length ? r.product_types.join(', ') : 'любой'}</td>
          <td className="ell" title={r.declaration_title
            ? `${r.declaration_title} · ${r.declaration_number}` : r.declaration_number}>
            {r.declaration_number}</td>
          <td className="ell" title={r.producer}>{r.producer || '—'}</td>
          <td className="ell" title={fs}>{fs || '—'}</td>
          <td className="actions"><button className="btn sm" onClick={() => delRule(r)}>Удалить</button></td></tr> })}
          {rules && !rules.length && <tr><td colSpan={6}><div className="empty"><b>Правил нет</b>Пример: вид «ШАПКА» → декларация №…, производитель и размер ONE SIZE. Правило без бренда и видов не создаётся — оно подходило бы всем строкам.</div></td></tr>}
        </tbody></table></div>
    </div>}
    {tab === 'emitter' && <div className="card">
      <div className="card-h"><h2>Эмиттер документов ЧЗ</h2><span className="hint">реквизиты для LK_RECEIPT</span></div>
      <div className="card-b">
        <div className="field"><label>ИНН продавца</label>
          <input className="mono" style={{ maxWidth: 220 }} value={inn}
            onChange={(e) => { setInn(e.target.value); localStorage.setItem('inn', e.target.value) }} /></div>
        <div className="field"><label>ФИАС места отгрузки (МОД)</label>
          <input className="mono" style={{ fontSize: 12 }} value={(em || {}).fias_id || ''}
            onChange={(e) => setEm({ ...(em || {}), fias_id: e.target.value })} />
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>Прод ЧЗ отклоняет DISTANCE без ФИАС — не оставляйте пустым.</span></div>
        <div className="field"><label>Наименование первички для чеков без фискального знака</label>
          <input value={(em || {}).primary_custom_name || ''}
            onChange={(e) => setEm({ ...(em || {}), primary_custom_name: e.target.value })} /></div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button className="btn pri" onClick={saveEm}>Сохранить реквизиты</button></div>
      </div>
    </div>}
  </>
}

/* ================= журнал КМ ================= */
/* карточка разбора аномалии: объяснение + факты события + пресеты разрешения */
function AnomalyCard({ it, ctx }) {
  const { notify, confirm, closeDrawer, bump } = ctx
  const help = ANOMALY_HELP[it.state] || {}
  const ev = it.last_event || {}
  const [busy, setBusy] = useState(false)
  const kindRu = opRu(ev)
  const resolve = (p) => confirm(`Разобрать: ${p.label}?`,
    `Код перейдёт в состояние «${ITEM_STATES[p.target][0]}», решение зафиксируется в журнале (кто, когда, почему). Действие необратимо.`,
    it.km, p.label, async () => {
      setBusy(true)
      try {
        await api(`/v1/journal/${encodeURIComponent(it.km)}/resolve`,
          { method: 'POST', body: JSON.stringify({ target: p.target, note: p.note }) })
        notify('Аномалия разобрана', `${p.note} · код → «${ITEM_STATES[p.target][0]}»`)
        closeDrawer(); bump()
      } catch (e) { notify('Не удалось разобрать', e.message, 'bad'); setBusy(false) } })
  return <div>
    <div style={{ marginBottom: 12 }}><KmCell km={it.km} /></div>
    <p>{help.what}</p>
    {help.why?.length ? <>
      <b style={{ fontSize: 12.5 }}>Почему бывает</b>
      <ul style={{ margin: '6px 0 12px', paddingLeft: 18, fontSize: 12.5, color: 'var(--muted)' }}>
        {help.why.map((w) => <li key={w} style={{ marginBottom: 2 }}>{w}</li>)}
      </ul></> : null}
    <div className="note" style={{ marginBottom: 12 }}>{help.todo}</div>
    <b style={{ fontSize: 12.5 }}>Событие, создавшее аномалию</b>
    <div className="twrap" style={{ margin: '6px 0 14px' }}><table className="t small"><tbody>
      <tr><td className="faint" style={{ width: '40%' }}>Вид события</td><td>{kindRu}</td></tr>
      <tr><td className="faint">Дата чека</td><td>{fmtDay(ev.fiscal_dt)}</td></tr>
      <tr><td className="faint">Чек ККТ</td><td className="mono">{ev.fiscal_doc_number ?? '—'}</td></tr>
      <tr><td className="faint">Цена</td><td>{ev.price ? rub(ev.price) : '—'}</td></tr>
      <tr><td className="faint">nm_id</td><td className="mono">{ev.nm_id ?? '—'}</td></tr>
      <tr><td className="faint">Заказ (srid)</td>
        <td className="mono" style={{ wordBreak: 'break-all' }}>{ev.srid || '—'}</td></tr>
    </tbody></table></div>
    <b style={{ fontSize: 12.5 }}>Разобрать</b>
    <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
      {(help.presets || []).map((p) => (
        <button key={p.label} className="btn" disabled={busy} onClick={() => resolve(p)}>{p.label}</button>))}
      <button className="btn" disabled={busy} onClick={() => { closeDrawer(); ctx.go('trace', it.km) }}>Трассировка</button>
    </div>
    <details style={{ marginTop: 14 }}>
      <summary style={{ fontSize: 12, color: 'var(--muted)', cursor: 'pointer' }}>Сырые данные события</summary>
      <pre>{JSON.stringify(ev, null, 2)}</pre>
    </details>
  </div>
}

/* карточка штатного КМ: статус ЧЗ + проверка + ручной люк + сигнал WB */
function KmCard({ it, ctx }) {
  const { notify, confirm, bump } = ctx
  const [st, setSt] = useState(it)
  const [busy, setBusy] = useState(false)
  const ev = st.last_event || {}
  const checkCis = async () => {
    setBusy(true)
    try { const r = await api('/v1/journal/cis-sync',
        { method: 'POST', body: JSON.stringify({ kms: [st.km] }) })
      const upd = r.items[0] || {}
      setSt((s) => ({ ...s, ...upd }))
      notify('Код проверен в ЧЗ', CIS_STATUS[upd.cis_status]?.[0] || upd.cis_status || 'статус неизвестен')
      bump()
    } catch (e) { notify('Проверка не удалась', e.message, 'bad') } finally { setBusy(false) } }
  const markWb = () => confirm('Пометить: код выведен WB?',
    'Код перейдёт в состояние «выведен» с пометкой «вывел WB» (вывод по чеку ККТ Wildberries, не нашим документом). Действие необратимо.',
    st.km, 'Выведен WB', async () => {
      setBusy(true)
      try { await api(`/v1/journal/${encodeURIComponent(st.km)}/withdraw-source`,
          { method: 'POST', body: JSON.stringify({ by: 'wb' }) })
        notify('Код помечен', 'вывел WB · состояние «выведен»')
        ctx.closeDrawer(); bump()
      } catch (e) { notify('Не удалось пометить', e.message, 'bad'); setBusy(false) } })
  return <div>
    <div style={{ marginBottom: 12 }}><KmCell km={st.km} /></div>
    <b style={{ fontSize: 12.5 }}>Честный знак</b>
    <div className="twrap" style={{ margin: '6px 0 8px' }}><table className="t small"><tbody>
      <tr><td className="faint" style={{ width: '40%' }}>Статус КИЗ</td>
        <td>{st.cis_status ? <Badge dict={CIS_STATUS} v={st.cis_status} />
          : <span className="faint">не проверялся</span>}</td></tr>
      <tr><td className="faint">Наименование</td><td>{st.cis_product_name || '—'}</td></tr>
      <tr><td className="faint">Проверено</td><td>{st.cis_checked_at ? fmtD(st.cis_checked_at) : '—'}</td></tr>
    </tbody></table></div>
    <div className="row" style={{ gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
      <button className="btn" disabled={busy} onClick={checkCis}>Проверить в ЧЗ</button>
      {st.cis_status === 'retired' && st.state === 'PENDING_WITHDRAW' &&
        <button className="btn" disabled={busy} onClick={markWb}>Выведен WB</button>}
      <button className="btn" disabled={busy} onClick={() => ctx.go('trace', st.km)}>Трассировка</button>
    </div>
    <b style={{ fontSize: 12.5 }}>Последний сигнал WB</b>
    <div className="twrap" style={{ margin: '6px 0 8px' }}><table className="t small"><tbody>
      <tr><td className="faint" style={{ width: '40%' }}>Вид события</td><td>{opRu(ev)}</td></tr>
      <tr><td className="faint">Дата чека</td><td>{fmtDay(ev.fiscal_dt)}</td></tr>
      <tr><td className="faint">Чек ККТ</td><td className="mono">{ev.fiscal_doc_number ?? '—'}</td></tr>
      <tr><td className="faint">Цена</td><td>{ev.price ? rub(ev.price) : '—'}</td></tr>
      <tr><td className="faint">nm_id</td><td className="mono">{ev.nm_id ?? '—'}</td></tr>
      <tr><td className="faint">Заказ (srid)</td>
        <td className="mono" style={{ wordBreak: 'break-all' }}>{ev.srid || '—'}</td></tr>
    </tbody></table></div>
    <details style={{ marginTop: 10 }}>
      <summary style={{ fontSize: 12, color: 'var(--muted)', cursor: 'pointer' }}>Сырые данные события</summary>
      <pre>{JSON.stringify(ev, null, 2)}</pre>
    </details>
  </div>
}

/* карточка заказа WB (результат lookup из поиска журнала) */
function OrderCard({ data, ctx }) {
  const { openDrawer } = ctx
  const o = data.order
  return <div>
    <div style={{ marginBottom: 12 }}><KmCell km={data.order_doc} /></div>
    <div className="note" style={{ marginBottom: 12 }}>{ORDER_LOOKUP_STATUS[data.status] || ''}</div>
    {data.status === 'found' && !o &&
      <p style={{ fontSize: 12.5, color: 'var(--muted)', margin: '0 0 14px' }}>
        Сам заказ уже ушёл из снапшота WB: выкупленные заказы исчезают из него
        на 1–3 дня раньше, чем приходят строки продаж. На данные журнала это не влияет.</p>}
    {o ? <><b style={{ fontSize: 12.5 }}>Реестр WB</b>
      <div className="twrap" style={{ margin: '6px 0 14px' }}><table className="t small"><tbody>
        <tr><td className="faint" style={{ width: '40%' }}>Тип доставки</td>
          <td>{WB_DELIVERY[o.delivery_type] || o.delivery_type || '—'}</td></tr>
        <tr><td className="faint">nm_id</td><td className="mono">{o.nm_id ?? '—'}</td></tr>
        <tr><td className="faint">Создан</td>
          <td className="mono">{o.order_created_at ? fmtDay(o.order_created_at) : '—'}</td></tr>
        <tr><td className="faint">Видели в реестре</td><td className="mono">{fmtD(o.last_seen)}</td></tr>
      </tbody></table></div></>
      : <p style={{ fontSize: 12.5, color: 'var(--muted)', margin: '0 0 14px' }}>В реестре WB этого заказа нет.</p>}
    <b style={{ fontSize: 12.5 }}>Коды маркировки ({data.items.length})</b>
    {data.items.length ? <div className="twrap" style={{ margin: '6px 0 8px' }}>
      <table className="t small">
        <thead><tr><th>Код</th><th>Состояние</th><th>Последний сигнал</th></tr></thead>
        <tbody>{data.items.map((it) => { const [lbl] = ITEM_STATES[it.state] || [it.state]
          return <tr key={it.km} style={{ cursor: 'pointer' }}
            onClick={() => openDrawer(<>КМ · {lbl} ·&nbsp;<span className="mono"
              style={{ fontSize: 12, color: 'var(--muted)' }}>{it.state}</span></>,
              <KmCard it={it} ctx={ctx} />)}>
            <td><KmCell km={it.km} /></td>
            <td><Badge dict={ITEM_STATES} v={it.state} /></td>
            <td style={{ fontSize: 12.5 }}>{evLine(it)}</td></tr> })}
        </tbody></table></div>
      : <div className="empty"><b>Кодов по заказу нет</b>Пояснение выше — почему их нет и что делать.</div>}
  </div>
}

/* декларативный конфиг колонок журнала — фундамент под будущее управление
   составом колонок (переключатели пока не делаем): w — ширина col в px
   (фиксированный лейаут; без w — колонка забирает остаток), ell — однострочная
   обрезка с кликом-раскрытием, mono — кодовая колонка (DESIGN.md 11) */
const JOURNAL_COLUMNS = [
  { key: 'km', label: 'Код маркировки', w: 248, render: (it) => <KmCell km={it.km} /> },
  { key: 'name', label: 'Наименование', w: 238, ell: true, text: (it) => it.cis_product_name || '',
    render: (it) => it.cis_product_name || <span className="faint">—</span> },
  { key: 'state', label: 'Состояние', w: 126, render: (it) => <Badge dict={ITEM_STATES} v={it.state} /> },
  { key: 'sig', label: 'Последний сигнал', w: 194, ell: true, text: (it) => evLine(it),
    render: (it) => evLine(it) },
  { key: 'order', label: 'Заказ WB', w: 224, ell: true, mono: true, text: (it) => it.last_event?.srid || '',
    render: (it) => it.last_event?.srid || <span className="faint">—</span> },
  { key: 'cz', label: 'ЧЗ', w: 120, render: (it) => it.cis_status ? <Badge dict={CIS_STATUS} v={it.cis_status} /> : <span className="faint">—</span> },
  { key: 'upd', label: 'Обновлён', w: 86, mono: true, render: (it) => fmtD(it.updated_at) },
]

// ID заказа WB: [префикс.]тело[.n.m]. Тела реальных rid двух видов (фикстура
// эксайза): 32–33 alnum (маркер i/r + hex) и uuid с дефисами; префикс бывает
// 'eBQ' и служебный 'WH_RO_SRN_MW'. КМ не матчится (КМ начинается с '01',
// содержит GS-разделитель '!' и ':'-криптогруппы не из этого алфавита)
const RID_BODY = '(?:[0-9a-z]{32,33}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})'
const ORDER_ID_RE = new RegExp(`^(?:[0-9a-z_]{1,16}\\.)?${RID_BODY}(?:\\.\\d+\\.\\d+)?$`, 'i')
const isOrderId = (s) => { const v = (s || '').trim().toLowerCase()
  return !v.startsWith('01') && !v.includes('!') && ORDER_ID_RE.test(v) }

function Journal({ ctx, initial }) {
  const { openDrawer, notify, confirm, bump } = ctx
  const [rows, setRows] = useState(null)
  const [stats, setStats] = useState({})
  const [state, setState] = useState(initial || '')
  const [q, setQ] = useState('')
  const [syncBusy, setSyncBusy] = useState(false)
  const lastLookup = useRef('')
  useEffect(() => { api(`/v1/journal?limit=1000${state && state !== 'ANOMALY' ? `&state=${encodeURIComponent(state)}` : ''}`)
      .then(setRows).catch(() => setRows([]))
    api('/v1/journal/stats').then(setStats).catch(() => {}) }, [ctx.tick, state])
  const qv = q.trim().toLowerCase()
  // вставка ID заказа — фильтр по документу (без хвоста '.n.m'): строка с
  // любым суффиксом этого заказа остаётся видимой под открывшейся карточкой
  const qDoc = isOrderId(q) ? qv.replace(/\.\d+\.\d+$/, '') : null
  const shown = (rows || []).filter((it) =>
    (state !== 'ANOMALY' || it.state.startsWith('ANOMALY'))
    && (!qv || it.km.toLowerCase().includes(qv) || evLine(it).toLowerCase().includes(qv)
      || (qDoc && (it.last_event?.srid || '').toLowerCase().startsWith(qDoc))))
  const anomalies = Object.entries(stats).filter(([k]) => k.startsWith('ANOMALY')).reduce((a, [, v]) => a + v, 0)
  const total = Object.values(stats).reduce((a, v) => a + v, 0)
  const lookupOrder = async (val) => { const rid = val.trim()
    lastLookup.current = rid
    try { const r = await api(`/v1/wb/lookup?rid=${encodeURIComponent(rid)}`)
      openDrawer(<>Заказ WB ·&nbsp;<span className="mono"
        style={{ fontSize: 12, color: 'var(--muted)' }}>{r.order_doc}</span></>,
        <OrderCard data={r} ctx={ctx} />)
    } catch (e) { lastLookup.current = ''; notify('Lookup не удался', e.message, 'bad') } }
  const onQ = (e) => { const v = e.target.value; setQ(v)
    if (isOrderId(v) && v.trim() !== lastLookup.current) lookupOrder(v) }
  const doSync = () => confirm('Обновить статусы ЧЗ?',
    `Все коды журнала (${total}) будут проверены в Честном Знаке. Коды, которые ЧЗ уже считает выведенными и по которым у нас нет поданной заявки на вывод, перейдут в «выведен (WB)» с записью в журнал.`,
    `${total} КМ`, 'Проверить в ЧЗ', async () => {
      setSyncBusy(true)
      try { const r = await api('/v1/journal/cis-sync', { method: 'POST', body: JSON.stringify({}) })
        notify('Статусы ЧЗ обновлены',
          `проверено ${r.checked} · переведено «вывел WB» ${r.translated}${r.errors ? ` · ошибок ${r.errors}` : ''}`)
        bump()
      } catch (e) { notify('Синхронизация не удалась', e.message, 'bad') } finally { setSyncBusy(false) } })
  return <>
    <Head title="Журнал кодов маркировки" sub="Жизненный цикл каждого КМ: продажа → вывод из оборота → возврат. Красные строки — противоречия в данных: клик по строке объясняет причину и позволяет разобрать."
      tools={<><Sync tick={ctx.tick} />
        <button className="btn sm" disabled={syncBusy || !total} onClick={doSync}>Обновить статусы ЧЗ</button></>} />
    <div className="chiprow" style={{ marginBottom: 14 }}>
      <button className="chip" aria-pressed={state === ''} onClick={() => setState('')}>все состояния <span className="n">{total}</span></button>
      {anomalies > 0 && <button className="chip alert" aria-pressed={state === 'ANOMALY'} onClick={() => setState('ANOMALY')}>аномалии <span className="n">{anomalies}</span></button>}
      {CHIP_ORDER.filter((s) => stats[s] || s === state).map((s) => {
        const [lbl, cls] = ITEM_STATES[s]
        return <button key={s} className={`chip${cls === 'red' ? ' alert' : ''}`} aria-pressed={state === s}
          onClick={() => setState(s)}>{lbl} <span className="n">{stats[s] || 0}</span></button> })}
    </div>
    <div className="frow" style={{ marginBottom: 14 }}>
      <div className="search" style={{ flex: '1 1 380px', maxWidth: 560 }}>{I.search}
        <input value={q} placeholder="Поиск по КМ, событию или ID заказа WB…"
          onChange={onQ}
          onKeyDown={(e) => { if (e.key === 'Enter' && isOrderId(q)) lookupOrder(q) }} /></div>
      <span className="faint" style={{ fontSize: 12 }}>показано <span className="mono">{shown.length}</span>
        {isOrderId(q) && <> · Enter — карточка заказа WB</>}</span>
    </div>
    <div className="card">
      <div className="twrap"><table className="t fit">
        <colgroup>{JOURNAL_COLUMNS.map((c) => <col key={c.key} style={c.w ? { width: c.w } : undefined} />)}</colgroup>
        <thead><tr>{JOURNAL_COLUMNS.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead>
        <tbody>{shown.map((it) => { const [lbl] = ITEM_STATES[it.state] || [it.state]
          const anom = it.state.startsWith('ANOMALY')
          return <tr key={it.km} className={anom ? 'rowhot' : ''} style={{ cursor: 'pointer' }}
            onClick={() => openDrawer(<>КМ · {lbl} ·&nbsp;<span className="mono"
              style={{ fontSize: 12, color: 'var(--muted)' }}>{it.state}</span></>,
              anom
                ? <AnomalyCard it={it} ctx={ctx} />
                : <KmCard it={it} ctx={ctx} />)}>
            {JOURNAL_COLUMNS.map((c) => c.ell
              ? <EllCell key={c.key} title={c.text(it)} mono={c.mono}>{c.render(it)}</EllCell>
              : <td key={c.key} className={c.mono ? 'mono' : undefined}>{c.render(it)}</td>)}
          </tr> })}
          {rows && !shown.length && <tr><td colSpan={JOURNAL_COLUMNS.length}><div className="empty"><b>Ничего не найдено</b>Ослабьте фильтр или очистите поиск.</div></td></tr>}
        </tbody></table></div>
    </div>
  </>
}

/* трассировка КМ: жизненный цикл одного кода по системам (ЧЗ/МАРКО/WB).
   Точечный запрос — не грузится по 60с-тику; живые проверки (ЧЗ, WB) —
   кнопками, фоновых опросов и новых словарей статусов не заводим */
function Trace({ ctx, initial }) {
  const { notify, openDrawer, go, bump } = ctx
  const [q, setQ] = useState(initial || '')
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [czBusy, setCzBusy] = useState(false)
  const [wbBusy, setWbBusy] = useState(false)
  const [wbMeta, setWbMeta] = useState(null)
  const [unknownCz, setUnknownCz] = useState(null)
  const run = async (val, keepWb = false) => { const km = (val ?? q).trim()
    if (!km) return
    setBusy(true); setUnknownCz(null)
    if (!keepWb) setWbMeta(null)
    try { setData(await api(`/v1/trace?km=${encodeURIComponent(km)}`)) }
    catch (e) { setData(null); notify('Трассировка не удалась', e.message, 'bad') }
    finally { setBusy(false) } }
  useEffect(() => { if (initial) run(initial) }, [])
  const checkCis = async () => {
    if (!data) return
    setCzBusy(true)
    try {
      const r = await api('/v1/journal/cis-sync',
        { method: 'POST', body: JSON.stringify({ kms: [data.km] }) })
      const info = (r.infos || [])[0]
      if (r.items?.length) { await run(data.input, true)
        notify('Код проверен в ЧЗ', CIS_STATUS[r.items[0].cis_status]?.[0] || '') }
      else if (info) { setUnknownCz(info)
        notify('Код проверен в ЧЗ', info.status ? (CIS_STATUS[info.status]?.[0] || info.status) : (info.error || 'нет данных')) }
      else notify('Проверка ЧЗ', 'код не найден', 'warn')
      bump()
    } catch (e) { notify('Проверка не удалась', e.message, 'bad') } finally { setCzBusy(false) } }
  const fetchWbMeta = async () => {
    if (!data) return
    setWbBusy(true)
    try { setWbMeta(await api('/v1/trace/wb-meta',
        { method: 'POST', body: JSON.stringify({ km: data.km }) })) }
    catch (e) { notify('WB не ответил', `${e.message} — повторите через минуту`, 'bad') }
    finally { setWbBusy(false) } }
  const evDrawer = (e) => openDrawer(<>Событие ·&nbsp;<span className="mono"
    style={{ fontSize: 12, color: 'var(--muted)' }}>{e.kind}</span></>,
    <div><div className="twrap" style={{ marginBottom: 10 }}><table className="t small"><tbody>
      <tr><td className="faint" style={{ width: '40%' }}>Система</td><td><Badge dict={TRACE_SYSTEMS} v={e.system} /></td></tr>
      <tr><td className="faint">Событие</td><td>{e.title}</td></tr>
      <tr><td className="faint">Момент</td><td className="mono">{fmtDay(e.ts)}</td></tr>
      <tr><td className="faint">Детали</td><td>{e.detail || '—'}</td></tr>
    </tbody></table></div>
      <details open><summary style={{ fontSize: 12, color: 'var(--muted)', cursor: 'pointer' }}>Сырые данные события</summary>
        <pre>{JSON.stringify(e.payload, null, 2)}</pre></details></div>)
  const docDrawer = async (d) => { let full = null
    try { full = await api(`/v1/docs/${d.id}`) } catch {}
    openDrawer(`Документ №${d.id} · ${d.type}`,
      <div><div className="twrap" style={{ marginBottom: 10 }}><table className="t small"><tbody>
        <tr><td className="faint" style={{ width: '40%' }}>Статус</td><td><Badge dict={DOC_STATUS} v={d.status} /></td></tr>
        <tr><td className="faint">uuid ЧЗ</td><td className="mono" style={{ wordBreak: 'break-all' }}>{d.external_id || '—'}</td></tr>
        <tr><td className="faint">Создан</td><td className="mono">{fmtD(d.created_at)}</td></tr>
      </tbody></table></div>
        <details open><summary style={{ fontSize: 12, color: 'var(--muted)', cursor: 'pointer' }}>Позиции документа</summary>
          <pre>{JSON.stringify(full?.payload ?? d, null, 2)}</pre></details></div>) }
  const it = data?.item
  const name = data?.item?.cis_product_name || data?.card?.name || ''
  return <>
    <Head title="Трассировка кода маркировки" sub="Жизненный цикл одного КМ по системам: сигналы Wildberries, наши документы ЧЗ, наблюдения Честного знака. Живые проверки — кнопками, фоновых опросов нет."
      tools={<Sync tick={ctx.tick} />} />
    <div className="frow" style={{ marginBottom: 14 }}>
      <div className="search" style={{ flex: '1 1 380px', maxWidth: 560 }}>{I.search}
        <input value={q} placeholder="КИЗ (с криптохвостом) или короткий КМ…"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') run() }} /></div>
      <button className="btn pri" disabled={busy || !q.trim()} onClick={() => run()}>Проследить</button>
      <span className="faint" style={{ fontSize: 12 }}>{busy ? 'ищем…' : 'Enter — тоже'}</span>
    </div>
    {data && <>
      <div className="card" style={{ marginBottom: 22 }}>
        <div className="card-h"><b style={{ fontSize: 12.5 }}>{data.km === data.input.trim() ? 'Код' : 'Код (нормализован)'}</b>
          <span className="hint">событий: {data.counts.events}</span></div>
        <div className="card-b">
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
            <KmCell km={data.km} />
            {it && <Badge dict={ITEM_STATES} v={it.state} />}
            {it?.withdrawn_by === 'wb' && <span className="bdg grey">вывел WB</span>}
            {it?.cis_status ? <Badge dict={CIS_STATUS} v={it.cis_status} /> : <span className="faint">ЧЗ: не проверялся</span>}
            {unknownCz && !it && (unknownCz.status
              ? <Badge dict={CIS_STATUS} v={unknownCz.status} />
              : <span className="bdg grey">{unknownCz.error || 'нет данных ЧЗ'}</span>)}
          </div>
          <div className="twrap"><table className="t small"><tbody>
            <tr><td className="faint" style={{ width: '40%' }}>Наименование</td>
              <td>{name || <span className="faint">—</span>}</td></tr>
            <tr><td className="faint">GTIN</td><td className="mono">{data.gtin}</td></tr>
            {data.card && <tr><td className="faint">Карточка НК</td>
              <td>{data.card.article} · {data.card.name} <Badge dict={CARD_STATUS} v={data.card.status} /></td></tr>}
            {it?.cis_checked_at && <tr><td className="faint">Проверен в ЧЗ</td>
              <td className="mono">{fmtD(it.cis_checked_at)}</td></tr>}
            {it && <tr><td className="faint">Последний сигнал</td><td>{evLine(it)}</td></tr>}
          </tbody></table></div>
          <div className="row" style={{ gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <button className="btn" disabled={czBusy} onClick={checkCis}>Проверить в ЧЗ</button>
            <button className="btn" disabled={wbBusy || !(data.orders.length || data.returns.some((r) => r.order_id > 0))}
              title={data.orders.length || data.returns.some((r) => r.order_id > 0) ? '' : 'по событиям кода нет известных заказов WB'} onClick={fetchWbMeta}>Статусы закрепления WB</button>
            {data.card && <button className="btn" onClick={() => go('catalog')}>Открыть каталог НК</button>}
          </div>
          {!data.found && <div className="empty" style={{ marginTop: 12 }}>
            <b>Код не наблюдается контуром МАРКО</b>
            Продаж и возвратов по нему через наш FBS не было — код другой партии или кабинета. Проверку в Честном знаке кнопка выше выполняет и для таких кодов.
          </div>}
        </div>
      </div>
      {wbMeta && <div className="card" style={{ marginBottom: 22 }}>
        <div className="card-h"><b style={{ fontSize: 12.5 }}>Закрепления на WB</b>
          <span className="hint">live · {wbMeta.fetched_at ? fmtD(wbMeta.fetched_at) : ''}</span></div>
        <div className="card-b">
          {wbMeta.note ? <p style={{ fontSize: 12.5, color: 'var(--muted)', margin: 0 }}>{wbMeta.note}</p>
            : wbMeta.orders.map((o) => <div key={o.id} style={{ marginBottom: 10 }}>
              <span className="mono" style={{ fontSize: 12 }}>заказ {o.id}</span>
              {!o.sgtins.length && <span className="faint" style={{ fontSize: 12 }}> — кодов не закреплено</span>}
              {o.sgtins.map((s, i) => <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', marginTop: 2 }}>
                <span className="km" style={{ fontSize: 11 }}>{s.sgtin || '—'}</span>
                <span>{SGTIN_DECISION[s.decision] || <span className="mono" style={{ fontSize: 12 }}>{s.decision || '—'}</span>}</span>
                {s.sgtin && s.sgtin.startsWith(data.km) && <span className="bdg blue">этот код</span>}
              </div>)}
            </div>)}
        </div>
      </div>}
      <div className="card" style={{ marginBottom: 22 }}>
        <div className="card-h"><b style={{ fontSize: 12.5 }}>Хронология</b>
          <span className="hint">клик по строке — сырые данные события</span></div>
        {data.timeline.length ? <ul className="feed">
          {data.timeline.map((e) => <li key={e.id} tabIndex={0} style={{ cursor: 'pointer' }}
            onClick={() => evDrawer(e)}
            onKeyDown={(ev2) => { if (ev2.key === 'Enter') evDrawer(e) }}>
            <time>{fmtDay(e.ts)}</time>
            <span className="dotsep" style={{ background: TRACE_DOT[e.kind] || 'var(--line-strong)' }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <Badge dict={TRACE_SYSTEMS} v={e.system} />
                <span>{e.title}</span>
              </div>
              {e.detail && <div className="sm" style={{ color: 'var(--muted)', marginTop: 2 }}>{e.detail}</div>}
            </div>
          </li>)}
        </ul> : <div className="empty"><b>Событий нет</b>Код не встречался ни в одном контуре платформы.</div>}
      </div>
      {data.docs.length > 0 && <div className="card" style={{ marginBottom: 22 }}>
        <div className="card-h"><b style={{ fontSize: 12.5 }}>Документы ЧЗ ({data.docs.length})</b>
          <span className="hint">клик — позиции и uuid</span></div>
        <div className="twrap"><table className="t small fit">
          <colgroup><col style={{ width: 90 }} /><col style={{ width: 150 }} /><col style={{ width: 130 }} /><col style={{ width: 120 }} /></colgroup>
          <thead><tr><th>№</th><th>Тип</th><th>Статус</th><th>Создан</th></tr></thead>
          <tbody>{data.docs.map((d) => <tr key={d.id} style={{ cursor: 'pointer' }} onClick={() => docDrawer(d)}>
            <td className="mono">№{d.id}</td>
            <td className="mono ell" title={d.type}>{d.type}</td>
            <td><Badge dict={DOC_STATUS} v={d.status} /></td>
            <td className="mono">{fmtD(d.created_at)}</td>
          </tr>)}</tbody></table></div>
      </div>}
      {(data.orders.length > 0 || data.returns.length > 0) && <div className="card">
        <div className="card-h"><b style={{ fontSize: 12.5 }}>Wildberries</b></div>
        {data.orders.length > 0 && <><b style={{ fontSize: 12.5 }}>Заказы ({data.orders.length})</b>
          <div className="twrap" style={{ margin: '6px 0 14px' }}><table className="t small fit">
            <colgroup><col style={{ width: 278 }} /><col style={{ width: 130 }} /><col style={{ width: 90 }} /><col style={{ width: 110 }} /><col style={{ width: 150 }} /></colgroup>
            <thead><tr><th>Документ</th><th>ID задания</th><th>Тип</th><th>nm_id</th><th>Создан</th></tr></thead>
            <tbody>{data.orders.map((o) => <tr key={o.order_doc}>
              <td className="mono ell" title={o.order_doc}>{o.order_doc}</td>
              <td className="mono">{o.order_id ?? '—'}</td>
              <td>{WB_DELIVERY[o.delivery_type] || o.delivery_type || '—'}</td>
              <td className="mono">{o.nm_id ?? '—'}</td>
              <td className="mono">{o.order_created_at ? fmtDay(o.order_created_at) : '—'}</td>
            </tr>)}</tbody></table></div></>}
        {data.returns.length > 0 && <><b style={{ fontSize: 12.5 }}>Возвраты на ПВЗ ({data.returns.length})</b>
          <div className="twrap" style={{ margin: '6px 0 0' }}><table className="t small fit">
            <colgroup><col style={{ width: 278 }} /><col style={{ width: 130 }} /><col style={{ width: 190 }} /><col style={{ width: 140 }} /><col style={{ width: 110 }} /></colgroup>
            <thead><tr><th>Возврат (srid)</th><th>ID задания</th><th>Статус</th><th>Причина</th><th>Дедлайн</th></tr></thead>
            <tbody>{data.returns.map((r) => <tr key={r.srid}>
              <td className="mono ell" title={r.srid}>{r.srid}</td>
              <td className="mono">{r.order_id || '—'}</td>
              <td className="ell" title={`${r.status || ''}${r.is_active ? '' : ' · завершён'}`}>{r.status || '—'}</td>
              <td className="ell" title={r.reason || ''}>{r.reason || '—'}</td>
              <td className="mono">{r.expired_dt ? fmtDay(r.expired_dt) : '—'}</td>
            </tr>)}</tbody></table></div></>}
      </div>}
    </>}
    {!data && !busy && <div className="card"><div className="empty">
      <b>Введите код маркировки</b>
      Отсканируйте или вставьте КИЗ — полный (с криптохвостом) или короткий КМ. Платформа соберёт всё, что наблюдала по коду: сигналы WB, документы ЧЗ, статусы Честного знака.
    </div></div>}
  </>
}

/* ================= консоль ================= */
const NAV = [
  ['overview', 'Обзор', I.pulse],
  ['withdraw', 'Вывод из оборота', I.swap],
  ['returns', 'Возвраты', I.back],
  ['catalog', 'Каталог НК', I.grid],
  ['refs', 'Справочники', I.book],
  ['journal', 'Журнал КМ', I.list],
  ['trace', 'Трассировка', I.clock],
]

function Console({ me, logout }) {
  const [view, setView] = useState('overview')
  const [jInit, setJInit] = useState('')
  const [tInit, setTInit] = useState('')
  const [pulse, setPulse] = useState(null)
  const [tick, setTick] = useState(Date.now())
  const [inn, setInn] = useState(localStorage.getItem('inn') || '090201471350')
  const [toasts, setToasts] = useState([])
  const [modal, setModal] = useState(null)
  const [drawer, setDrawer] = useState(null)
  const [wide, setWide] = useState(null)
  const bump = () => setTick(Date.now())
  const notify = (title, text = '', kind = '') => { const id = Math.random()
    setToasts((t) => [...t, { id, title, text, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500) }
  const confirm = (title, text, detail, okLabel, action) => setModal({ title, text, detail, okLabel, action })
  const openDrawer = (title, node) => setDrawer({ title, node })
  const openWide = (title, node) => setWide({ title, node })
  const go = (v, jf) => { if (jf != null) { setJInit(jf); setTInit(jf) } setView(v); window.scrollTo(0, 0) }
  useEffect(() => { const i = setInterval(bump, 60000); return () => clearInterval(i) }, [])
  useEffect(() => { api('/v1/pulse').then(setPulse).catch(() => {}) }, [tick])
  // Esc закрывает верхний слой: confirm → широкая модалка → drawer (DESIGN.md 7)
  useEffect(() => {
    const onKey = (e) => { if (e.key !== 'Escape') return
      if (modal) setModal(null)
      else if (wide) setWide(null)
      else if (drawer) setDrawer(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [modal, wide, drawer])
  const ctx = { notify, confirm, openDrawer, closeDrawer: () => setDrawer(null),
    openWide, closeWide: () => setWide(null),
    bump, tick, inn, setInn, go, pulse }
  const s = pulse?.stats || {}
  const navCnt = { overview: null, withdraw: s.PENDING_WITHDRAW || 0,
    returns: pulse?.returns?.pending_return || 0,
    catalog: pulse ? Object.entries(pulse.batches || {})
      .filter(([k]) => !['published', 'error'].includes(k)).reduce((a, [, v]) => a + v, 0) : 0,
    refs: null,
    journal: Object.entries(s).filter(([k]) => k.startsWith('ANOMALY')).reduce((a, [, v]) => a + v, 0),
    trace: null }
  const navBtn = (v) => { const [key, lbl, icon] = NAV.find((x) => x[0] === v)
    return <button key={key} className="nav-item" aria-current={view === key}
      onClick={() => go(key)}>{icon}<span className="lbl">{lbl}</span>
      {navCnt[key] ? <span className="cnt">{navCnt[key]}</span> : null}</button> }
  return <div id="app">
    <aside className="rail">
      <div className="brand"><Mark size={34} /><div><b>МАРКО</b><span>Честный знак · нацкат · WB</span></div></div>
      <nav className="nav" aria-label="Разделы">{NAV.map((x) => navBtn(x[0]))}</nav>
      <div className="rail-foot">
        <span className="who">оператор · {(me.scopes || []).join(', ')}</span>
        <button onClick={logout}>Выйти из консоли</button>
        <span className="sys">API: ок · БД: ок</span>
      </div>
    </aside>
    <div>
      <div className="topnav">
        <div className="tn-brand"><Mark size={26} /><b style={{ font: '600 13px var(--disp)' }}>МАРКО</b></div>
        <div className="tn-scroll">{NAV.map((x) => navBtn(x[0]))}</div>
      </div>
      <main className="content">
        {view === 'overview' && <Overview ctx={ctx} pulse={pulse} />}
        {view === 'withdraw' && <Withdraw ctx={ctx} />}
        {view === 'returns' && <Returns ctx={ctx} pulse={pulse} />}
        {view === 'catalog' && <Catalog ctx={ctx} />}
        {view === 'refs' && <Refs ctx={ctx} />}
        {view === 'journal' && <Journal key={jInit} ctx={ctx} initial={jInit} />}
        {view === 'trace' && <Trace key={tInit} ctx={ctx} initial={tInit} />}
      </main>
    </div>
    <div id="toasts" aria-live="polite">
      {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}><i />
        <div><div className="tt">{t.title}</div>
          {t.text && <div style={{ opacity: .85, fontSize: 12.5, marginTop: 2 }}>{t.text}</div>}</div></div>)}
    </div>
    {modal && <div className="veil" onClick={(e) => { if (e.target === e.currentTarget) setModal(null) }}>
      <div className="modal" role="dialog" aria-modal="true">
        <h3>{modal.title}</h3><p>{modal.text}</p>
        {modal.detail && <div className="mono-s">{modal.detail}</div>}
        <div className="row"><button className="btn" onClick={() => setModal(null)}>Отмена</button>
        <button className="btn pri" onClick={() => { const m = modal; setModal(null); m.action() }}>{modal.okLabel}</button></div>
      </div></div>}
    {wide && <div className="veil" onClick={(e) => { if (e.target === e.currentTarget) setWide(null) }}>
      <div className="modal wide" role="dialog" aria-modal="true">
        <div className="modal-h"><h3>{wide.title}</h3>
          <button className="btn sm" onClick={() => setWide(null)}>Закрыть</button></div>
        <div className="modal-b">{wide.node}</div>
      </div></div>}
    {drawer && <><div className="drawer-veil" onClick={() => setDrawer(null)} />
      <aside className="drawer">
        <div className="drawer-h"><h3>{drawer.title}</h3>
          <button className="btn sm" onClick={() => setDrawer(null)}>Закрыть</button></div>
        <div className="drawer-b">{drawer.node}</div>
      </aside></>}
  </div>
}

/* ================= вход ================= */
export default function App() {
  const [me, setMe] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [user, setUser] = useState('')
  const [pass, setPass] = useState('')
  useEffect(() => {   // живая cookie-сессия? — сразу консоль (тихая проба:
    localStorage.removeItem('tok')   // без marko:unauthorized, 401 здесь — норма)
    fetch('/v1/me')
      .then(async (r) => { if (r.ok) setMe(await r.json()) })
      .catch(() => {})
  }, [])
  useEffect(() => {   // истёкшая сессия в любом из ~40 вызовов → экран входа
    const lost = () => { setMe(null); setErr('Сессия истекла — войдите заново.') }
    window.addEventListener('marko:unauthorized', lost)
    return () => window.removeEventListener('marko:unauthorized', lost)
  }, [])
  const login = () => { if (busy) return
    if (!user.trim() || !pass) return setErr('Введите логин и пароль.')
    setBusy(true)
    fetch('/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user.trim(), password: pass }) })
      .then(async (r) => {
        if (r.status === 429) {
          const wait = parseInt(r.headers.get('Retry-After') || '60', 10)
          return setErr(`Слишком много попыток. Подождите ${wait} с и попробуйте снова.`) }
        if (r.status >= 500) return setErr('Сервер недоступен. Попробуйте ещё раз.')
        if (!r.ok) return setErr('Неверный логин или пароль. Проверьте раскладку и повторите.')
        setMe(await r.json()); setUser(''); setPass(''); setErr('') })
      .catch(() => setErr('Сервер недоступен. Попробуйте ещё раз.'))
      .finally(() => setBusy(false)) }
  const logout = () => fetch('/v1/auth/logout', { method: 'POST' })
    .then((r) => { if (!r.ok) throw new Error() })
    .then(() => setMe(null))
    .catch(() => { setErr('Не удалось выйти: сервер недоступен. Проверьте сеть — до подтверждённого выхода консоль откроется и перезагрузкой.'); setMe(null) })
  if (!me) return <div id="auth" role="dialog" aria-label="Вход в консоль">
    <div className="auth-card">
      <div className="brandline"><Mark size={40} fg="#17242B" accent="#C81E36" />
        <div><h1>МАРКО</h1><div style={{ fontSize: 11, color: 'var(--faint)' }}>МАРкировка + КОды · консоль оператора</div></div></div>
      <p className="sub">Войдите с учёткой оператора. Сессия на 7 дней живёт в защищённой cookie этого браузера.</p>
      <div className="field"><label>Логин</label>
        <input autoComplete="username" autoFocus value={user}
          onChange={(e) => setUser(e.target.value)} /></div>
      <div className="field"><label>Пароль</label>
        <input type="password" autoComplete="current-password" value={pass}
          onChange={(e) => setPass(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') login() }} /></div>
      <button className="btn pri" disabled={busy}
        style={{ width: '100%', justifyContent: 'center', padding: 9 }}
        onClick={login}>Войти</button>
      <div className="auth-err">{err}</div>
      <p className="auth-foot">Учётку выдаёт администратор платформы. Права видны в левой панели после входа.</p>
    </div></div>
  return <Console me={me} logout={logout} />
}
