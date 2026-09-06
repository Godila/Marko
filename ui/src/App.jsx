import React, { useEffect, useRef, useState } from 'react'
import './marko.css'

/* ================= http ================= */
const errOf = async (r) => {
  let m = `${r.status}`
  try { const j = await r.json(); if (j && j.detail != null)
    m += ': ' + (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) } catch {}
  return new Error(m)
}
const api = async (path, token, opts = {}) => {
  const r = await fetch(path, { ...opts, headers: { 'Authorization': `Bearer ${token.trim()}`,
    'Content-Type': 'application/json', ...(opts.headers || {}) } })
  if (!r.ok) throw await errOf(r)
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text()
}
// НКМТ: FormData без Content-Type, в ошибке виден {detail} (409/502)
const nkmt = async (path, token, opts = {}) => {
  const json = opts.body != null && !(opts.body instanceof FormData)
  const r = await fetch(path, { ...opts, headers: { 'Authorization': `Bearer ${token.trim()}`,
    ...(json ? { 'Content-Type': 'application/json' } : {}), ...(opts.headers || {}) } })
  if (!r.ok) throw await errOf(r)
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text()
}
const dl = async (path, token, name) => {   // скачивание с токеном в заголовке, не в URL
  const r = await fetch(path, { headers: { 'Authorization': `Bearer ${token.trim()}` } })
  if (!r.ok) throw await errOf(r)
  const b = await r.blob(); const u = URL.createObjectURL(b)
  const a = document.createElement('a'); a.href = u; a.download = name; a.click()
  URL.revokeObjectURL(u)
}

/* ================= словари статусов ================= */
const ITEM_STATES = {
  NEW: ['новый', 'grey'], PENDING_WITHDRAW: ['к выводу', 'amber'], WITHDRAWN: ['выведен', 'green'],
  PENDING_RETURN: ['к возврату', 'blue'], RETURNED: ['возвращён', 'green'],
  ANOMALY_NO_RECEIPT: ['аномалия: нет чека', 'red'], ANOMALY_RESALE: ['аномалия: перепродажа', 'red'],
  ANOMALY_RERETURN: ['аномалия: повторный возврат', 'red'],
  ANOMALY_UNKNOWN_TRANSITION: ['аномалия: неизвестный переход', 'red'],
  SKIPPED_FBW: ['вне контура · FBW', 'grey'],
}
const CHIP_ORDER = ['PENDING_WITHDRAW', 'PENDING_RETURN', 'WITHDRAWN', 'RETURNED',
  'ANOMALY_RESALE', 'ANOMALY_NO_RECEIPT', 'ANOMALY_RERETURN', 'ANOMALY_UNKNOWN_TRANSITION',
  'SKIPPED_FBW', 'NEW']
const DOC_STATUS = { draft: ['черновик', 'grey'], signing: ['подписывается', 'blue'],
  submitted: ['подан', 'blue'], checked_ok: ['принят ЧЗ', 'green'], error: ['ошибка', 'red'] }
const CARD_STATUS = { ok: ['новая', 'grey'], fed: ['подана', 'blue'], moderation: ['модерация', 'amber'],
  notsigned: ['ждёт подписи', 'amber'], signing: ['подписывается', 'blue'], published: ['опубликована', 'green'],
  error: ['ошибка', 'red'], errors: ['ошибки', 'red'], error_sign: ['ошибка подписи', 'red'] }
const BATCH_STATUS = { new: ['новый', 'grey'], partial: ['частично', 'amber'], feeding: ['подача', 'blue'],
  moderation: ['модерация', 'amber'], signing: ['подпись', 'blue'], published: ['опубликован', 'green'],
  error: ['ошибка', 'red'] }
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
  ['brand', 'Бренд'], ['techreg', 'Техрегламент'], ['target_gender', 'Пол'],
  ['size_system', 'Система размеров'], ['country', 'Страна'], ['producer', 'Производитель'],
  ['declaration_number', 'Номер декларации'], ['declaration_date', 'Дата декларации']]
const KIND_RU = { sale: 'продажа', return: 'возврат', withdraw: 'вывод',
  return_apply: 'возврат проведён', skip_fbw: 'вне FBS' }

/* ================= формат ================= */
const pad2 = (n) => String(n).padStart(2, '0')
const fmtD = (s) => { if (!s) return '—'; const d = new Date(s); return isNaN(d) ? s :
  `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}` }
const fmtAgo = (ts) => { if (!ts) return '—'; const m = Math.round((Date.now() / 1000 - ts) / 60)
  if (m < 1) return 'только что'; if (m < 60) return `${m} мин назад`
  const h = Math.round(m / 60); if (h < 36) return `${h} ч назад`; return `${Math.round(h / 24)} дн назад` }
const fmtLeft = (iso) => { const ms = new Date(iso) - Date.now(); if (isNaN(ms)) return ''
  if (ms <= 0) return 'просрочен'; const h = ms / 36e5
  if (h >= 48) return `${Math.round(h / 24)} дн`; if (h >= 1) return `${Math.round(h)} ч`
  return `${Math.max(1, Math.round(ms / 6e4))} мин` }
const leftCls = (iso) => { const ms = new Date(iso) - Date.now()
  return ms <= 0 ? 'over' : ms <= 864e5 ? 'danger' : ms <= 1728e5 ? 'warn' : 'ok' }
const rub = (n) => `${Number(n || 0).toLocaleString('ru-RU')} ₽`
const evLine = (it) => { const ev = it.last_event || {}
  const base = KIND_RU[ev.kind] || ev.kind || '—'
  return base + (ev.fiscal_dt ? ` · ${fmtD(ev.fiscal_dt)}` : '')
    + (ev.fiscal_doc_number ? ` · чек ${ev.fiscal_doc_number}` : '')
    + (ev.price ? ` · ${rub(ev.price)}` : '') }

/* ================= иконки ================= */
const I = {
  seal: (s = 34) => <svg className="seal" width={s} height={s} viewBox="0 0 40 40" aria-hidden="true">
    <circle cx="20" cy="20" r="18.5" fill="none" stroke="#C81E36" strokeWidth="2" />
    <circle cx="20" cy="20" r="13" fill="none" stroke="#C81E36" strokeWidth="1" />
    <path d="M20 8v4M20 28v4M8 20h4M28 20h4" stroke="#C81E36" strokeWidth="1.4" />
    <path d="M14.5 20.5l3.6 3.6 7.4-8" fill="none" stroke="#C81E36" strokeWidth="2.2"
      strokeLinecap="round" strokeLinejoin="round" /></svg>,
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

const Head = ({ title, sub, tools }) => (
  <div className="view-head">
    <div><h1>{title}</h1>{sub && <div className="sub">{sub}</div>}</div>
    {tools && <div className="head-tools">{tools}</div>}
  </div>)

const Sync = ({ tick }) => <span className="sync">обновлено {Math.max(0, Math.round((Date.now() - tick) / 1000))} с назад</span>

/* ================= документы ЧЗ (общая таблица) ================= */
function DocTable({ docs, ctx, empty }) {
  const { token, notify, confirm, openDrawer, bump } = ctx
  const submitDoc = (d) => confirm(`Подать документ №${d.id} в «Честный знак»?`,
    'Документ уйдёт в ЧЗ и будет подписан УКЭП автоматически (signer). Отменить подачу нельзя — только создать корректировку.',
    `${d.type}`, 'Подать в ЧЗ', async () => {
      try { const r = await api(`/v1/docs/${d.id}/submit`, token, { method: 'POST' })
        notify(`Документ №${d.id} подан`, `uuid ${r.external_id}`); bump()
      } catch (e) { notify('Подача не прошла', e.message, 'bad') }
    })
  const checkDoc = async (d) => { try {
      const r = await api(`/v1/docs/${d.id}/check`, token, { method: 'POST' })
      notify(`Документ №${d.id}: ${DOC_STATUS[r.status]?.[0] || r.status}`, r.mt_status || ''); bump()
    } catch (e) { notify('Проверка не удалась', e.message, 'bad') } }
  const showPayload = async (d) => { try {
      const full = await api(`/v1/docs/${d.id}`, token)
      openDrawer(`Документ №${d.id} · ${d.type}`,
        <div><p>Так документ уходит в ЧЗ: base64(JSON) в product_document. Правка состава — только пересборкой черновика.</p>
          <pre>{JSON.stringify(full.payload, null, 2)}</pre></div>)
    } catch (e) { notify('Не удалось открыть состав', e.message, 'bad') } }
  if (!docs.length) return <div className="empty"><b>{empty || 'Документов пока нет'}</b>Они появятся после сбора из позиций журнала.</div>
  return <div className="twrap"><table className="t">
    <thead><tr><th>№</th><th>Тип</th><th>Статус</th><th>uuid в ЧЗ</th><th>Создан</th><th></th></tr></thead>
    <tbody>{docs.map((d) => <tr key={d.id}>
      <td className="num">{d.id}</td>
      <td className="mono">{d.type}</td>
      <td><Badge dict={DOC_STATUS} v={d.status} /></td>
      <td className="mono">{d.external_id || '—'}</td>
      <td className="mono">{fmtD(d.created_at)}</td>
      <td className="actions">
        {d.status === 'draft' && <button className="btn sm pri" onClick={() => submitDoc(d)}>Подать</button>}
        {(d.status === 'submitted' || d.status === 'error')
          && <button className="btn sm" onClick={() => checkDoc(d)}>Проверить</button>}
        <button className="btn sm" onClick={() => showPayload(d)}>Состав</button>
      </td></tr>)}</tbody>
  </table></div>
}

/* ================= обзор ================= */
function Overview({ ctx, pulse }) {
  const { token, notify, confirm, bump, inn, go } = ctx
  const [docs, setDocs] = useState(null)
  useEffect(() => { api('/v1/docs?limit=6', token).then(setDocs).catch(() => setDocs([])) }, [ctx.tick])
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
  const dlHot = dl && new Date(dl) - Date.now() <= 48 * 36e5
  const doWithdraw = () => { if (!pendW) return notify('Нет позиций к выводу', 'Журнал не содержит КМ в статусе «к выводу».', 'warn')
    confirm('Собрать вывод из оборота?',
      `Из ${pendW} КМ будет создан черновик LK_RECEIPT (без фискального чека — отдельным документом). КМ сразу перейдут в «Выведен», подача в ЧЗ — отдельным шагом.`,
      `ИНН ${inn}`, 'Собрать документ', async () => {
        try { const r = await api('/v1/batches/withdraw', token, { method: 'POST', body: JSON.stringify({ inn }) })
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
            <span className="hint">{openPts} пунктов</span></div>
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
  const { token, notify, confirm, bump, inn } = ctx
  const [pend, setPend] = useState(null)
  const [docs, setDocs] = useState(null)
  useEffect(() => { api('/v1/journal?state=PENDING_WITHDRAW&limit=1000', token).then(setPend).catch(() => setPend([]))
    api('/v1/docs?limit=200', token).then(setDocs).catch(() => setDocs([])) }, [ctx.tick])
  const doWithdraw = () => { const n = pend ? pend.length : 0
    if (!n) return notify('Нет позиций к выводу', 'Журнал не содержит КМ в статусе «к выводу».', 'warn')
    confirm('Собрать вывод из оборота?',
      `Из ${n} КМ будет создан черновик LK_RECEIPT (позиции без фискального чека уйдут отдельным документом «Иное»). КМ сразу перейдут в «Выведен»; подача в ЧЗ — отдельным шагом.`,
      `ИНН ${inn}`, 'Собрать документ', async () => {
        try { const r = await api('/v1/batches/withdraw', token, { method: 'POST', body: JSON.stringify({ inn }) })
          if (r.doc_id === 0) notify('Нет позиций к выводу', '', 'warn')
          else notify(`Создан черновик LK_RECEIPT №${r.doc_id}`, 'Подайте его в ЧЗ — кнопкой «Подать» ниже.')
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
      <div className="twrap"><table className="t">
        <thead><tr><th>Код маркировки</th><th>Последний сигнал</th></tr></thead>
        <tbody>{(pend || []).map((it) => <tr key={it.km}>
          <td><KmCell km={it.km} /></td><td style={{ fontSize: 12.5 }}>{evLine(it)}</td></tr>)}
          {pend && !pend.length && <tr><td colSpan={2}><div className="empty"><b>Всё выведено</b>Новые продажи появятся после поллинга WB — 06:30 и 18:30 МСК.</div></td></tr>}
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
  const { token, notify, confirm, bump, inn } = ctx
  const [rows, setRows] = useState(null)
  const [docs, setDocs] = useState(null)
  const [stats, setStats] = useState(null)
  useEffect(() => { api('/v1/wb/returns', token).then(setRows).catch(() => setRows([]))
    api('/v1/docs?limit=200', token).then(setDocs).catch(() => setDocs([]))
    api('/v1/journal/stats', token).then(setStats).catch(() => {}) }, [ctx.tick])
  const sorted = (rows || []).slice().sort((a, b) => {
    if (!!a.completed_dt !== !!b.completed_dt) return a.completed_dt ? 1 : -1
    return (a.expired_dt || '').localeCompare(b.expired_dt || '') })
  const nearest = sorted.find((r) => !r.completed_dt && r.expired_dt)
  const hot = nearest && new Date(nearest.expired_dt) - Date.now() <= 48 * 36e5
  const poll = () => confirm('Опросить WB goods-return вручную?',
    'Ручной опрос расходует ту же квоту, что и часовой автоматический.',
    'GET goods-return · окно 7 дней', 'Опросить', async () => {
      try { const r = await api('/v1/wb/returns/poll', token, { method: 'POST' })
        notify('Опрос выполнен', `новых ${r.new}, обновлено ${r.updated}` + (r.alerts ? `, алертов ${r.alerts}` : ''))
        bump()
      } catch (e) { notify('Опрос не удался', e.message, 'bad') } })
  const doReturn = () => { const n = (stats || {}).PENDING_RETURN || 0
    if (!n) return notify('Нет позиций к возврату', 'КМ в статусе «к возврату» появятся, когда WB примет возврат (excise op=2).', 'warn')
    confirm('Собрать возврат продавца?',
      `Из ${n} КМ будет создан черновик LP_RETURN. Первичка — чеки из последних выводов; КМ без вывода будут пропущены.`,
      'LP_RETURN · REMOTE_SALE_RETURN · оплачено', 'Собрать документ', async () => {
        try { const r = await api('/v1/batches/return', token, { method: 'POST', body: JSON.stringify({ inn }) })
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
      <div className="twrap"><table className="t">
        <thead><tr><th>Заказ</th><th>Предмет</th><th>Причина</th><th>Статус</th><th>Забрать до</th><th>Выдан</th><th>ПВЗ</th></tr></thead>
        <tbody>{sorted.map((r) => {
          const c = !r.completed_dt && r.expired_dt ? leftCls(r.expired_dt) : ''
          return <tr key={r.srid} className={c === 'danger' || c === 'over' ? 'rowhot' : ''}>
            <td className="num">{r.order_id}</td><td>{r.subject || r.srid}</td>
            <td>{r.reason || '—'}</td><td>{r.status}</td>
            <td>{r.expired_dt ? <><span className={`cd ${c}`}>{fmtLeft(r.expired_dt)}</span>
              <div className="faint mono" style={{ fontSize: 11, marginTop: 2 }}>{fmtD(r.expired_dt)}</div></> : '—'}</td>
            <td>{r.completed_dt ? fmtD(r.completed_dt) : '—'}</td><td>{r.office || '—'}</td></tr> })}
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
function Catalog({ ctx }) {
  const { token, notify, confirm, bump } = ctx
  const [batches, setBatches] = useState(null)
  const [stage, setStage] = useState('')
  const [openId, setOpenId] = useState(null)
  const [cards, setCards] = useState(null)
  const [cardFilter, setCardFilter] = useState('')
  const [file, setFile] = useState(null)
  const fileRef = useRef(null)
  useEffect(() => { nkmt('/v1/nkmt/batches', token).then(setBatches)
    .catch((e) => { setBatches([]); notify('Не удалось загрузить батчи', e.message, 'bad') }) }, [ctx.tick])
  const stageMatch = STAGES.find((x) => x.key === stage)
  useEffect(() => { if (openId != null)
    nkmt(`/v1/nkmt/batches/${openId}?card_status=${encodeURIComponent(cardFilter)}`, token)
      .then((r) => setCards(r.cards || []))
      .catch((e) => notify('Не удалось загрузить карточки', e.message, 'bad')) }, [openId, cardFilter])
  const toggle = (id) => { if (openId === id) { setOpenId(null); setCards(null); return }
    setOpenId(id); setCards(null) }
  const act = (id, kind, okMsg) => confirm(
    kind === 'feed' ? 'Подать фид в Национальный каталог?' : `Выполнить «${kind}» для батча №${id}?`,
    kind === 'feed' ? 'Карточки уйдут в НК; дальше модерация и подпись идут автоматически (воркер).' : 'Ручной прогон того же, что делает автоматика.',
    `/v1/nkmt/batches/${id}/${kind}`, kind === 'feed' ? 'Подать фид' : 'Выполнить',
    async () => { try { const r = await nkmt(`/v1/nkmt/batches/${id}/${kind}`, token, { method: 'POST' })
        notify(okMsg(r), ''); bump()
      } catch (e) { notify('Не удалось', e.message, 'bad') } })
  const doImport = () => { if (!file) return
    const fd = new FormData(); fd.append('file', file)
    nkmt('/v1/nkmt/import', token, { method: 'POST', body: fd })
      .then((r) => { notify(`Импорт завершён: батч №${r.batch_id}`, `ok ${r.stats.ok}, ошибок ${r.stats.error}`)
        setFile(null); if (fileRef.current) fileRef.current.value = ''; bump() })
      .catch((e) => notify('Импорт не удался', e.message, 'bad')) }
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
      <div className="card-h"><h2>Импорт выгрузки</h2><span className="hint">.xlsx из 1С → карточки с дефолтами из «Справочников»</span></div>
      <div className="card-b">
        <label className="drop" style={{ display: 'block' }}>
          <input ref={fileRef} type="file" accept=".xlsx" style={{ display: 'none' }}
            onChange={(e) => setFile(e.target.files[0] || null)} />
          <b>Выберите выгрузку .xlsx</b>
          <div style={{ fontSize: 12.5, marginTop: 2 }}>атрибуты подтянутся по ТН ВЭД, декларация — из справочника</div>
          {file && <div className="file">{file.name}</div>}
        </label>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
          <button className="btn pri" disabled={!file} onClick={doImport}>Импортировать</button></div>
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
            <button className="btn sm" onClick={() => dl(`/v1/nkmt/batches/${b.id}/report?format=xlsx`, token, `marko-batch-${b.id}.xlsx`).catch((e) => notify('Отчёт не сформирован', e.message, 'bad'))}>Отчёт 1С</button>
            <button className="btn sm" onClick={() => dl(`/v1/nkmt/batches/${b.id}/report?format=csv`, token, `marko-batch-${b.id}.csv`).catch((e) => notify('Отчёт не сформирован', e.message, 'bad'))}>csv</button>
          </span>
        </div>
        {openId === b.id && <div className="cards-wrap">
          <div className="tools">
            <select value={cardFilter} onChange={(e) => setCardFilter(e.target.value)}>
              <option value="">все статусы карточек</option>
              {Object.keys(CARD_STATUS).map((s2) => <option key={s2} value={s2}>{CARD_STATUS[s2][0]}</option>)}
            </select>
            <span className="hint">{cards ? `карточек: ${cards.length}` : 'загрузка…'}</span></div>
          <div className="twrap"><table className="t small">
            <thead><tr><th>Артикул</th><th>GTIN</th><th>Наименование</th><th>Статус</th><th>Ошибка</th></tr></thead>
            <tbody>{(cards || []).map((c) => <tr key={c.id}>
              <td className="mono">{c.article}</td><td className="mono">{c.gtin || '—'}</td>
              <td>{c.name}</td><td><Badge dict={CARD_STATUS} v={c.status} /></td>
              <td className="err-tx">{c.error_text || ''}</td></tr>)}</tbody>
          </table></div>
        </div>}
      </div>)}
      {batches && !shown.length && <div className="empty"><b>Батчей в этом статусе нет</b>Импортируйте выгрузку, чтобы начать новый.</div>}
      </div>
    </div>
  </>
}

/* ================= справочники ================= */
function Refs({ ctx }) {
  const { token, notify, confirm, inn, setInn } = ctx
  const [decls, setDecls] = useState(null)
  const [defs, setDefs] = useState(null)
  const [em, setEm] = useState(null)
  const [dnum, setDnum] = useState(''); const [ddate, setDdate] = useState(''); const [dtype, setDtype] = useState('declaration')
  useEffect(() => { nkmt('/v1/nkmt/declarations', token).then(setDecls).catch(() => setDecls([]))
    nkmt('/v1/nkmt/defaults', token).then(setDefs).catch(() => setDefs({}))
    api('/v1/emitter/defaults', token).then(setEm).catch(() => setEm({ fias_id: '', primary_custom_name: '' })) }, [ctx.tick])
  const addDecl = () => { if (!dnum || !ddate) return notify('Заполните номер и дату', '', 'warn')
    nkmt('/v1/nkmt/declarations', token, { method: 'POST', body: JSON.stringify({ doc_number: dnum, doc_date: ddate, doc_type: dtype }) })
      .then(() => { setDnum(''); setDdate(''); notify('Декларация добавлена', ''); ctx.bump() })
      .catch((e) => notify('Не добавлено', e.message, 'bad')) }
  const delDecl = (d) => confirm('Удалить декларацию?', d.doc_number, 'Карточки, где она уже подставлена, не изменятся.', 'Удалить',
    () => nkmt(`/v1/nkmt/declarations/${d.id}`, token, { method: 'DELETE' })
      .then(() => { notify('Декларация удалена', ''); ctx.bump() })
      .catch((e) => notify('Не удалось удалить', e.message, 'bad')))
  const saveDefs = () => nkmt('/v1/nkmt/defaults', token, { method: 'PUT', body: JSON.stringify(defs || {}) })
    .then(() => notify('Дефолты сохранены', 'Подставятся при следующем импорте.'))
    .catch((e) => notify('Не сохранено', e.message, 'bad'))
  const saveEm = () => api('/v1/emitter/defaults', token, { method: 'PUT', body: JSON.stringify(em || {}) })
    .then(() => notify('Реквизиты эмиттера сохранены', 'Применятся к следующим черновикам LK_RECEIPT.'))
    .catch((e) => notify('Не сохранено', e.message, 'bad'))
  return <>
    <Head title="Справочники" sub="Общие значения для карточек НК и документов: заполняются один раз, подставляются автоматически." />
    <div className="grid2">
      <div>
        <div className="card">
          <div className="card-h"><h2>Дефолты карточек НК</h2><span className="hint">подставляются при импорте</span></div>
          <div className="card-b">
            <div className="formgrid">
              {DEF_FIELDS.map(([k, label]) => <div className="field" key={k}>
                <label>{label}</label>
                <input value={(defs || {})[k] || ''} type={k === 'declaration_date' ? 'date' : 'text'}
                  onChange={(e) => setDefs({ ...(defs || {}), [k]: e.target.value })} /></div>)}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn pri" onClick={saveDefs}>Сохранить дефолты</button></div>
          </div>
        </div>
        <div className="card">
          <div className="card-h"><h2>Эмиттер документов</h2><span className="hint">реквизиты для LK_RECEIPT</span></div>
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
        </div>
      </div>
      <div>
        <div className="card">
          <div className="card-h"><h2>Декларации соответствия</h2><span className="hint">подставляются в карточки по номеру</span></div>
          <div className="card-b" style={{ borderBottom: '1px solid var(--line)' }}>
            <div className="frow">
              <div className="field" style={{ flex: 1, minWidth: 170 }}><label>Номер</label>
                <input value={dnum} placeholder="ЕАЭС N RU Д-…" onChange={(e) => setDnum(e.target.value)} /></div>
              <div className="field"><label>Дата</label><input type="date" value={ddate} onChange={(e) => setDdate(e.target.value)} /></div>
              <div className="field"><label>Тип</label><select value={dtype} onChange={(e) => setDtype(e.target.value)}>
                <option value="declaration">декларация</option><option value="certificate">сертификат</option></select></div>
              <button className="btn pri" onClick={addDecl}>Добавить</button></div>
          </div>
          <div className="twrap"><table className="t small">
            <thead><tr><th>Номер</th><th>Дата</th><th>Тип</th><th></th></tr></thead>
            <tbody>{(decls || []).map((d) => <tr key={d.id}>
              <td style={{ fontSize: 12.5 }}>{d.doc_number}</td><td className="mono">{d.doc_date}</td>
              <td>{d.doc_type === 'certificate' ? 'сертификат' : 'декларация'}</td>
              <td className="actions"><button className="btn sm" onClick={() => delDecl(d)}>Удалить</button></td></tr>)}
              {decls && !decls.length && <tr><td colSpan={4}><div className="empty"><b>Список пуст</b>Добавьте действующую декларацию — она подставится в карточки.</div></td></tr>}
            </tbody></table></div>
        </div>
      </div>
    </div>
  </>
}

/* ================= журнал КМ ================= */
function Journal({ ctx, initial }) {
  const { token, openDrawer } = ctx
  const [rows, setRows] = useState(null)
  const [stats, setStats] = useState({})
  const [state, setState] = useState(initial || '')
  const [q, setQ] = useState('')
  useEffect(() => { api(`/v1/journal?limit=1000${state && state !== 'ANOMALY' ? `&state=${encodeURIComponent(state)}` : ''}`, token)
      .then(setRows).catch(() => setRows([]))
    api('/v1/journal/stats', token).then(setStats).catch(() => {}) }, [ctx.tick, state])
  const shown = (rows || []).filter((it) => state === 'ANOMALY' ? it.state.startsWith('ANOMALY')
    : q ? it.km.toLowerCase().includes(q.toLowerCase()) || evLine(it).toLowerCase().includes(q.toLowerCase()) : true)
  const anomalies = Object.entries(stats).filter(([k]) => k.startsWith('ANOMALY')).reduce((a, [, v]) => a + v, 0)
  const total = Object.values(stats).reduce((a, v) => a + v, 0)
  return <>
    <Head title="Журнал кодов маркировки" sub="Жизненный цикл каждого КМ: продажа → вывод из оборота → возврат. Аномалии требуют ручного разбора — автоматика их не трогает."
      tools={<Sync tick={ctx.tick} />} />
    <div className="chiprow" style={{ marginBottom: 14 }}>
      <button className="chip" aria-pressed={state === ''} onClick={() => setState('')}>все состояния <span className="n">{total}</span></button>
      {anomalies > 0 && <button className="chip alert" aria-pressed={state === 'ANOMALY'} onClick={() => setState('ANOMALY')}>аномалии <span className="n">{anomalies}</span></button>}
      {CHIP_ORDER.filter((s) => stats[s] || s === state).map((s) => {
        const [lbl, cls] = ITEM_STATES[s]
        return <button key={s} className={`chip${cls === 'red' ? ' alert' : ''}`} aria-pressed={state === s}
          onClick={() => setState(s)}>{lbl} <span className="n">{stats[s] || 0}</span></button> })}
    </div>
    <div className="frow" style={{ marginBottom: 14 }}>
      <div className="search">{I.search}
        <input value={q} placeholder="Поиск по коду КМ или событию…" onChange={(e) => setQ(e.target.value)} /></div>
      <span className="faint" style={{ fontSize: 12 }}>показано <span className="mono">{shown.length}</span></span>
    </div>
    <div className="card">
      <div className="twrap"><table className="t">
        <thead><tr><th>Код маркировки</th><th>Состояние</th><th>Последний сигнал</th><th>Обновлён</th></tr></thead>
        <tbody>{shown.map((it) => { const [lbl] = ITEM_STATES[it.state] || [it.state]
          const anom = it.state.startsWith('ANOMALY')
          return <tr key={it.km} className={anom ? 'rowhot' : ''} style={{ cursor: 'pointer' }}
            onClick={() => openDrawer(`КМ · ${lbl} · ${it.state}`,
              <div><p>Последнее событие по коду (поле last_event в журнале).</p>
                <pre>{JSON.stringify(it.last_event, null, 2)}</pre></div>)}>
            <td><KmCell km={it.km} /></td>
            <td><Badge dict={ITEM_STATES} v={it.state} /></td>
            <td style={{ fontSize: 12.5 }}>{evLine(it)}</td>
            <td className="mono">{fmtD(it.updated_at)}</td></tr> })}
          {rows && !shown.length && <tr><td colSpan={4}><div className="empty"><b>Ничего не найдено</b>Ослабьте фильтр или очистите поиск.</div></td></tr>}
        </tbody></table></div>
    </div>
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
]

function Console({ token, me, logout }) {
  const [view, setView] = useState('overview')
  const [jInit, setJInit] = useState('')
  const [pulse, setPulse] = useState(null)
  const [tick, setTick] = useState(Date.now())
  const [inn, setInn] = useState(localStorage.getItem('inn') || '090201471350')
  const [toasts, setToasts] = useState([])
  const [modal, setModal] = useState(null)
  const [drawer, setDrawer] = useState(null)
  const bump = () => setTick(Date.now())
  const notify = (title, text = '', kind = '') => { const id = Math.random()
    setToasts((t) => [...t, { id, title, text, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500) }
  const confirm = (title, text, detail, okLabel, action) => setModal({ title, text, detail, okLabel, action })
  const openDrawer = (title, node) => setDrawer({ title, node })
  const go = (v, jf) => { if (jf != null) setJInit(jf); setView(v); window.scrollTo(0, 0) }
  useEffect(() => { const i = setInterval(bump, 60000); return () => clearInterval(i) }, [])
  useEffect(() => { api('/v1/pulse', token).then(setPulse).catch(() => {}) }, [tick])
  const ctx = { token, notify, confirm, openDrawer, bump, tick, inn, setInn, go, pulse }
  const s = pulse?.stats || {}
  const navCnt = { overview: null, withdraw: s.PENDING_WITHDRAW || 0,
    returns: pulse?.returns?.pending_return || 0,
    catalog: pulse ? Object.entries(pulse.batches || {})
      .filter(([k]) => !['published', 'error'].includes(k)).reduce((a, [, v]) => a + v, 0) : 0,
    refs: null,
    journal: Object.entries(s).filter(([k]) => k.startsWith('ANOMALY')).reduce((a, [, v]) => a + v, 0) }
  const navBtn = (v) => { const [key, lbl, icon] = NAV.find((x) => x[0] === v)
    return <button key={key} className="nav-item" aria-current={view === key}
      onClick={() => go(key)}>{icon}<span className="lbl">{lbl}</span>
      {navCnt[key] ? <span className="cnt">{navCnt[key]}</span> : null}</button> }
  return <div id="app">
    <aside className="rail">
      <div className="brand">{I.seal(34)}<div><b>МАРКО</b><span>Честный знак · нацкат · WB</span></div></div>
      <nav className="nav" aria-label="Разделы">{NAV.map((x) => navBtn(x[0]))}</nav>
      <div className="rail-foot">
        <span className="who">оператор · {(me.scopes || []).join(', ')}</span>
        <button onClick={logout}>Выйти из консоли</button>
        <span className="sys">API: ок · БД: ок</span>
      </div>
    </aside>
    <div>
      <div className="topnav">
        <div className="tn-brand">{I.seal(26)}<b style={{ font: '600 13px var(--disp)' }}>МАРКО</b></div>
        <div className="tn-scroll">{NAV.map((x) => navBtn(x[0]))}</div>
      </div>
      <main className="content">
        {view === 'overview' && <Overview ctx={ctx} pulse={pulse} />}
        {view === 'withdraw' && <Withdraw ctx={ctx} />}
        {view === 'returns' && <Returns ctx={ctx} pulse={pulse} />}
        {view === 'catalog' && <Catalog ctx={ctx} />}
        {view === 'refs' && <Refs ctx={ctx} />}
        {view === 'journal' && <Journal key={jInit} ctx={ctx} initial={jInit} />}
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
  const [token, setToken] = useState(localStorage.getItem('tok') || '')
  const [me, setMe] = useState(null)
  const [err, setErr] = useState('')
  const tryToken = (t, quiet) => api('/v1/me', t)
    .then((m) => { localStorage.setItem('tok', t); setMe(m); setErr('') })
    .catch((e) => { if (!quiet) setErr(`Токен не принят: ${e.message}. Проверьте значение или выдайте новый.`) })
  useEffect(() => { if (token) tryToken(token, true) }, [])
  const login = () => { if (!token.trim()) return setErr('Введите токен доступа платформы.'); tryToken(token, false) }
  const logout = () => { localStorage.removeItem('tok'); setToken(''); setMe(null) }
  if (!me) return <div id="auth" role="dialog" aria-label="Вход в консоль">
    <div className="auth-card">
      <div className="brandline">{I.seal(40)}
        <div><h1>МАРКО</h1><div style={{ fontSize: 11, color: 'var(--faint)' }}>МАРкировка + КОды · консоль оператора</div></div></div>
      <p className="sub">Введите токен доступа платформы. Он выдаётся администратором и хранится только в этом браузере.</p>
      <div className="field"><label>Токен доступа</label>
        <input type="password" autoComplete="off" className="mono" value={token}
          onChange={(e) => setToken(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') login() }} /></div>
      <button className="btn pri" style={{ width: '100%', justifyContent: 'center', padding: 9 }} onClick={login}>Войти</button>
      <div className="auth-err">{err}</div>
      <p className="auth-foot">Права токена видны в левой панели после входа; проверка — GET /v1/me (имя и scopes).</p>
    </div></div>
  return <Console token={token} me={me} logout={logout} />
}
