import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { DRAWER_TABS, type DrawerTabId } from '../src/drawerTabs.ts'
import {
  DRAWER_SEGMENTS,
  DRAWER_SEGMENT_TABS,
  availableDrawerSegments,
  drawerSectionTarget,
  drawerSegment,
  drawerSegmentsFor,
  hasDrawerSegments,
  resolveDrawerSegment,
} from '../src/drawerSegments.ts'

const source = (name: string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')
const agent = { hasTranscript: true, isAgentSession: true }
const shell = { hasTranscript: false, isAgentSession: false }

test('every segment belongs to a registered tab and is unique within it', () => {
  const ids = new Set(DRAWER_TABS.map(tab => tab.id))
  const seen = new Set<string>()
  for (const segment of DRAWER_SEGMENTS) {
    assert.ok(ids.has(segment.tab), `${segment.tab} is not a registered tab`)
    const key = `${segment.tab}:${segment.id}`
    assert.equal(seen.has(key), false, `${key} is registered twice`)
    seen.add(key)
    // The label is drawn in a segmented control three across a 300px column, and it is
    // also the generated command's name, so it must stay short and readable on its own.
    assert.ok(segment.label.length > 0 && segment.label.length <= 18, `${key} label`)
    assert.ok(segment.title.length > 0, `${key} needs a tooltip`)
  }
})

test('a segmented tab has at least two segments, and a section-only tab has none', () => {
  // One segment is not a choice, and drawing a one-button segmented control says the
  // opposite. `DRAWER_SEGMENT_TABS` is what the host asks before drawing anything.
  for (const tab of DRAWER_SEGMENT_TABS) {
    assert.ok(drawerSegmentsFor(tab, 'segment').length >= 2, `${tab} needs a real choice`)
  }
  assert.deepEqual([...DRAWER_SEGMENT_TABS].sort(), ['activity', 'agent', 'git'])
  // Actions is sections only: everything on it ends in text reaching the focused agent,
  // so a mode switch between "the thing I want to send" and "the other thing I want to
  // send" would buy nothing and cost a click.
  assert.equal(hasDrawerSegments('actions'), false)
  assert.equal(drawerSegmentsFor('actions', 'section').length, 4)
})

test('unavailable segments drop out and the selection falls back to one that works', () => {
  // The rule InsightTab used to carry inline for Timeline, now applied to every
  // segmented tab: a stored choice that cannot render must never leave an empty body.
  assert.deepEqual(availableDrawerSegments('activity', agent).map(item => item.id), ['timeline', 'findings', 'changes'])
  assert.deepEqual(availableDrawerSegments('activity', shell).map(item => item.id), ['findings', 'changes'])
  assert.equal(resolveDrawerSegment('activity', 'timeline', shell), 'findings')
  assert.equal(resolveDrawerSegment('activity', 'timeline', agent), 'timeline')

  // A shell session keeps Agent's instruction files, which is what the separate,
  // Project-scoped Context tab used to be for. Config and Tools read a live harness
  // inventory and are the two that go.
  assert.deepEqual(availableDrawerSegments('agent', agent).map(item => item.id), ['config', 'tools', 'instructions'])
  assert.deepEqual(availableDrawerSegments('agent', shell).map(item => item.id), ['instructions'])
  assert.equal(resolveDrawerSegment('agent', 'tools', shell), 'instructions')

  // Unknown and absent selections resolve to the first available segment; a tab with no
  // segments resolves to null so the host draws its body directly.
  assert.equal(resolveDrawerSegment('activity', undefined, agent), 'timeline')
  assert.equal(resolveDrawerSegment('activity', 'nonsense', agent), 'timeline')
  assert.equal(resolveDrawerSegment('queue', 'anything', agent), null)
  assert.equal(resolveDrawerSegment('actions', 'clipboard', agent), null, 'sections are not selections')
})

test('lookup is exact and never crosses tabs', () => {
  assert.equal(drawerSegment('activity', 'changes')?.label, 'Changes')
  assert.equal(drawerSegment('agent', 'changes'), null)
  assert.equal(drawerSegment('activity', 'nope'), null)
})

test('section targets are namespaced so they cannot collide with Settings', () => {
  // Sections reuse `settingReveal.ts` rather than growing a parallel mechanism, so their
  // `[data-setting]` values share that namespace and must be prefixed.
  assert.equal(drawerSectionTarget('actions', 'clipboard'), 'drawer.actions.clipboard')
  const actions = source('ActionsTab.tsx')
  assert.ok(actions.includes("data-setting={drawerSectionTarget('actions', id)}"))
  // On the section element, not its body: the body is unmounted while collapsed, and the
  // reveal has to have something to scroll to in the frame before the expansion lands.
  const marked = actions.slice(actions.indexOf('class={`actions-section actions-section-'))
  assert.ok(marked.slice(0, 160).includes("data-setting={drawerSectionTarget('actions', id)}"))
  assert.ok(!actions.includes('class="actions-section-body" data-setting'), 'the body is unmounted while collapsed')
})

test('every segment and section gets a palette entry and a voice phrase', () => {
  // This is the whole reason the registry exists. Folding Clipboard into Actions and
  // Change Map into Activity would otherwise have deleted "open Clipboard" and "open
  // Change Map" as commands *and* as voice phrases, so the merge would have cost a click
  // at the rail and an entire navigation path for anyone who works by name.
  const app = source('App.tsx')
  assert.ok(app.includes('...DRAWER_SEGMENTS.map((segment): Command => ({'))
  assert.ok(app.includes('id:`drawer.${segment.tab}.${segment.id}`'))
  assert.ok(app.includes('phrases:[`open ${segment.label}`,`show ${segment.label}`,`go to ${segment.label}`]'))
  // A segment is selected; a section is revealed, because a co-visible region cannot be
  // "selected" and pretending otherwise would scroll nowhere.
  assert.ok(app.includes("if(segment.kind==='section')revealDrawerSection(segment.tab,segment.id)"))
  assert.ok(app.includes('else openDrawerTab(segment.tab,projectId,segment.id)'))
  // Command ids must be unique across the tab commands they sit beside.
  const ids = DRAWER_SEGMENTS.map(segment => `drawer.${segment.tab}.${segment.id}`)
  assert.equal(new Set(ids).size, ids.length)
  const tabIds = new Set(DRAWER_TABS.flatMap(tab => [`drawer.${tab.id}`, `drawer.show:${tab.id}`]))
  for (const id of ids) assert.equal(tabIds.has(id), false, `${id} collides with a tab command`)
})

test('the segment selection is persisted per Project beside the tab selection', () => {
  const layout = source('drawerLayout.ts')
  const app = source('App.tsx')
  // Keyed by tab rather than by stack: a tab lives in exactly one stack, and keying by
  // stack would lose the choice the moment the tab were dragged into another pane.
  assert.ok(layout.includes('selected_segments: Record<string, string>'))
  assert.ok(layout.includes("export const DRAWER_PROJECT_PRESENTATIONS_KEY = 'mux.drawer.projects.v3'"))
  // v2 is read as a fallback tier, so an existing arrangement is not thrown away by the
  // version bump; `normalizeDrawerProjectPresentation` fills in the missing segment map.
  assert.ok(layout.includes("export const DRAWER_PROJECT_PRESENTATIONS_KEY_V2 = 'mux.drawer.projects.v2'"))
  assert.ok(app.includes('localStorage.getItem(DRAWER_PROJECT_PRESENTATIONS_KEY_V2)'))
  assert.ok(app.includes('localStorage.removeItem(DRAWER_PROJECT_PRESENTATIONS_KEY_V2)'))
  assert.ok(app.includes('onSegment={selectDrawerTabSegment}'))
})

test('the host draws one shared control and keeps only what must stay mounted', () => {
  const host = source('UtilityDrawer.tsx')
  const control = source('DrawerSegmentControl.tsx')
  assert.ok(host.includes('<DrawerSegmentControl'))
  // Unavailable segments are dropped rather than disabled: a greyed-out "Timeline" on a
  // shell session promises a surface that does not exist.
  assert.ok(control.includes('const segments = availableDrawerSegments(tab, context)'))
  assert.ok(control.includes('if (segments.length < 2) return null'))
  assert.ok(!control.includes('disabled='), 'unavailable segments are omitted, not disabled')
  // Lazy mount, then never unmount, and only for segments that ask.
  assert.ok(host.includes("else if (!segment.keepMounted || !mountedSegments.current.has(key)) return null"))
  // `hidden` rather than a class, since the class sets `display:flex` and would beat the
  // user-agent default - the trap `.drawer-note-host[hidden]` already documents.
  assert.ok(host.includes('hidden={!on}'))
  assert.ok(source('style.css').includes('.drawer-segment-body[hidden]{display:none}'))
})

test('Git owns no view state of its own', () => {
  // Registering Git's readings is what leaves the drawer with one mechanism for this
  // idea instead of two, and it buys "open Git Log" as a voice phrase for free. Land
  // is a segment for the same reason, and for the watch-here/act-there split: Map
  // answers "what is in this worktree", Land answers "what is happening to it".
  const git = source('GitTab.tsx')
  assert.ok(git.includes('export type GitView'))
  assert.ok(git.includes('view:GitView'))
  assert.ok(!git.includes("useState<GitView>"), 'the view is owned by the drawer host')
  assert.ok(!git.includes('git-view-toggle'), 'the bespoke toggle is replaced by the shared control')
  const tabs: DrawerTabId[] = ['git']
  for (const tab of tabs) {
    assert.deepEqual(
      drawerSegmentsFor(tab, 'segment').map(item => item.id),
      ['map', 'log', 'provenance', 'land'],
    )
  }
})
