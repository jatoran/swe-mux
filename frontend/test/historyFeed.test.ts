import assert from 'node:assert/strict'
import test from 'node:test'
import { groupHistoryFeed, type HistoryFeedEntry } from '../src/historyFeed.ts'

const entry = (id: string, project?: string): HistoryFeedEntry & { id: string } =>
  ({ id, ...(project ? { project_id: project } : {}) })

const registered = [
  { id: 'alpha', name: 'Alpha' },
  { id: 'zulu', name: 'Zulu' },
]

test('headings follow the feed, so changing the sort moves the top of the list', () => {
  // The other half of "changing the sort does nothing": the rows under each heading did
  // reorder, but headings sorted by name kept the alphabetically-first Project pinned to
  // the top of a listing that is meant to be in activity order.
  const feed = groupHistoryFeed(
    [entry('z1', 'zulu'), entry('a1', 'alpha'), entry('z2', 'zulu')],
    [],
    registered,
  )
  assert.deepEqual(feed.map(([id]) => id), ['zulu', 'alpha'])
  // ...and the conversations inside a heading stay in the order they arrived in.
  assert.deepEqual(
    feed[0][1].entries.map(item => (item as { id: string }).id),
    ['z1', 'z2'],
  )

  // The same page in the other order puts the other heading first, with no other change.
  const reversed = groupHistoryFeed(
    [entry('a1', 'alpha'), entry('z1', 'zulu'), entry('z2', 'zulu')],
    [],
    registered,
  )
  assert.deepEqual(reversed.map(([id]) => id), ['alpha', 'zulu'])
})

test('the unassigned bucket sorts last however recent it is', () => {
  // A catch-all rather than a Project: conversations land there because mux could not
  // attribute them, not because that is where the work is.
  const feed = groupHistoryFeed([entry('u1'), entry('a1', 'alpha')], [], registered)
  assert.deepEqual(feed.map(([id]) => id), ['alpha', null])
  assert.equal(feed[1][1].label, 'Unassigned')
})

test('a heading is named by the history index first, then the registry, then the row', () => {
  const feed = groupHistoryFeed(
    [entry('a1', 'alpha'), entry('g1', 'ghost')],
    [{ project_id: 'alpha', label: 'Alpha (indexed)' }],
    registered,
  )
  assert.equal(feed[0][1].label, 'Alpha (indexed)')
  // A Project neither source knows still gets a heading rather than being dropped.
  assert.equal(feed[1][1].label, 'Unassigned')
})

test('a removed Project says so in its heading', () => {
  const feed = groupHistoryFeed(
    [entry('a1', 'alpha')],
    [{ project_id: 'alpha', label: 'Alpha', removed_at: 1 }],
    registered,
  )
  assert.equal(feed[0][1].label, 'Alpha (removed)')
})

test('an empty page groups into nothing rather than an empty heading', () => {
  assert.deepEqual(groupHistoryFeed([], [], registered), [])
})
