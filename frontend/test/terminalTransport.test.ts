import assert from 'node:assert/strict'
import test from 'node:test'
import {
  CLOSE_GATEWAY_RESTARTING,
  CLOSE_USE_DAEMON,
  CLOSE_USE_GATEWAY,
  GATEWAY_FAILURE_BASE_MS,
  GATEWAY_RESTART_HOLD_MS,
  MAX_IMMEDIATE_REDIRECTS,
  REPORT_MAX_AGE_MS,
  SESSION_REFUSAL_MS,
  TRANSPORT_STORAGE_KEY,
  TerminalTransportState,
  daemonPtyUrl,
  gatewayPtyUrl,
  parseTerminalTransport,
  type StorageLike,
} from '../src/terminalTransport.ts'

const LOOPBACK = { href: 'http://127.0.0.1:8765/', protocol: 'http:' }
const TAILNET = { href: 'http://100.64.152.41:8765/?x=1#y', protocol: 'http:' }
const SERVE = { href: 'https://desk.tail1.ts.net/', protocol: 'https:' }
const IPV6 = { href: 'http://[::1]:8765/', protocol: 'http:' }
const GATEWAY = { transport: 'gateway', state: 'running', port: 8766 }
const DAEMON = { transport: 'daemon', state: 'starting', port: null }

class Clock {
  at = 1_000_000
  now = () => this.at
  advance(ms: number) { this.at += ms }
}

class MemoryStorage implements StorageLike {
  items = new Map<string, string>()
  getItem(key: string) { return this.items.get(key) ?? null }
  setItem(key: string, value: string) { this.items.set(key, value) }
}

const state = (clock = new Clock(), storage: StorageLike | null = null, proxied = false) =>
  new TerminalTransportState({ now: clock.now, storage, proxied })

test('the daemon is same-origin /pty on every access path', () => {
  assert.equal(daemonPtyUrl('s 1', LOOPBACK), 'ws://127.0.0.1:8765/pty/s%201')
  assert.equal(daemonPtyUrl('s1', TAILNET), 'ws://100.64.152.41:8765/pty/s1')
  assert.equal(daemonPtyUrl('s1', SERVE), 'wss://desk.tail1.ts.net/pty/s1')
})

test('an http page reaches the gateway on its own port, an https one through the proxy', () => {
  assert.equal(gatewayPtyUrl('s1', LOOPBACK, 8766), 'ws://127.0.0.1:8766/pty/s1')
  assert.equal(gatewayPtyUrl('s1', TAILNET, 8766), 'ws://100.64.152.41:8766/pty/s1')
  assert.equal(gatewayPtyUrl('s1', IPV6, 8766), 'ws://[::1]:8766/pty/s1')
  // An https page cannot open ws: to another port, and the proxy keeps /pty the daemon's.
  assert.equal(gatewayPtyUrl('s1', SERVE, 8766), 'wss://desk.tail1.ts.net/gateway/pty/s1')
  // The Vite dev server proxies /gateway the same way over plain HTTP.
  assert.equal(
    gatewayPtyUrl('s1', { href: 'http://127.0.0.1:5173/', protocol: 'http:' }, 8766, { proxied: true }),
    'ws://127.0.0.1:5173/gateway/pty/s1',
  )
})

test('only a well-formed report is believed, and a gateway needs a port', () => {
  assert.deepEqual(parseTerminalTransport(GATEWAY), GATEWAY)
  assert.deepEqual(parseTerminalTransport({ ...DAEMON, reason: 'no PTY supervisor' }), {
    ...DAEMON, reason: 'no PTY supervisor',
  })
  assert.equal(parseTerminalTransport({ transport: 'gateway', state: 'running', port: null }), null)
  assert.equal(parseTerminalTransport({ transport: 'gateway', port: 70000 }), null)
  assert.equal(parseTerminalTransport({ transport: 'carrier-pigeon' }), null)
  assert.equal(parseTerminalTransport(undefined), null)
  assert.equal(parseTerminalTransport('gateway'), null)
})

test('with no report, or a daemon report, terminals open on the daemon', () => {
  const transport = state()
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon')
  transport.note({ transport: 'nonsense' })
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon')
  transport.note(DAEMON)
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon')
  transport.note(GATEWAY)
  assert.deepEqual(transport.route('s1', LOOPBACK), { via: 'gateway', url: 'ws://127.0.0.1:8766/pty/s1' })
})

test('a session the gateway does not hold goes to the daemon, and only that session', () => {
  const clock = new Clock()
  const transport = state(clock)
  transport.note(GATEWAY)
  assert.deepEqual(transport.closed('cold', 'gateway', CLOSE_USE_DAEMON, true), { immediate: true })
  assert.equal(transport.route('cold', LOOPBACK).via, 'daemon')
  assert.equal(transport.route('live', LOOPBACK).via, 'gateway')
  clock.advance(SESSION_REFUSAL_MS + 1)
  assert.equal(transport.route('cold', LOOPBACK).via, 'gateway', 'the refusal lapses')
})

test('a restarting gateway sends terminals to the daemon briefly, then back', () => {
  const clock = new Clock()
  const transport = state(clock)
  transport.note(GATEWAY)
  assert.deepEqual(transport.closed('s1', 'gateway', CLOSE_GATEWAY_RESTARTING, true), { immediate: true })
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon')
  assert.equal(transport.route('s2', LOOPBACK).via, 'daemon', 'the whole gateway is restarting')
  clock.advance(GATEWAY_RESTART_HOLD_MS + 1)
  assert.equal(transport.route('s1', LOOPBACK).via, 'gateway')
})

test('an unreachable gateway is backed off from, doubling, and reset by one that answers', () => {
  const clock = new Clock()
  const transport = state(clock)
  transport.note(GATEWAY)
  // A handshake that never opened (refused, firewalled, stalled) is not a refusal.
  assert.deepEqual(transport.closed('s1', 'gateway', 1006, false), { immediate: true })
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon')
  clock.advance(GATEWAY_FAILURE_BASE_MS + 1)
  assert.equal(transport.route('s1', LOOPBACK).via, 'gateway')
  transport.closed('s1', 'gateway', 0, false)
  clock.advance(GATEWAY_FAILURE_BASE_MS + 1)
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon', 'the second hold is twice as long')
  clock.advance(GATEWAY_FAILURE_BASE_MS)
  assert.equal(transport.route('s1', LOOPBACK).via, 'gateway')
  transport.opened('s1', 'gateway')
  transport.closed('s1', 'gateway', 1006, false)
  clock.advance(GATEWAY_FAILURE_BASE_MS + 1)
  assert.equal(transport.route('s1', LOOPBACK).via, 'gateway', 'an answer reset the doubling')
})

test('an ordinary drop after opening keeps the transport and takes the ordinary backoff', () => {
  const transport = state()
  transport.note(GATEWAY)
  assert.deepEqual(transport.closed('s1', 'gateway', 1006, true), { immediate: false })
  assert.equal(transport.route('s1', LOOPBACK).via, 'gateway')
  assert.deepEqual(transport.closed('s1', 'daemon', 1006, true), { immediate: false })
})

test("the daemon's 4012 outranks a stale report, and clears a refusal", () => {
  const clock = new Clock()
  const transport = state(clock)
  transport.note(GATEWAY)
  transport.closed('s1', 'gateway', CLOSE_USE_DAEMON, true)
  transport.note(DAEMON) // a report from just before the gateway came back
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon')
  assert.deepEqual(transport.closed('s1', 'daemon', CLOSE_USE_GATEWAY, true), { immediate: true })
  assert.deepEqual(transport.route('s1', LOOPBACK), { via: 'gateway', url: 'ws://127.0.0.1:8766/pty/s1' })
  assert.equal(transport.needsReport('s1'), false)
})

test('a 4012 before any report named a port asks for one', () => {
  const transport = state()
  transport.closed('s1', 'daemon', CLOSE_USE_GATEWAY, true)
  assert.equal(transport.needsReport('s1'), true)
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon', 'nowhere else to go yet')
  transport.note(GATEWAY)
  assert.equal(transport.needsReport('s1'), false)
  assert.equal(transport.route('s1', LOOPBACK).via, 'gateway')
})

test('two processes disagreeing about a session cannot bounce it in a busy loop', () => {
  const transport = state()
  transport.note(GATEWAY)
  const immediates: boolean[] = []
  for (let round = 0; round < 4; round += 1) {
    immediates.push(transport.closed('s1', 'gateway', CLOSE_USE_DAEMON, true).immediate)
    immediates.push(transport.closed('s1', 'daemon', CLOSE_USE_GATEWAY, true).immediate)
  }
  assert.deepEqual(immediates.slice(0, MAX_IMMEDIATE_REDIRECTS), Array(MAX_IMMEDIATE_REDIRECTS).fill(true))
  assert.ok(immediates.slice(MAX_IMMEDIATE_REDIRECTS).every(value => !value), 'then the backoff applies')
})

test('a gateway newly up is tried at once, whatever the last one did', () => {
  const transport = state()
  transport.note(GATEWAY)
  transport.closed('s1', 'gateway', 0, false)
  assert.equal(transport.route('s1', LOOPBACK).via, 'daemon')
  transport.note(DAEMON)
  transport.note(GATEWAY)
  assert.equal(transport.route('s1', LOOPBACK).via, 'gateway')
})

test('the last report survives a reload, but not a stale one', () => {
  const clock = new Clock()
  const storage = new MemoryStorage()
  state(clock, storage).note(GATEWAY)
  assert.ok(storage.getItem(TRANSPORT_STORAGE_KEY))
  // A page reloaded while the daemon is down still finds the gateway.
  assert.equal(state(clock, storage).route('s1', LOOPBACK).via, 'gateway')
  clock.advance(REPORT_MAX_AGE_MS + 1)
  assert.equal(state(clock, storage).route('s1', LOOPBACK).via, 'daemon')
  storage.setItem(TRANSPORT_STORAGE_KEY, '{not json')
  assert.equal(state(clock, storage).route('s1', LOOPBACK).via, 'daemon')
})

test('in development the gateway is reached through the dev server', () => {
  const transport = state(new Clock(), null, true)
  transport.note(GATEWAY)
  assert.equal(
    transport.route('s1', { href: 'http://127.0.0.1:5173/', protocol: 'http:' }).url,
    'ws://127.0.0.1:5173/gateway/pty/s1',
  )
})
