import React, { useEffect, useState } from 'react'

const api = async (path, token, opts = {}) => {
  const r = await fetch(path, { ...opts, headers: { 'Authorization': `Bearer ${token.trim()}`,
    'Content-Type': 'application/json', ...(opts.headers || {}) } })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text()
}

export default function App() {
  const [token, setToken] = useState(localStorage.getItem('tok') || '')
  const [tab, setTab] = useState('journal')
  const [items, setItems] = useState([]); const [docs, setDocs] = useState([])
  const [stats, setStats] = useState(null)
  const [inn, setInn] = useState('090201471350'); const [msg, setMsg] = useState('')
  const [pre, setPre] = useState('')

  const load = async () => {
    if (!token) return
    try {
      setItems(await api('/v1/journal?limit=200', token))
      setDocs(await api('/v1/docs', token))
      setStats(await api('/v1/journal/stats', token)); setMsg('')
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  useEffect(() => { localStorage.setItem('tok', token); load() }, [token])

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

  return (
    <div style={{ fontFamily: 'sans-serif', margin: '0 auto', maxWidth: 1100 }}>
      <h2>MP-GIS_MT</h2>
      <input value={token} onChange={e => setToken(e.target.value)} placeholder="API token" size={40} />
      {['journal', 'batches'].map(t => (
        <button key={t} onClick={() => setTab(t)} style={{ marginLeft: 8, fontWeight: tab === t ? 'bold' : 'normal' }}>
          {t === 'journal' ? 'Журнал' : 'Батчи'}</button>))}
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
            <thead><tr><th>id</th><th>тип</th><th>статус</th><th>создан</th><th>Док</th></tr></thead>
            <tbody>{docs.map(d => (
              <tr key={d.id}><td>{d.id}</td><td>{d.type}</td><td>{d.status}</td><td>{d.created_at}</td>
                <td>[<a href="#" onClick={e => { e.preventDefault(); showDoc(d.id, 'json') }}>json</a>
                  {' '}|{' '}<a href="#" onClick={e => { e.preventDefault(); showDoc(d.id, 'csv') }}>csv</a>]</td></tr>))}</tbody>
          </table></div>)}
      <pre style={{ background: '#f4f4f4', padding: 8, marginTop: 12, maxHeight: 300, overflow: 'auto' }}>{pre || msg}</pre>
    </div>
  )
}
