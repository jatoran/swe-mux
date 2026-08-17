// The real Notes drawer, at a real drawer width, so the tab rail actually overflows.
//
// That overflow is the whole point: `OverflowRail` only claims the pointer for its
// horizontal pan when the strip is wider than its box, and claiming it retargets every
// later pointer event to the rail. A tab's own `pointermove`/`pointerup` handlers then
// never run, which is why the long-press that opens a tab's actions menu watches `window`
// instead. Nothing below the browser can reproduce that: pointer capture needs a real
// pointer, so the spec drives trusted touch through CDP.
//
// `?notes=one` renders a Project with a single note — the collection the daemon refuses to
// empty — so the menu's disabled delete can be measured as drawn rather than as intended.
import { render } from 'preact'
import { NotesTab, type ProjectNoteSummary } from '../../src/NotesTab'
import type { Project } from '../../src/types'
import '../../src/style.css'

const NOW = 1_770_000_000
const params = new URLSearchParams(location.search)
const single = params.get('notes') === 'one'

const note = (index: number): ProjectNoteSummary => ({
  note_id: `note-${index}`,
  project_id: 'p1',
  project_name: 'Project One',
  // Long enough that a handful of them cannot fit a phone-width drawer.
  title: `Working note number ${index}`,
  created_at: NOW - 1000 * (20 - index),
  updated_at: NOW - index,
  bytes: 512,
  revision: `rev-${index}`,
  excerpt: 'note body text',
  origin_session_id: null,
})

const ITEMS = single ? [note(1)] : Array.from({ length: 8 }, (_, index) => note(index + 1))

// Assigned before the first render, because the collection loads from an effect.
window.fetch = (async (input: RequestInfo | URL) => {
  const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const body = path.includes('/api/notes')
    ? { items: ITEMS }
    : { error: `unstubbed request: ${path}` }
  return new Response(JSON.stringify(body), {
    status: path.includes('/api/notes') ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  })
}) as typeof fetch

const project = { id: 'p1', name: 'Project One' } as unknown as Project

render(
  <div class="utility-drawer" style="width:320px;height:440px;display:flex;flex-direction:column">
    <NotesTab
      project={project}
      allProjects={false}
      onAllProjects={() => {}}
      onOpenNote={() => {}}
      onOpenScratchpad={() => {}}
      onDone={() => {}}
      selectedResourceId={`note:${ITEMS[0].note_id}`}
      editor={<div class="notes-state">editor</div>}
      onPopSelected={() => {}}
    />
  </div>,
  document.querySelector('#root')!,
)
