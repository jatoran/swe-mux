import assert from 'node:assert/strict'
import test from 'node:test'
import { buildWatchRows, watchTotals, type WatchSnapshot } from '../src/processWatch.ts'

const projects = [
  { id: 'p1', name: 'Alpha', position: 0 },
  { id: 'p2', name: 'Beta', position: 1 },
]
const sessions = [
  { id: 's1', name: 'claude', project_id: 'p1', state: 'running', created_at: 10 },
  { id: 's2', name: 'shell', project_id: 'p1', state: 'running', created_at: 20 },
  { id: 's3', name: 'codex', project_id: 'p2', state: 'idle', created_at: 5 },
]
const process = (over: Record<string, unknown> = {}) =>
  ({ pid: 1, cpu_pct: 1, memory_bytes: 1048576, listeners: [], ...over })

const snapshot: WatchSnapshot = {
  sessions: [
    { session_id: 's3', project_id: 'p2', processes: [process({ pid: 30 })] },
    { session_id: 's1', project_id: 'p1', processes: [process({ pid: 10, cpu_pct: 2.5, memory_bytes: 2097152 }), process({ pid: 11 })] },
    { session_id: 's2', project_id: 'p1', processes: [process({ pid: 20 })] },
  ],
}

test('rows roll a session up and follow Project order then session age', () => {
  const rows = buildWatchRows(snapshot, sessions, projects, '', null)
  assert.deepEqual(rows.map(row => row.sessionId), ['s1', 's2', 's3'])
  assert.equal(rows[0].processes, 2)
  assert.equal(rows[0].cpu_pct, 3.5)
  assert.equal(rows[0].memory_bytes, 3145728)
  assert.equal(rows[0].projectLabel, 'Alpha')
})

test('the focused session sorts first, whatever Project it is in', () => {
  const rows = buildWatchRows(snapshot, sessions, projects, '', 's3')
  assert.deepEqual(rows.map(row => row.sessionId), ['s3', 's1', 's2'])
  assert.equal(rows[0].focused, true)
  assert.equal(rows[1].focused, false)
})

test('scoping to a Project drops the others', () => {
  const rows = buildWatchRows(snapshot, sessions, projects, 'p1', null)
  assert.deepEqual(rows.map(row => row.sessionId), ['s1', 's2'])
})

test('ended processes do not count, and a session with only ended ones drops out', () => {
  const withEnded: WatchSnapshot = {
    sessions: [
      { session_id: 's1', project_id: 'p1', processes: [process({ pid: 10 }), process({ pid: 11, exited_at: 5, cpu_pct: 99 })] },
      { session_id: 's2', project_id: 'p1', processes: [process({ pid: 20, exited_at: 5 })] },
    ],
  }
  const rows = buildWatchRows(withEnded, sessions, projects, '', null)
  assert.deepEqual(rows.map(row => row.sessionId), ['s1'])
  assert.equal(rows[0].processes, 1)
  assert.equal(rows[0].cpu_pct, 1)
})

test('only loopback listeners become server rows, deduped by port', () => {
  const serving: WatchSnapshot = {
    sessions: [{
      session_id: 's1',
      project_id: 'p1',
      processes: [process({
        pid: 10,
        listeners: [
          { host: '::1', port: 3000, loopback: true, url: 'http://[::1]:3000/' },
          { host: '127.0.0.1', port: 3000, loopback: true, url: 'http://127.0.0.1:3000/' },
          { host: '10.0.0.4', port: 8080, loopback: false, url: 'http://10.0.0.4:8080/' },
        ],
      })],
    }],
  }
  const rows = buildWatchRows(serving, sessions, projects, '', null)
  assert.deepEqual(rows[0].servers.map(server => server.port), [3000])
  assert.equal(rows[0].servers[0].host, '127.0.0.1')
})

test('a session the app no longer knows still gets a row, labelled by its id', () => {
  const orphan: WatchSnapshot = { sessions: [{ session_id: 'gone', project_id: 'p1', processes: [process()] }] }
  const rows = buildWatchRows(orphan, sessions, projects, '', null)
  assert.equal(rows[0].label, 'gone')
  assert.equal(rows[0].projectLabel, 'Alpha')
  assert.equal(rows[0].state, 'running')
})

test('a missing snapshot is no rows rather than a throw', () => {
  assert.deepEqual(buildWatchRows(null, sessions, projects, '', null), [])
  assert.deepEqual(buildWatchRows({}, sessions, projects, '', null), [])
})

test('totals describe the rows actually drawn, not the whole fleet', () => {
  const scoped = buildWatchRows(snapshot, sessions, projects, 'p1', null)
  assert.deepEqual(watchTotals(scoped), {
    sessions: 2,
    processes: 3,
    cpu_pct: 4.5,
    memory_bytes: 4194304,
    servers: 0,
  })
  assert.deepEqual(watchTotals([]), { sessions: 0, processes: 0, cpu_pct: 0, memory_bytes: 0, servers: 0 })
})
