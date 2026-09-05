import React, { useEffect, useRef, useState } from 'react'

const api = async (path, token, opts = {}) => {
  const r = await fetch(path, { ...opts, headers: { 'Authorization': `Bearer ${token.trim()}`,
    'Content-Type': 'application/json', ...(opts.headers || {}) } })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text()
}

// НКМТ: как api(), но FormData идёт без Content-Type, а в ошибке виден {detail} (409/502)
const nkmtErr = async (r) => {
  let m = `${r.status}`
  try { const j = await r.json(); if (j && j.detail != null) m += ': ' + (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) } catch {}
  return m
}
const nkmt = async (path, token, opts = {}) => {
  const json = opts.body != null && !(opts.body instanceof FormData)
  const r = await fetch(path, { ...opts, headers: { 'Authorization': `Bearer ${token.trim()}`,
    ...(json ? { 'Content-Type': 'application/json' } : {}), ...(opts.headers || {}) } })
  if (!r.ok) throw new Error(await nkmtErr(r))
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text()
}
const nkmtBlob = async (path, token) => {  // отчёты: токен в заголовке, не в URL
  const r = await fetch(path, { headers: { 'Authorization': `Bearer ${token.trim()}` } })
  if (!r.ok) throw new Error(await nkmtErr(r))
  return r.blob()
}

const CARD_STATUSES = ['ok', 'error', 'fed', 'moderation', 'notsigned', 'signing', 'published', 'errors', 'error_sign']
const DEF_FIELDS = [
  ['brand', 'Бренд'], ['techreg', 'Техрегламент'], ['target_gender', 'Пол'],
  ['size_system', 'Система размеров'], ['country', 'Страна'], ['producer', 'Производитель'],
  ['declaration_number', 'Номер декларации'], ['declaration_date', 'Дата декларации']]

export default function App() {
  const [token, setToken] = useState(localStorage.getItem('tok') || '')
  const [tab, setTab] = useState('journal')
  const [items, setItems] = useState([]); const [docs, setDocs] = useState([])
  const [stats, setStats] = useState(null)
  const [inn, setInn] = useState('090201471350'); const [msg, setMsg] = useState('')
  const [pre, setPre] = useState('')
  const [stateFilter, setStateFilter] = useState('')
  // Каталог (НКМТ)
  const [nb, setNb] = useState([]); const [nbOpen, setNbOpen] = useState(null); const [nbCards, setNbCards] = useState([])
  const [cardFilter, setCardFilter] = useState(''); const [impFile, setImpFile] = useState(null)
  const fileRef = useRef(null)
  const [decls, setDecls] = useState([]); const [dnum, setDnum] = useState(''); const [ddate, setDdate] = useState('')
  const [defs, setDefs] = useState(null)
  // Возвраты (WB goods-return + LP_RETURN)
  const [wbRet, setWbRet] = useState([])

  const load = async () => {
    if (!token) return
    try {
      const q = stateFilter ? `&state=${encodeURIComponent(stateFilter)}` : ''
      setItems(await api(`/v1/journal?limit=200${q}`, token))
      setDocs(await api('/v1/docs', token))
      setStats(await api('/v1/journal/stats', token)); setMsg('')
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  useEffect(() => { localStorage.setItem('tok', token); load() }, [token, stateFilter])

  const loadNb = async () => {
    try { setNb(await nkmt('/v1/nkmt/batches', token)) } catch (e) { setMsg('ошибка: ' + e.message) } }
  const loadDecls = async () => {
    try { setDecls(await nkmt('/v1/nkmt/declarations', token)) } catch (e) { setMsg('ошибка: ' + e.message) } }
  useEffect(() => {
    if (!token || tab !== 'catalog') return
    loadNb(); loadDecls()
    nkmt('/v1/nkmt/defaults', token).then(setDefs).catch(e => setMsg('ошибка: ' + e.message))
  }, [token, tab])

  const loadRet = async () => {
    try { setWbRet(await api('/v1/wb/returns', token)); setMsg('') } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  useEffect(() => { if (token && tab === 'returns') { loadRet(); load() } }, [token, tab])

  const pollRet = async () => {
    try {
      const r = await api('/v1/wb/returns/poll', token, { method: 'POST' })
      setMsg(''); setPre(`goods-return poll:\n${JSON.stringify(r, null, 2)}`)
      loadRet(); load()
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }

  // дедлайн забора ≤48 ч и ещё не выдан → красный (WB хранит возврат 7 дней)
  const retRowColor = (r) => {
    if (r.completed_dt) return ''
    const dl = r.expired_dt ? new Date(r.expired_dt).getTime() : 0
    return dl && dl - Date.now() < 48 * 3600 * 1000 ? '#ffcccc' : ''
  }

  const loadCards = async (id) => {
    try {
      const q = cardFilter ? `?card_status=${encodeURIComponent(cardFilter)}` : ''
      setNbCards((await nkmt(`/v1/nkmt/batches/${id}${q}`, token)).cards || [])
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  const openNb = (id) => {
    if (nbOpen === id) { setNbOpen(null); return }
    setNbOpen(id); loadCards(id)
  }
  useEffect(() => { if (token && nbOpen != null) loadCards(nbOpen) }, [cardFilter])

  const doImport = async () => {
    if (!impFile) { setMsg('выберите файл'); return }
    try {
      const fd = new FormData(); fd.append('file', impFile)
      const r = await nkmt('/v1/nkmt/import', token, { method: 'POST', body: fd })
      setMsg(''); setPre(`импорт: batch ${r.batch_id}\n${JSON.stringify(r.stats, null, 2)}`)
      setImpFile(null); if (fileRef.current) fileRef.current.value = ''
      loadNb()
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  const nbAction = async (id, action) => {
    try {
      const r = await nkmt(`/v1/nkmt/batches/${id}/${action}`, token, { method: 'POST' })
      setMsg(''); setPre(`${action} batch ${id}:\n${JSON.stringify(r, null, 2)}`)
      loadNb(); if (nbOpen === id) loadCards(id)
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  const nbReport = async (id, fmt) => {
    try {
      const blob = await nkmtBlob(`/v1/nkmt/batches/${id}/report?format=${fmt}`, token)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = `nkmt-batch-${id}.${fmt}`; a.click()
      URL.revokeObjectURL(url); setMsg('')
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  const addDecl = async () => {
    if (!dnum || !ddate) { setMsg('введите номер и дату'); return }
    try {
      await nkmt('/v1/nkmt/declarations', token, { method: 'POST', body: JSON.stringify({ doc_number: dnum, doc_date: ddate }) })
      setDnum(''); setDdate(''); setMsg(''); loadDecls()
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  const delDecl = async (id) => {
    try { await nkmt(`/v1/nkmt/declarations/${id}`, token, { method: 'DELETE' }); loadDecls() } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  const saveDefs = async () => {
    if (defs == null) return
    try { await nkmt('/v1/nkmt/defaults', token, { method: 'PUT', body: JSON.stringify(defs) }); setMsg('дефолты сохранены') } catch (e) { setMsg('ошибка: ' + e.message) }
  }

  const mkBatch = async (kind) => {
    try {
      const r = await api(`/v1/batches/${kind}`, token, { method: 'POST', body: JSON.stringify({ inn }) })
      if (kind === 'withdraw' && r && r.doc_id === 0) { setPre('нет позиций к выводу'); setMsg('') }
      else { setMsg(''); setPre(JSON.stringify(r, null, 2)) }
      load()
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }

  const showDoc = (id, fmt) =>
    api(`/v1/docs/${id}${fmt === 'csv' ? '/csv' : ''}`, token)
      .then(d => setPre(fmt === 'csv' ? d : JSON.stringify(d, null, 2)))
      .catch(e => setMsg('ошибка: ' + e.message))

  const mtAction = (id, action) =>
    api(`/v1/docs/${id}/${action}`, token, { method: 'POST' })
      .then(r => { setPre(JSON.stringify(r, null, 2)); load() })
      .catch(e => setMsg('ошибка: ' + e.message))

  return (
    <div style={{ fontFamily: 'sans-serif', margin: '0 auto', maxWidth: 1100 }}>
      <h2>МАРКО</h2>
      <input value={token} onChange={e => setToken(e.target.value)} placeholder="API token" size={40} />
      {['journal', 'batches', 'catalog', 'returns'].map(t => (
        <button key={t} onClick={() => setTab(t)} style={{ marginLeft: 8, fontWeight: tab === t ? 'bold' : 'normal' }}>
          {t === 'journal' ? 'Журнал' : t === 'batches' ? 'Батчи' : t === 'catalog' ? 'Каталог' : 'Возвраты'}</button>))}
      <select value={stateFilter} onChange={e => setStateFilter(e.target.value)} style={{ marginLeft: 12 }}>
        <option value="">все состояния</option>
        {Object.keys(stats || {}).map(s => <option key={s} value={s}>{s} ({stats[s]})</option>)}
      </select>
      <span style={{ color: 'red', marginLeft: 12 }}>{msg}</span>
      {tab === 'journal' && (
        <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', marginTop: 12, width: '100%' }}>
          <thead><tr><th>КМ</th><th>Состояние</th><th>Обновлён</th></tr></thead>
          <tbody>{items.map(i => (
            <tr key={i.km}><td style={{ fontFamily: 'monospace' }}>{i.km}</td><td>{i.state}</td><td>{i.updated_at}</td></tr>))}</tbody>
        </table>)}
      {tab === 'batches' && (
        <div style={{ marginTop: 12 }}>
          {stats !== null && (
            <div style={{ fontFamily: 'monospace', marginBottom: 8 }}>
              {Object.entries(stats).map(([s, n]) => `${s}: ${n}`).join(', ') || 'журнал пуст'}
            </div>)}
          <input value={inn} onChange={e => setInn(e.target.value)} size={14} />
          <button onClick={() => mkBatch('withdraw')} style={{ marginLeft: 8 }}>Собрать вывод</button>
          <button onClick={() => mkBatch('return')} style={{ marginLeft: 8 }}>Собрать возврат</button>
          <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', marginTop: 12, width: '100%' }}>
            <thead><tr><th>id</th><th>тип</th><th>статус</th><th>ЧЗ uuid</th><th>создан</th><th>Док</th><th>ЧЗ</th></tr></thead>
            <tbody>{docs.map(d => (
              <tr key={d.id}><td>{d.id}</td><td>{d.type}</td><td>{d.status}</td>
                <td style={{ fontFamily: 'monospace' }}>{d.external_id || '—'}</td><td>{d.created_at}</td>
                <td>[<a href="#" onClick={e => { e.preventDefault(); showDoc(d.id, 'json') }}>json</a>
                  {' '}|{' '}<a href="#" onClick={e => { e.preventDefault(); showDoc(d.id, 'csv') }}>csv</a>]</td>
                <td>{d.status === 'draft' && <button onClick={() => mtAction(d.id, 'submit')}>Подать</button>}
                    {(d.status === 'submitted' || d.status === 'error') && <button onClick={() => mtAction(d.id, 'check')}>Проверить</button>}</td></tr>))}</tbody>
          </table></div>)}
      {tab === 'returns' && (
        <div style={{ marginTop: 12 }}>
          <button onClick={pollRet}>Обновить WB</button>
          <span style={{ marginLeft: 12 }}>к возврату в ЧЗ (PENDING_RETURN): <b>{(stats || {}).PENDING_RETURN || 0}</b></span>
          <input value={inn} onChange={e => setInn(e.target.value)} size={14} style={{ marginLeft: 12 }} />
          <button onClick={() => mkBatch('return')} style={{ marginLeft: 4 }}>Собрать возврат</button>
          <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', marginTop: 12, width: '100%' }}>
            <thead><tr>
              <th>заказ</th><th>предмет</th><th>статус WB</th><th>причина</th>
              <th>готов к выдаче</th><th>выдан продавцу</th><th>забрать до</th><th>ПВЗ</th>
            </tr></thead>
            <tbody>{wbRet.map(r => (
              <tr key={r.srid} style={{ background: retRowColor(r) }}>
                <td>{r.order_id}</td><td>{r.subject || r.srid}</td><td>{r.status}</td><td>{r.reason || '—'}</td>
                <td>{r.ready_dt || '—'}</td><td>{r.completed_dt || '—'}</td>
                <td>{r.expired_dt || '—'}</td><td>{r.office || '—'}</td>
              </tr>))}</tbody>
          </table>
          {wbRet.length === 0 && <p>Возвратов нет (или нажмите «Обновить WB» — квота 2 запроса/час).</p>}
          <h3>Документы возврата (LP_RETURN)</h3>
          <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead><tr><th>id</th><th>статус</th><th>ЧЗ uuid</th><th>создан</th><th></th><th>ЧЗ</th></tr></thead>
            <tbody>{docs.filter(d => d.type === 'LP_RETURN').map(d => (
              <tr key={d.id}><td>{d.id}</td><td>{d.status}</td>
                <td style={{ fontFamily: 'monospace' }}>{d.external_id || '—'}</td><td>{d.created_at}</td>
                <td>[<a href="#" onClick={e => { e.preventDefault(); showDoc(d.id, 'json') }}>json</a>]</td>
                <td>{d.status === 'draft' && <button onClick={() => mtAction(d.id, 'submit')}>Подать</button>}
                    {(d.status === 'submitted' || d.status === 'error') && <button onClick={() => mtAction(d.id, 'check')}>Проверить</button>}</td>
              </tr>))}</tbody>
          </table>
        </div>)}
      {tab === 'catalog' && (
        <div style={{ marginTop: 12 }}>
          <h3>Импорт</h3>
          <input ref={fileRef} type="file" accept=".xlsx" onChange={e => setImpFile(e.target.files[0] || null)} />
          <button onClick={doImport} style={{ marginLeft: 8 }} disabled={!impFile}>Импорт</button>

          <h3>Батчи</h3>
          <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead><tr><th>id</th><th>файл</th><th>статус</th><th>ok/err</th><th>создан</th><th>действия</th></tr></thead>
            <tbody>{nb.map(b => (
              <React.Fragment key={b.id}>
                <tr>
                  <td>{b.id}</td><td>{b.source_filename}</td><td>{b.status}</td>
                  <td>{b.stats ? `${b.stats.ok}/${b.stats.error}` : '—'}</td>
                  <td>{b.created_at}</td>
                  <td>
                    <button onClick={() => openNb(b.id)}>{nbOpen === b.id ? 'Скрыть карточки' : 'Карточки'}</button>
                    <button onClick={() => nbAction(b.id, 'feed')} style={{ marginLeft: 4 }}>Подать</button>
                    <button onClick={() => nbAction(b.id, 'refresh')} style={{ marginLeft: 4 }}>Обновить</button>
                    <button onClick={() => nbAction(b.id, 'sign')} style={{ marginLeft: 4 }}>Подписать</button>
                    <button onClick={() => nbReport(b.id, 'xlsx')} style={{ marginLeft: 4 }}>Отчёт xlsx</button>
                    <button onClick={() => nbReport(b.id, 'csv')} style={{ marginLeft: 4 }}>csv</button>
                  </td>
                </tr>
                {nbOpen === b.id && (
                  <tr><td colSpan={6}>
                    <select value={cardFilter} onChange={e => setCardFilter(e.target.value)}>
                      <option value="">все статусы карточек</option>
                      {CARD_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                    <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', marginTop: 8, width: '100%' }}>
                      <thead><tr><th>артикул</th><th>gtin</th><th>наименование</th><th>статус</th><th>ошибка</th></tr></thead>
                      <tbody>{nbCards.map(c => (
                        <tr key={c.id}>
                          <td>{c.article}</td>
                          <td style={{ fontFamily: 'monospace' }}>{c.gtin || '—'}</td>
                          <td>{c.name}</td>
                          <td>{c.status}</td>
                          <td style={{ color: 'red' }}>{c.error_text || ''}</td>
                        </tr>))}</tbody>
                    </table>
                  </td></tr>)}
              </React.Fragment>))}</tbody>
          </table>

          <h3>Декларации</h3>
          <input value={dnum} onChange={e => setDnum(e.target.value)} placeholder="номер" size={20} />
          <input value={ddate} onChange={e => setDdate(e.target.value)} type="date" style={{ marginLeft: 4 }} />
          <button onClick={addDecl} style={{ marginLeft: 4 }}>Добавить</button>
          <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', marginTop: 8, width: '100%' }}>
            <thead><tr><th>id</th><th>номер</th><th>дата</th><th>тип</th><th></th></tr></thead>
            <tbody>{decls.map(d => (
              <tr key={d.id}>
                <td>{d.id}</td><td>{d.doc_number}</td><td>{d.doc_date}</td><td>{d.doc_type}</td>
                <td>[<a href="#" onClick={e => { e.preventDefault(); delDecl(d.id) }}>удалить</a>]</td>
              </tr>))}</tbody>
          </table>

          <h3>Дефолты</h3>
          {DEF_FIELDS.map(([k, label]) => (
            <div key={k} style={{ marginBottom: 4 }}>
              <span style={{ display: 'inline-block', width: 180 }}>{label}:</span>
              <input value={(defs || {})[k] || ''} onChange={e => setDefs({ ...(defs || {}), [k]: e.target.value })}
                type={k === 'declaration_date' ? 'date' : 'text'} size={k === 'techreg' ? 60 : 40} />
            </div>))}
          <button onClick={saveDefs}>Сохранить</button>
        </div>)}
      <pre style={{ background: '#f4f4f4', padding: 8, marginTop: 12, maxHeight: 300, overflow: 'auto' }}>{pre || msg}</pre>
    </div>
  )
}
