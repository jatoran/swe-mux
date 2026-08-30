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
import { TranscriptTab } from '../../src/TranscriptTab'
import type { Session } from '../../src/types'
import '../../src/style.css'

const params = new URLSearchParams(location.search)

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

window.fetch = (async (input: RequestInfo | URL) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  // No clips: the markers render in their resting state, which is the state whose width
  // the header gutter has to clear. Which state a marker is in is `transcriptAudio`'s
  // business and is tested there.
  const body = path.includes('/voice/clips') ? { items: [] } : TRANSCRIPT
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

render(
  <div style="width:100%;height:100dvh;display:flex;flex-direction:column">
    <TranscriptTab session={SESSION} readAloud={params.get('readAloud') === '1'} />
  </div>,
  document.querySelector('#root')!,
)
