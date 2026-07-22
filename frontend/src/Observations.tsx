import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { useModalFocus } from './modalFocus'

type Observation = { id: string; body: string; done: boolean; created_at: number }
type InboxState = { revision: string; observations: Observation[] }

// The observation inbox: a per-project capture surface. Drop quick notes while
// testing without switching to the agent, then hand the batch over in one insert.
// No AI. See CONTROL_PLANE_IDEAS.md §6.9.
export function Observations(
  { project, onClose, onInsertBatch }:
  { project: { id: string; name: string }; onClose: () => void; onInsertBatch?: (text: string) => void }
) {
  const [state, setState] = useState<InboxState>({ revision: 'missing', observations: [] })
  const [draft, setDraft] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const panel = useRef<HTMLElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  useModalFocus(panel, onClose)

  const base = `/api/projects/${encodeURIComponent(project.id)}/observations`
  const load = async () => {
    try { setState(await api<InboxState>('GET', base)) }
    catch (err) { setNote(err instanceof Error ? err.message : String(err)) }
  }
  useEffect(() => { void load(); requestAnimationFrame(() => input.current?.focus()) }, [project.id])

  const add = async () => {
    const body = draft.trim()
    if (!body || busy) return
    setBusy(true)
    try { setState(await api<InboxState>('POST', base, { body })); setDraft(''); input.current?.focus() }
    catch (err) { setNote(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  const replace = async (observations: Observation[]) => {
    setBusy(true)
    try { setState(await api<InboxState>('PUT', base, { observations, revision: state.revision })) }
    catch (err) {
      setNote(err instanceof Error ? err.message : String(err))
      await load()  // reconcile after a revision conflict
    }
    finally { setBusy(false) }
  }
  const toggle = (item: Observation) =>
    void replace(state.observations.map(o => o.id === item.id ? { ...o, done: !o.done } : o))
  const remove = (item: Observation) =>
    void replace(state.observations.filter(o => o.id !== item.id))
  const clearDone = () => void replace(state.observations.filter(o => !o.done))

  const open = state.observations.filter(o => !o.done)
  const asText = () => open.map(o => `- ${o.body}`).join('\n')
  const copyAll = () => { void navigator.clipboard.writeText(asText()); setNote('Copied open items to clipboard.') }
  const sendBatch = () => {
    if (!onInsertBatch) { setNote('Focus an agent terminal to insert into.'); return }
    if (!open.length) { setNote('No open observations to send.'); return }
    onInsertBatch(`${asText()}\n`)
    setNote('Inserted open items into the focused agent (not sent).')
  }

  return <div class="usage-layer observations-layer" role="dialog" aria-modal="true" aria-label="Observation inbox"
    onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section class="usage-panel observations-panel" ref={panel}>
      <header>
        <div><span>OBSERVATIONS</span><strong>{project.name} · capture now, hand to the agent later</strong></div>
        <div class="usage-header-actions"><button aria-label="Close observations" onClick={onClose}>×</button></div>
      </header>
      <div class="observations-compose">
        <textarea ref={input} rows={2} placeholder="Something you noticed while testing…" value={draft}
          onInput={e => setDraft(e.currentTarget.value)}
          onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void add() } }} />
        <button class="primary" disabled={busy || !draft.trim()} onClick={() => void add()}>capture <kbd>Ctrl+↵</kbd></button>
      </div>
      <div class="usage-progress" role="status" aria-live="polite"><span>·</span><strong>{note || `${open.length} open · ${state.observations.length - open.length} done`}</strong></div>
      <main>
        {state.observations.length === 0
          ? <div class="automation-empty"><strong>Inbox empty</strong><span>Drop a line whenever something catches your eye. Nothing is sent until you say so.</span></div>
          : <ul class="observations-list">{state.observations.map(item =>
            <li class={item.done ? 'done' : ''}>
              <label><input type="checkbox" checked={item.done} onChange={() => toggle(item)} /><span>{item.body}</span></label>
              <button aria-label="Delete observation" onClick={() => remove(item)}>×</button>
            </li>)}</ul>}
      </main>
      <footer>
        <button class="primary" disabled={!open.length} onClick={sendBatch}>↑ insert open items into agent</button>
        <button disabled={!open.length} onClick={copyAll}>copy open</button>
        <button disabled={state.observations.every(o => !o.done)} onClick={clearDone}>clear done</button>
      </footer>
    </section>
  </div>
}
