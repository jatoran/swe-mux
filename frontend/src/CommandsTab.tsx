import { useMemo, useState } from 'preact/hooks'
import { railPayload, resolveRail, type RailBackend, type RailItem } from './commandRail'
import { activatePromptRailItem } from './promptRail'
import { currentProfile, loadRailItems } from './deviceSettings'
import type { Session } from './types'

// The command rail's long tail, as a grid.
//
// The strip under a terminal holds what you hammer (Esc, Enter, arrows, ^C); it is
// horizontally scarce, which is why several built-ins used to ship switched off.
// Everything else — extra keys, skills, slash commands, literal text snippets —
// lives here, where room is not the constraint and items can carry full labels.
//
// This tab is session-scoped but renders outside the terminal pane, so it cannot
// touch xterm directly. Every activation goes over the same `mux:terminal-action`
// bus the pane already listens on, which keeps one owner for terminal writes.

type Props = {
  session: Session | null
  onDone: () => void
  onOpenSettings: () => void
}

function dispatch(sessionId: string, action: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId, action, ...detail } }))
}

/** Built-in action items the drawer can run; the rest are injection types. */
const ACTION_LABELS: Record<string, string> = {
  paste: 'Paste',
  copyReply: 'Copy reply',
  copyResume: 'Copy resume',
  branch: 'Branch',
  relaunch: 'Relaunch',
  toggleKeyboard: 'Keyboard',
  clipboardHistory: 'Clipboard',
}

export function CommandsTab({ session, onDone, onOpenSettings }: Props) {
  const [note, setNote] = useState('')
  const backend = (session?.backend || 'shell') as RailBackend
  const items = useMemo(
    () => resolveRail(loadRailItems(session?.project_id), { platform: currentProfile(), backend }, 'drawer'),
    [session?.project_id, backend],
  )

  if (!session) {
    return <>
      <p class="drawer-status">no terminal focused</p>
      <p class="drawer-empty">Session commands act on a terminal. Focus one and reopen this tab.</p>
    </>
  }

  const run = (item: RailItem) => {
    // Prompt templates are fetched from the library at click time, so this one path
    // is asynchronous: it closes the drawer only once the insert (or the hand-off to
    // the Prompts tab for variable filling) has actually happened.
    if (item.type === 'prompt') {
      void activatePromptRailItem(item, { sessionId: session.id, projectId: session.project_id })
        .then(problem => { if (problem) setNote(problem); else onDone() })
      return
    }
    if (item.type === 'key') dispatch(session.id, 'sendKey', { text: item.bytes || '' })
    else if (item.type === 'action') {
      // `clipboardHistory` is the drawer itself; running it from inside the drawer
      // would be a no-op, so it is filtered out of this grid entirely.
      dispatch(session.id, item.action === 'toggleKeyboard' ? 'toggleKeyboard' : item.action || '', {})
    } else {
      const payload = railPayload(item, backend)
      if (!payload) { setNote(`${item.label} has no payload configured.`); return }
      dispatch(session.id, 'insertText', { text: payload, submit: !!item.submit })
    }
    onDone()
  }

  const visible = items.filter(item => item.id !== 'clipboardHistory')
  const keys = visible.filter(item => item.type === 'key')
  const rest = visible.filter(item => item.type !== 'key')
  const label = (item: RailItem) =>
    item.type === 'action' ? ACTION_LABELS[item.action || ''] || item.label : item.label

  return <>
    <p class="drawer-status">{session.name || session.id} · {backend}</p>
    {rest.length > 0 && <div class="drawer-grid" role="group" aria-label="Session commands">
      {rest.map(item => <button key={item.id} title={item.title || (item.type === 'prompt' ? 'Insert this prompt template into the composer' : railPayload(item, backend)) || item.label} onClick={() => run(item)}>
        <span>{label(item)}</span>
        {item.type !== 'action' && <small>{item.type === 'skill' ? 'skill' : item.type === 'slash' ? 'command' : item.type === 'prompt' ? 'prompt' : 'text'}{item.type !== 'prompt' && item.submit ? ' · sends' : ''}</small>}
      </button>)}
    </div>}
    {keys.length > 0 && <div class="drawer-grid keys" role="group" aria-label="Terminal keys">
      {keys.map(item => <button key={item.id} title={item.title || item.label} onClick={() => run(item)}><span>{item.label}</span></button>)}
    </div>}
    {!visible.length && <p class="drawer-empty">Nothing is assigned to the drawer for this session. Move rail items here from Settings → Command rail.</p>}
    {note && <p class="clipboard-note" aria-live="polite">{note}</p>}
    <footer class="drawer-actions"><button onClick={onOpenSettings}>Edit command rail</button></footer>
  </>
}
