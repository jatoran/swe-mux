// The transcript reader's per-message chip row, over a stubbed daemon.
//
// The row is drawn by CSS state that no unit test can observe: it is absent from the
// resting column and revealed by hover on a pointer, by a tap on the entry on touch. Both
// halves are computed style plus a real input, and the failure that matters most - a row
// that is invisible but still takes the click - is invisible to a DOM assertion too.
//
// `?readAloud=1` turns the per-message read-aloud markers on, which is the four-chip row
// the header's reserved gutter has to widen for.
import { render } from 'preact'
import { useState } from 'preact/hooks'
import { TranscriptTab } from '../../src/TranscriptTab'
import type { Session } from '../../src/types'
import '../../src/deviceMode'
import '../../src/style.css'

const params = new URLSearchParams(location.search)
let bound = params.get('deferredBinding') !== '1'

const SESSION = {
  id: 's1', name: 'claude-0e7d93', generated_title: 'Transcript reader',
  project_id: 'p1', backend: 'claude', state: 'running', cwd: 'D:/PROJECTS/swe-mux',
  agent_run_id: 'run-1', pid: 4242,
} as Session

const message = (ordinal: number, role: 'user' | 'assistant', text: string) => ({
  message_id: `m${ordinal}`,
  ordinal,
  role,
  ts: new Date(Date.UTC(2026, 7, 30, 14, 5 + ordinal)).toISOString(),
  text,
  preceding_tool_calls: 0,
})

const TRANSCRIPT = {
  session_id: SESSION.id,
  agent_run_id: 'run-1',
  backend: 'claude',
  messages: [
    message(1, 'user', 'Make the transcript controls compact.'),
    message(2, 'assistant', 'The chip row is icon-only now, and it stays out of the column until you ask for it.'),
    message(3, 'user', 'And on a phone?'),
  ],
  hidden: 0,
  truncated: false,
  reason: null,
}

// `?long=1` stands in for a daemon that pages and answers deltas
// (`routes/scan_timeline.session_transcript`): a 450-message conversation read 200 at a
// time from the tail, `before=` for older pages, `since=` for what changed. Each
// message after the first is preceded by one tool call, whose input is only served by
// `/transcript/tool-calls`. `window.__transcriptAppend()` adds a message as an agent
// would, and `window.__transcriptRequests` records every query the reader sent.
const long = params.get('long') === '1'
const conversation = Array.from({ length: 450 }, (_, index) => ({
  ...message(index, index % 2 ? 'assistant' : 'user', `Message number ${index}. ${'Words to give it height. '.repeat(index % 3 + 1)}`),
  preceding_tool_calls: index ? 1 : 0,
  preceding_tools: index ? [{ id: `call-${index}`, name: 'Bash' }] : [],
}))
const stamps = conversation.map(() => 1)
let seq = 1
const requests: string[] = []
Object.assign(window, {
  __transcriptRequests: requests,
  __transcriptAppend: (text: string) => {
    seq += 1
    const index = conversation.length
    conversation.push({ ...message(index, 'assistant', text), preceding_tool_calls: 0, preceding_tools: [] })
    stamps.push(seq)
  },
})

function pagedTranscript(query: URLSearchParams) {
  const page = (start: number, end: number, kind: string, extra: object = {}) => ({
    ...TRANSCRIPT,
    messages: conversation.slice(start, end),
    generation: 'h.1', seq, total: conversation.length, has_older: start > 0,
    page: kind, delta: kind === 'delta', replace_from: null, reset: false, ...extra,
  })
  const limit = Number(query.get('limit') || 200)
  const before = query.get('before')
  const since = query.get('since')
  if (since !== null && query.get('generation') === 'h.1') {
    const oldest = conversation.findIndex(item => item.message_id === query.get('oldest'))
    const floor = Math.max(0, oldest)
    const first = stamps.findIndex((stamp, index) => index >= floor && stamp > Number(since))
    return first < 0
      ? page(floor, floor, 'delta', { has_older: floor > 0 })
      : page(first, conversation.length, 'delta', { has_older: floor > 0, replace_from: conversation[first].message_id })
  }
  const end = before ? conversation.findIndex(item => item.message_id === before) : conversation.length
  return page(Math.max(0, end - limit), end, before ? 'older' : 'tail')
}

window.fetch = (async (input: RequestInfo | URL) => {
  const raw = String(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url)
  const url = new URL(raw, location.href)
  const path = url.pathname
  let body: unknown
  // No clips: the markers render in their resting state, which is the state whose width
  // the header gutter has to clear. Which state a marker is in is `transcriptAudio`'s
  // business and is tested there.
  if (path.includes('/voice/clips')) body = { items: [] }
  else if (path.endsWith('/transcript/tool-calls')) {
    const id = url.searchParams.get('message_id')
    const found = conversation.find(item => item.message_id === id)
    body = found
      ? { calls: found.preceding_tools.map(tool => ({ ...tool, input: { command: `ls ${found.ordinal}` } })), reason: null }
      : { calls: [], reason: 'message_unavailable' }
  } else if (long) {
    requests.push(url.search)
    body = pagedTranscript(url.searchParams)
  } else body = bound ? TRANSCRIPT : { ...TRANSCRIPT, messages: [], reason: 'no_transcript' }
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

function Harness() {
  const [session,setSession]=useState({...SESSION,native_session_id:'placeholder'})
  return <div style="width:100%;height:100dvh;display:flex;flex-direction:column">
    {params.get('deferredBinding')==='1'&&<button onClick={()=>{
      bound=true
      setSession({...session,native_session_id:'verified-fork'})
    }}>Bind fork transcript</button>}
    <TranscriptTab session={session} readAloud={params.get('readAloud') === '1'} />
  </div>
}

render(
  <Harness />,
  document.querySelector('#root')!,
)
