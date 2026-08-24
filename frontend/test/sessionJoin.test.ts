import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { paneStacks, parseLayout, stackForView, terminalIds } from '../src/layout.ts'
import {
  MAX_JOIN_ATTEMPTS, forgetJoinAttempts, joinAnchorId, joinSessions, joinableSessionIds,
  recordJoinFailure, unjoinedSessionIds,
} from '../src/sessionJoin.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

/** Two panes: a resource pane on the left, the Project's agents on the right. */
const workspace = () => parseLayout({
  version: 7,
  root: {
    type: 'split', id: 'split-root', direction: 'horizontal', ratio: .5,
    first: { type: 'stack', id: 'pane-notes', active_child_id: 'note-a', children: [
      { type: 'leaf', kind: 'note', id: 'note-a' },
    ] },
    second: { type: 'stack', id: 'pane-agents', active_child_id: 'term-b', children: [
      { type: 'leaf', kind: 'terminal', id: 'term-a' },
      { type: 'leaf', kind: 'terminal', id: 'term-b' },
    ] },
  },
})

test('a daemon-started session joins the focused pane without taking its active tab', () => {
  const layout = workspace()
  const joined = joinSessions(layout, ['term-a', 'term-b', 'daemon-1'], 'term-a')
  const pane = stackForView(joined, 'daemon-1')
  assert.equal(pane?.id, 'pane-agents')
  assert.deepEqual(pane?.children.map(child => child.id), ['term-a', 'term-b', 'daemon-1'])
  // The operator was looking at term-b; the new tab is behind it, not in front of it.
  assert.equal(pane?.active_child_id, 'term-b')
})

test('a join lands in the pane that already holds terminals when no view is focused here', () => {
  const joined = joinSessions(workspace(), ['daemon-1'], null)
  assert.equal(stackForView(joined, 'daemon-1')?.id, 'pane-agents')
  assert.equal(paneStacks(joined).length, 2)
})

test('a stale preferred view falls back to the terminal pane rather than inventing one', () => {
  assert.equal(joinAnchorId(workspace(), 'closed-tab'), 'term-a')
  const joined = joinSessions(workspace(), ['daemon-1'], 'closed-tab')
  assert.equal(stackForView(joined, 'daemon-1')?.id, 'pane-agents')
  assert.equal(paneStacks(joined).length, 2)
})

test('a focused note pane receives the join and keeps showing the note', () => {
  const joined = joinSessions(workspace(), ['daemon-1'], 'note-a')
  const pane = stackForView(joined, 'daemon-1')
  assert.equal(pane?.id, 'pane-notes')
  assert.equal(pane?.active_child_id, 'note-a')
})

test('several daemon sessions join one pane in fleet order', () => {
  const joined = joinSessions(workspace(), ['daemon-1', 'daemon-2', 'daemon-3'], 'term-b')
  const pane = stackForView(joined, 'daemon-3')
  assert.deepEqual(pane?.children.map(child => child.id), ['term-a', 'term-b', 'daemon-1', 'daemon-2', 'daemon-3'])
  assert.equal(pane?.active_child_id, 'term-b')
  assert.equal(paneStacks(joined).length, 2)
})

test('a session the operator is already looking at is shown by the pane that receives it', () => {
  // An unpanned focused session renders as a synthetic full-workspace pane, so restoring some
  // other tab here would hide what is on screen the moment the join lands.
  const joined = joinSessions(workspace(), ['daemon-1'], 'daemon-1')
  const pane = stackForView(joined, 'daemon-1')
  assert.equal(pane?.id, 'pane-agents')
  assert.equal(pane?.active_child_id, 'daemon-1')
})

test('a second pass over a persisted join changes nothing, so a reload cannot duplicate or re-float it', () => {
  const joined = joinSessions(workspace(), ['daemon-1'], 'term-b')
  const again = joinSessions(joined, ['term-a', 'term-b', 'daemon-1'], 'term-b')
  assert.equal(again, joined)
  assert.deepEqual(terminalIds(again), ['term-a', 'term-b', 'daemon-1'])
})

test('a layout with no pane at all gets exactly one, showing the session that made it', () => {
  const joined = joinSessions(parseLayout({ version: 7, root: null }), ['daemon-1'], null)
  assert.equal(paneStacks(joined).length, 1)
  assert.deepEqual(terminalIds(joined), ['daemon-1'])
  assert.equal(paneStacks(joined)[0].active_child_id, 'daemon-1')
})

test('an id the layout already holds under another leaf kind is never proposed again', () => {
  const layout = parseLayout({
    version: 7,
    root: { type: 'stack', id: 'pane', active_child_id: 'queue:abc', children: [
      { type: 'leaf', kind: 'queue', id: 'queue:abc' },
    ] },
  })
  assert.deepEqual(unjoinedSessionIds(layout, ['queue:abc']), [])
  assert.equal(joinSessions(layout, ['queue:abc'], null), layout)
})

test('a session whose join the server keeps refusing is retired instead of retried forever', () => {
  let attempts = {}
  for (let attempt = 0; attempt < MAX_JOIN_ATTEMPTS; attempt += 1) {
    assert.deepEqual(joinableSessionIds(['daemon-1', 'daemon-2'], attempts), ['daemon-1', 'daemon-2'])
    attempts = recordJoinFailure(attempts, ['daemon-1'])
  }
  assert.deepEqual(joinableSessionIds(['daemon-1', 'daemon-2'], attempts), ['daemon-2'])
})

test('the refusal record forgets sessions that left the fleet', () => {
  const attempts = recordJoinFailure({}, ['daemon-1', 'daemon-2'])
  assert.deepEqual(forgetJoinAttempts(attempts, new Set(['daemon-2'])), { 'daemon-2': 1 })
})

test('the refresh cycle reconciles layouts through the one planner that holds these rules', () => {
  // The join rules themselves are tested against `planFleetLayouts` in fleetLayouts.test.ts,
  // where they are behaviour rather than source text. This only holds the wiring: a second
  // hand-rolled reconciliation inside the composition root is how they drifted apart before.
  const app = readFileSync(join(SRC, 'App.tsx'), 'utf8')
  assert.match(app, /const plan = planFleetLayouts\(\{/)
  assert.match(app, /joinAttempts\.current = plan\.joinAttempts/)
  assert.ok(!/const joinCandidates = new Map/.test(app), 'App.tsx must not rebuild the join candidate grouping')
})

test('a join is persisted quietly, so a lost revision race reports nothing to the operator', () => {
  // The quiet write's own behaviour - reload without a message - is tested against
  // `createLayoutWriter` in layoutWriter.test.ts. This holds the caller: the refresh
  // pass's automatic joins are the writes that must not report a conflict.
  const app = readFileSync(join(SRC, 'App.tsx'), 'utf8')
  assert.match(app, /updateLayout\(join\.projectId,join\.layout,\{quiet:true\}\)/)
})
