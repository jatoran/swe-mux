import assert from 'node:assert/strict'
import test from 'node:test'
import {
  combineSessionSnapshots, fleetViewTotals, normalizeSnapshot, scopedSessionGroups,
  type FleetSnapshot, type ProcessItem, type SessionSnapshot,
} from '../src/processFleet.ts'

const process = (over: Partial<ProcessItem> & { pid: number }) => ({
  executable: 'node.exe', command: 'node.exe', cpu_pct: 1, memory_bytes: 1048576,
  listeners: [], connections: [], conditions: [], ...over,
}) as ProcessItem

const sessions = [
  { id: 's1', project_id: 'p1' },
  { id: 's2', project_id: 'p1' },
  { id: 's3', project_id: 'p2' },
]

const fleet = (): FleetSnapshot => ({
  available: true,
  sessions: [
    { session_id: 's1', project_id: 'p1', processes: [process({ pid: 10 }), process({ pid: 11 })] },
    { session_id: 's2', project_id: 'p1', processes: [process({ pid: 20 })] },
    { session_id: 's3', project_id: 'p2', processes: [process({ pid: 30, cpu_pct: 4 })] },
  ],
  daemon: { pid: 1, processes: 2, cpu_pct: 1.2, memory_bytes: 2097152, listeners: 1, connections: 3 },
  totals: { processes: 4, cpu_pct: 7, memory_bytes: 4194304, listeners: 0, connections: 0 },
})

test('a fleet payload keeps its own totals and never hands on a missing array', () => {
  const raw = {
    available: true,
    sessions: [{ session_id: 's1', project_id: 'p1', processes: [{ pid: 10, executable: 'a.exe', command: '', cpu_pct: 0, memory_bytes: 0 }] }],
    daemon: { pid: 1, processes: 1, cpu_pct: 0, memory_bytes: 0, members: [{ pid: 1, executable: 'd.exe', command: '', cpu_pct: 0, memory_bytes: 0 }] },
    totals: { processes: 9, cpu_pct: 9, memory_bytes: 9, listeners: 9, connections: 9 },
  } as unknown as FleetSnapshot
  const snapshot = normalizeSnapshot(raw, sessions)
  assert.deepEqual(snapshot.sessions[0].processes[0].listeners, [])
  assert.deepEqual(snapshot.sessions[0].processes[0].connections, [])
  assert.deepEqual(snapshot.sessions[0].processes[0].conditions, [])
  assert.deepEqual(snapshot.daemon?.members?.[0].listeners, [])
  // A daemon that reported totals is trusted over a recount, which would drop the runtime.
  assert.equal(snapshot.totals.processes, 9)
})

test('a session payload becomes a one-group fleet, and takes its Project from the live session', () => {
  const snapshot = normalizeSnapshot(
    { available: true, session_id: 's3', processes: [process({ pid: 30 })] } as SessionSnapshot,
    sessions,
  )
  assert.equal(snapshot.sessions.length, 1)
  assert.equal(snapshot.sessions[0].project_id, 'p2')
  assert.equal(snapshot.totals.processes, 1)
})

test('a session payload for a session the app has lost still renders, unattributed', () => {
  const snapshot = normalizeSnapshot(
    { available: true, session_id: 'gone', processes: [process({ pid: 1 })] } as SessionSnapshot,
    sessions,
  )
  assert.equal(snapshot.sessions[0].project_id, '')
})

test('the older-daemon fallback sums the per-session reads into one fleet', () => {
  const combined = combineSessionSnapshots([
    { available: true, session_id: 's1', processes: [process({ pid: 10 }), process({ pid: 11 })] },
    { available: true, session_id: 's3', processes: [process({ pid: 30 })] },
  ] as SessionSnapshot[], sessions)
  assert.deepEqual(combined.sessions.map(group => group.session_id), ['s1', 's3'])
  assert.equal(combined.totals.processes, 3)
  assert.equal(combined.available, true)
})

test('one unavailable session read makes the combined fleet unavailable, with its diagnostic', () => {
  const combined = combineSessionSnapshots([
    { available: true, session_id: 's1', processes: [] },
    { available: false, session_id: 's3', processes: [], diagnostic: 'psutil is not installed' },
  ] as SessionSnapshot[], sessions)
  assert.equal(combined.available, false)
  assert.equal(combined.diagnostic, 'psutil is not installed')
})

test('a Project scope reads the live session first, so a moved session follows immediately', () => {
  const snapshot = fleet()
  // The sample still files s3 under p2; the app already knows it moved to p1.
  const moved = [...sessions.slice(0, 2), { id: 's3', project_id: 'p1' }]
  const groups = scopedSessionGroups(snapshot, moved, 'p1', null)
  assert.deepEqual(groups.map(group => group.session_id), ['s1', 's2', 's3'])
})

test('scoping and drilling down compose, and a missing snapshot is no groups', () => {
  const snapshot = fleet()
  assert.deepEqual(scopedSessionGroups(snapshot, sessions, '', null).map(g => g.session_id), ['s1', 's2', 's3'])
  assert.deepEqual(scopedSessionGroups(snapshot, sessions, 'p2', null).map(g => g.session_id), ['s3'])
  assert.deepEqual(scopedSessionGroups(snapshot, sessions, 'p1', 's2').map(g => g.session_id), ['s2'])
  // A drill-down outside the scope is empty rather than silently widening it.
  assert.deepEqual(scopedSessionGroups(snapshot, sessions, 'p1', 's3'), [])
  assert.deepEqual(scopedSessionGroups(null, sessions, '', null), [])
})

test('unscoped totals are the daemon figures plus the runtime, so they match the sidebar', () => {
  const snapshot = fleet()
  const totals = fleetViewTotals(snapshot, scopedSessionGroups(snapshot, sessions, '', null), false)
  assert.equal(totals.sessions, 3)
  assert.equal(totals.processes, 6)
  assert.equal(totals.cpu_pct, 8.2)
  assert.equal(totals.memory_bytes, 6291456)
  assert.equal(totals.listeners, 1)
  assert.equal(totals.connections, 3)
})

test('scoped totals are recomputed from the rows on screen, runtime excluded', () => {
  const snapshot = fleet()
  const totals = fleetViewTotals(snapshot, scopedSessionGroups(snapshot, sessions, 'p1', null), true)
  assert.equal(totals.sessions, 2)
  assert.equal(totals.processes, 3)
  assert.equal(totals.cpu_pct, 3)
  assert.equal(totals.memory_bytes, 3145728)
})

test('ended processes are excluded from a scoped total, and an all-ended session from its count', () => {
  const snapshot: FleetSnapshot = {
    ...fleet(),
    sessions: [
      { session_id: 's1', project_id: 'p1', processes: [process({ pid: 10 }), process({ pid: 11, exited_at: 5, cpu_pct: 99 })] },
      { session_id: 's2', project_id: 'p1', processes: [process({ pid: 20, exited_at: 5 })] },
    ],
  }
  const totals = fleetViewTotals(snapshot, scopedSessionGroups(snapshot, sessions, 'p1', null), true)
  assert.equal(totals.sessions, 1)
  assert.equal(totals.processes, 1)
  assert.equal(totals.cpu_pct, 1)
})

test('no snapshot is a zeroed line rather than a throw', () => {
  assert.deepEqual(fleetViewTotals(null, [], false), {
    sessions: 0, processes: 0, cpu_pct: 0, memory_bytes: 0, listeners: 0, connections: 0,
  })
})
