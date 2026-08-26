// The Files tab's drawer chrome, over a stubbed daemon.
//
// Three things here are CSS and DOM structure rather than logic, so no unit test can see
// them: that the two readings of a Project (the tree and Recent) are named subtabs rather
// than a pressed icon inside the search row, that those subtab labels keep their full width
// at a phone's drawer width instead of ellipsising under the Project name beside them, and
// that the header carries a way to the ignore patterns that decide what the tree contains.
//
// `?view=recent` selects the second subtab from the first paint, and `?width=` sets the
// drawer's width, because "does it still fit" is the whole question at 320px.
import { render } from 'preact'
import { useState } from 'preact/hooks'
import { DrawerSegmentControl } from '../../src/DrawerSegmentControl'
import { ProjectResource } from '../../src/ProjectResource'
import type { Project } from '../../src/types'
import '../../src/style.css'

const params = new URLSearchParams(location.search)
const width = Number(params.get('width')) || 380

const PROJECT = { id: 'p1', name: 'swe-mux', root: 'D:/PROJECTS/swe-mux' } as Project

const ROOT = {
  path: '', parent: null, truncated: false,
  items: [
    { name: 'frontend', path: 'frontend', kind: 'directory', size: null },
    { name: 'src', path: 'src', kind: 'directory', size: null },
    { name: 'README.md', path: 'README.md', kind: 'file', size: 2048 },
  ],
}

const RECENT = {
  available: true,
  items: [
    { name: 'style.css', path: 'frontend/src/style.css', kind: 'file', origin: 'working', status: 'M', committed_at: null },
    { name: 'gitLand.ts', path: 'frontend/src/gitLand.ts', kind: 'file', origin: 'committed', status: null, committed_at: 1_770_000_000 },
  ],
}

type SearchRequestRecord = { query: string; aborted: boolean }
const searchRequests: SearchRequestRecord[] = []
Object.assign(globalThis, { __fileSearchRequests: searchRequests })

function delayedSearch(query: string, signal?: AbortSignal | null): Promise<Response> {
  const record = { query, aborted: false }
  searchRequests.push(record)
  const delay = query === 'road' ? 800 : query === 'roadmap' ? 400 : 0
  const items = query === 'r'
    ? [
        { name: 'README.md', path: 'README.md', match: 'name', line: null, snippet: null },
        { name: 'runtime.ts', path: 'frontend/src/runtime.ts', match: 'name', line: null, snippet: null },
      ]
    : query === 'roadmap'
      ? [{ name: 'ROADMAP.md', path: '.docs/development/ROADMAP.md', match: 'name', line: null, snippet: null }]
      : query === 'road'
        ? [{ name: 'old-road.txt', path: 'old-road.txt', match: 'name', line: null, snippet: null }]
        : []
  return new Promise((resolve, reject) => {
    const abort = () => {
      record.aborted = true
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', abort)
      resolve(new Response(JSON.stringify({ items, truncated: false, truncated_reason: null, stopped_at: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    }, delay)
    if (signal?.aborted) abort()
    else signal?.addEventListener('abort', abort, { once: true })
  })
}

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  if (path.includes('/search')) {
    const query = new URL(path, location.href).searchParams.get('q') || ''
    return delayedSearch(query, init?.signal)
  }
  const body = path.includes('/files/recent') ? RECENT
    : path.includes('/files/tree') ? { directories: { '': ROOT } }
      : path.includes('/files') ? ROOT
        : {}
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

/** Every `mux:open-setting` the page dispatches, so the spec can prove the header's link
 *  asks for the ignore patterns rather than merely looking like a button. */
const settingRequests: string[] = []
Object.assign(globalThis, { __settingRequests: settingRequests })
window.addEventListener('mux:open-setting', event => {
  settingRequests.push(String((event as CustomEvent<{ target?: string }>).detail?.target ?? ''))
})

function FilesHarness() {
  const [segment, setSegment] = useState(params.get('view') === 'recent' ? 'recent' : 'explorer')
  // The real drawer chrome: Files draws its segments *in* the heading row, because its two
  // headings are its two segment labels, and the scope beside them is the Project's bare
  // name with no `Project:` in front of it.
  return <aside class="utility-drawer docked" style={`width:${width}px;height:100dvh;display:flex`}>
    <section class="drawer-pane" style="flex:1;min-width:0;min-height:0;display:flex;flex-direction:column">
      <div class="drawer-body drawer-body-files" style="flex:1;min-width:0;min-height:0;display:flex;flex-direction:column">
        <div class="drawer-pane-heading with-segments">
          <DrawerSegmentControl tab="files" active={segment} inline
            context={{ hasTranscript: true, isAgentSession: true }} onSelect={setSegment} />
          <span class="drawer-scope-context">{PROJECT.name}</span>
          <button class="drawer-collapse" aria-label="Collapse side panel">×</button>
        </div>
        <div class="drawer-segment-body" style="flex:1;min-height:0;display:flex;flex-direction:column">
          <ProjectResource
            project={PROJECT}
            resource={{ kind: 'files', id: PROJECT.id }}
            filesView={segment === 'recent' ? 'recent' : 'explorer'}
            onOpenFile={() => {}}
          />
        </div>
      </div>
    </section>
  </aside>
}

render(<FilesHarness />, document.querySelector('#root')!)
