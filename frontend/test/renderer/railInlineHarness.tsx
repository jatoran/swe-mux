// The in-place rail editor as a terminal pane mounts it: inside the rail area's
// box, editing the same device-settings cache the modal edits. `?backend=` picks
// the session type (default claude) so the backend dimming is observable.
import { render } from 'preact'
import { RailInlineEditor } from '../../src/RailInlineEditor'
import '../../src/style.css'

declare global {
  interface Window {
    railInlineClosed: boolean
    railInlineOpenedFull: boolean
  }
}
window.railInlineClosed = false
window.railInlineOpenedFull = false

window.fetch = (async (input: RequestInfo | URL) => {
  const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const ok = path.includes('/api/settings')
  return new Response(JSON.stringify(ok ? { profiles: {} } : { error: `unstubbed request: ${path}` }), {
    status: ok ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  })
}) as typeof fetch

const backend = new URLSearchParams(location.search).get('backend') || 'claude'

render(
  <div class="terminal-surface" style="width:900px;height:520px">
    <div class="terminal-host" />
    <div class="terminal-action-rail rail-editing">
      <RailInlineEditor
        backend={backend}
        onOpenFull={() => { window.railInlineOpenedFull = true }}
        onClose={() => { window.railInlineClosed = true }}
      />
    </div>
  </div>,
  document.querySelector('#root')!,
)
