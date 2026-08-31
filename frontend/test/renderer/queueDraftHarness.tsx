// The Queue tab's compose-and-edit surface, mounted over a stubbed daemon that actually
// stores what it is sent.
//
// The claims this exists to check are runtime ones no source-shape assertion can reach:
// that `+` writes nothing until something is typed, that what is typed reaches the daemon
// with no Save button pressed, that unmounting the pane mid-sentence still saves, and that
// the controls a staging decision needs stay on screen while the field is open.
//
// `fetch` is stubbed rather than the modules mocked, so the real `queueApi`, the real
// `queueDraftSaver` debounce, and the real component all run — including the create/PATCH
// split, which is exactly where a regression would hide.
import { render } from 'preact'
import { QueuePane } from '../../src/QueuePane'
import type { DeliveryReadiness, Session } from '../../src/types'
import '../../src/style.css'

type Row = {
  id: string
  body: string
  revision: number
  state: string
  armed_at: number | null
  constraints: Record<string, unknown> | null
  position: number
}

const rows: Row[] = []
//: Every write the harness took, in order, so a spec can assert on request *count* — the
//: difference between "autosave works" and "autosave PATCHes on every keystroke".
const writes: string[] = []
let nextId = 1

const message = (row: Row) => ({
  id: row.id,
  target_session_id: 'session',
  target_agent_run_id: 'run-1',
  target_backend: 'claude',
  target_label: 'claude-1',
  project_id: 'p1',
  position: row.position,
  state: row.state,
  body: row.body,
  revision: row.revision,
  sender_kind: 'user',
  sender_id: null,
  sender_label: null,
  origin_session_id: null,
  correlation_id: null,
  chain_depth: 0,
  origin: null,
  constraints: row.constraints,
  blocked_reasons: null,
  stranded_reason: null,
  cancel_kind: null,
  retargeted_from: null,
  created_at: 0,
  updated_at: 0,
  edited_at: null,
  armed_at: row.armed_at,
  sent_at: null,
  target_live: true,
})

const readiness: DeliveryReadiness = {
  state: 'safe',
  reason: 'all_required_evidence_positive',
  reasons: ['all_required_evidence_positive'],
  protected: [],
  interject_state: 'safe',
  observed_at: Date.now() / 1000,
  authorized: false,
}

const session = {
  id: 'session',
  name: 'claude-1',
  project_id: 'p1',
  backend: 'claude',
  state: 'idle',
  cwd: '.',
  agent_run_id: 'run-1',
  delivery_readiness: readiness,
} as Session

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  const method = (init?.method || 'GET').toUpperCase()
  const payload = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {}

  if (url.includes('/api/queue/auto')) {
    return json({
      master_enabled: true, paused: false,
      quiet_hours: { start: '', end: '', active: false },
      stable_seconds: 3, max_consecutive: 3, session_ttl_minutes: 30, reply_window_minutes: 30,
      sessions: [], counters: {},
      promotion: {
        criteria: {}, met: false, auto_sends: 0, unsafe_reports: 0, proving_days: 0,
        required_sends: 0, required_days: 0, fixture_classes: [],
      },
      last_error: '',
    })
  }

  if (url.includes('/api/queue/send-next') && method === 'POST') {
    const row = rows.find(item => item.id === payload.message_id)
    if (!row) return json({ error: 'no such queue message', code: 'not_found' }, 404)
    //: The daemon quotes the revision back, which is what catches a send issued on a body
    //: the sender had already edited.
    if (payload.revision !== row.revision) {
      return json(
        { error: 'the message changed since you last saw it', code: 'revision_conflict', revision: row.revision },
        409,
      )
    }
    writes.push(`send:${row.body}`)
    row.state = 'sent'
    return json({ status: 'sent', confirmed: false })
  }

  if (url.includes('/api/queue/messages') && method === 'POST') {
    const body = String(payload.body ?? '')
    //: The daemon's own refusal, reproduced: an empty body is a 400, never a row.
    if (!body) return json({ error: 'body must contain 1–500000 characters', code: 'invalid_body' }, 400)
    writes.push(`create:${body}`)
    const row: Row = {
      id: `m${nextId++}`,
      body,
      revision: 1,
      state: payload.armed ? 'armed' : 'draft',
      armed_at: payload.armed ? 1 : null,
      constraints: (payload.constraints as Record<string, unknown> | undefined) ?? null,
      position: rows.length + 1,
    }
    rows.push(row)
    return json(message(row))
  }

  const patch = url.match(/\/api\/queue\/messages\/([^/?]+)$/)
  if (patch && method === 'PATCH') {
    const row = rows.find(item => item.id === patch[1])
    if (!row) return json({ error: 'no such queue message', code: 'not_found' }, 404)
    if (typeof payload.body === 'string') {
      if (payload.revision !== row.revision) {
        return json(
          { error: 'the message changed since you last saw it', code: 'revision_conflict', revision: row.revision },
          409,
        )
      }
      writes.push(`update:${payload.body}`)
      row.body = payload.body
      row.revision += 1
    }
    if (typeof payload.armed === 'boolean') {
      writes.push(`armed:${payload.armed}`)
      row.state = payload.armed ? 'armed' : 'draft'
      row.armed_at = payload.armed ? 1 : row.armed_at
    }
    if ('constraints' in payload) {
      writes.push(`constraints:${JSON.stringify(payload.constraints)}`)
      row.constraints = (payload.constraints as Record<string, unknown> | null) ?? null
    }
    return json(message(row))
  }

  if (patch && method === 'DELETE') {
    writes.push(`delete:${patch[1]}`)
    const index = rows.findIndex(item => item.id === patch[1])
    if (index >= 0) rows.splice(index, 1)
    return json({ deleted: true, message_id: patch[1], already_deleted: false })
  }

  if (url.includes('/api/queue/messages')) {
    return json({
      target: {
        session_id: 'session',
        live: true,
        agent_run_id: 'run-1',
        label: 'claude-1',
        state: 'idle',
        delivery_readiness: session.delivery_readiness,
      },
      messages: rows.map(message),
      pending: rows.length,
    })
  }
  return json({})
}) as typeof fetch

const root = document.querySelector<HTMLElement>('#root')!
document.body.style.margin = '0'
//: The drawer's documented minimum. Everything here has to fit in it.
document.body.style.width = '300px'
document.documentElement.style.setProperty('--ui-scale', '1')

const draw = () => render(<QueuePane sessionId="session" sessions={[session]} />, root)
draw()

Object.assign(window as unknown as Record<string, unknown>, {
  __writes: () => writes.slice(),
  __rows: () => rows.map(row => ({ ...row })),
  //: Stands in for the drawer being swiped shut: the pane is unmounted with the field
  //: still holding characters the debounce has not fired for.
  __unmount: () => render(null, root),
  __remount: () => draw(),
})
