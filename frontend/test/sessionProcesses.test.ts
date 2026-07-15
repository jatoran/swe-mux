import assert from 'node:assert/strict'
import test from 'node:test'
import { detectedServers } from '../src/sessionProcesses.ts'

const listener = (port: number, host = '127.0.0.1', loopback = true) =>
  ({ host, port, loopback, url: `http://${host}:${port}/` })

test('an agent process tree without listeners yields no rows', () => {
  // The exact bloat this exists to exclude: cmd / python / claude.exe.
  const rows = detectedServers([
    { pid: 10, listeners: [] },
    { pid: 11, listeners: [] },
    { pid: 12 },
  ])
  assert.deepEqual(rows, [])
})

test('only processes that actually listen are reported', () => {
  const rows = detectedServers([
    { pid: 10, listeners: [] },
    { pid: 11, listeners: [listener(5173)] },
    { pid: 12, listeners: [] },
  ])
  assert.deepEqual(rows.map(row => row.port), [5173])
  assert.equal(rows[0].pid, 11)
  assert.equal(rows[0].url, 'http://127.0.0.1:5173/')
})

test('an exited server is not reported despite the daemon retaining it', () => {
  const rows = detectedServers([
    { pid: 11, exited_at: 123, listeners: [listener(5173)] },
    { pid: 12, listeners: [listener(8080)] },
  ])
  assert.deepEqual(rows.map(row => row.port), [8080])
})

test('non-loopback listeners are excluded because a preview cannot bridge them', () => {
  const rows = detectedServers([
    { pid: 11, listeners: [listener(5173, '0.0.0.0', false)] },
    { pid: 12, listeners: [listener(8080)] },
  ])
  assert.deepEqual(rows.map(row => row.port), [8080])
})

test('a port bound on both stacks collapses to one row, preferring IPv4 loopback', () => {
  // A wildcard-bound dev server arrives here as both 127.0.0.1 and ::1.
  const ipv6First = detectedServers([
    { pid: 11, listeners: [listener(5173, '::1'), listener(5173)] },
  ])
  assert.deepEqual(ipv6First.map(row => row.port), [5173])
  assert.equal(ipv6First[0].host, '127.0.0.1')

  const ipv4First = detectedServers([
    { pid: 11, listeners: [listener(5173), listener(5173, '::1')] },
  ])
  assert.deepEqual(ipv4First.map(row => row.host), ['127.0.0.1'])
})

test('an IPv6-only server still gets a row', () => {
  const rows = detectedServers([{ pid: 11, listeners: [listener(5173, '::1')] }])
  assert.deepEqual(rows.map(row => row.host), ['::1'])
})

test('rows are ordered by port so they do not move between samples', () => {
  const rows = detectedServers([
    { pid: 11, listeners: [listener(8080)] },
    { pid: 12, listeners: [listener(3000), listener(5173)] },
  ])
  assert.deepEqual(rows.map(row => row.port), [3000, 5173, 8080])
})
