import assert from 'node:assert/strict'
import test from 'node:test'
import type { PortConflict, PortConflictListener } from '../src/processFleet.ts'
import { portConflictNotice } from '../src/previewPortConflict.ts'

const listener = (pid: number, overrides: Partial<PortConflictListener> = {}): PortConflictListener => ({
  pid, executable: 'python.exe', command: `python.exe -m server ${pid}`, started_at: 1,
  session_id: null, project_id: null, evidence_state: null, owner: false, ...overrides,
})

const conflict = (overrides: Partial<PortConflict> = {}): PortConflict => ({
  host: '127.0.0.1', port: 8766,
  listeners: [listener(25604, { owner: true, session_id: 'task' }), listener(136796, { evidence_state: 'suspected_orphan', session_id: 'ended' })],
  answering_pid: 136796, answering_is_owner: false, ...overrides,
})

test('no conflict, or a single listener, draws nothing', () => {
  assert.equal(portConflictNotice(null), null)
  assert.equal(portConflictNotice(undefined), null)
  assert.equal(portConflictNotice(conflict({ listeners: [listener(1, { owner: true })] })), null)
})

test('a foreign process answering is named as the page being shown', () => {
  // The 2026-09-26 incident: the task's server never received a request.
  const notice = portConflictNotice(conflict())
  assert.ok(notice)
  assert.equal(notice.severity, 'wrong_server')
  assert.match(notice.headline, /showing PID 136796/)
  assert.match(notice.headline, /not this session's server/)
  const orphan = notice.listeners.find(item => item.pid === 136796)
  assert.ok(orphan)
  assert.equal(orphan.answering, true)
  assert.match(orphan.detail, /session that has ended/)
  assert.equal(notice.listeners.find(item => item.pid === 25604)?.detail, "this preview's session")
})

test('the owner answering is still reported, because the OS can change its mind', () => {
  const notice = portConflictNotice(conflict({ answering_pid: 25604, answering_is_owner: true }))
  assert.equal(notice?.severity, 'shared')
  assert.match(notice?.headline || '', /also held by 1 other process\./)
})

test('an unknown answerer is said to be unknown rather than guessed', () => {
  const notice = portConflictNotice(conflict({ answering_pid: null, answering_is_owner: null }))
  assert.equal(notice?.severity, 'unknown')
  assert.match(notice?.headline || '', /could not tell/)
  assert.ok(notice?.listeners.every(item => !item.answering))
})

test('a process no live session started is described as such', () => {
  const notice = portConflictNotice(conflict({ listeners: [listener(1, { owner: true }), listener(2)], answering_pid: 2 }))
  assert.equal(notice?.listeners[1].detail, 'not started by any live swe-mux session')
})

test('a long command line is shortened for the row, not dropped', () => {
  const long = `python.exe ${'x'.repeat(300)}`
  const notice = portConflictNotice(conflict({ listeners: [listener(1, { owner: true, command: long }), listener(2)] }))
  assert.equal(notice?.listeners[0].label.length, 120)
  assert.ok(notice?.listeners[0].label.startsWith('python.exe x'))
})
