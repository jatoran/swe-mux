import { render } from 'preact'
import { PreviewPane } from '../../src/PreviewPane'
import type { Preview } from '../../src/processFleet'
import '../../src/deviceMode'
import '../../src/style.css'

// One loopback Preview pane, with or without a shared port (`?conflict=wrong|none`).
// The conflicted one reproduces the 2026-09-26 incident: the task's own server on 8766
// never answered, an orphaned `http.server` did, and the pane has to say so above a
// frame that is still fully usable. The frame's route does not exist on this server,
// which is fine: what is under test is the chrome around it.

const conflicted = new URLSearchParams(location.search).get('conflict') !== 'none'

const preview: Preview = {
  id: 'bd9cd80852142644',
  session_id: 'task',
  project_id: 'workout-plan',
  url: 'http://127.0.0.1:8766/',
  host: '127.0.0.1',
  port: 8766,
  source: 'detected',
  viewport: 'responsive',
  kind: 'loopback',
  entry: '',
  static_server: false,
  port_conflict: conflicted ? {
    host: '127.0.0.1',
    port: 8766,
    answering_pid: 136796,
    answering_is_owner: false,
    listeners: [
      {
        pid: 25604, executable: 'python.exe', command: 'python.exe -u D:\\PROJECTS\\workout-plan/logger.py --tailscale',
        started_at: 1, session_id: 'task', project_id: 'workout-plan', evidence_state: 'active', owner: true,
      },
      {
        pid: 136796, executable: 'python.exe', command: 'python.exe -m http.server 8766 --bind 127.0.0.1',
        started_at: 0, session_id: null, project_id: null, evidence_state: null, owner: false,
      },
    ],
  } : null,
}

render(
  <div id="pane-host" style="position:fixed;inset:0;display:flex">
    <PreviewPane preview={preview} onClose={() => {}} />
  </div>,
  document.getElementById('root')!,
)
