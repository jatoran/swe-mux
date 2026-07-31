import assert from 'node:assert/strict'
import test from 'node:test'
import type { Session } from '../src/types.ts'
import { reconcileSeen, isUnread, projectRailStatus, projectSetRailStatus, type SeenMap } from '../src/sessionAttention.ts'

const session = (
  id: string,
  backend: Session['backend'],
  last_activity_ts: number,
  extra: Partial<Session> = {},
) => ({ id, name: id, project_id: 'p1', backend, state: 'working', last_activity_ts, ...extra }) as unknown as Session

test('newly-sighted agents start read at their current activity', () => {
  const sessions = [session('a', 'claude', 100), session('b', 'codex', 200)]
  const seen = reconcileSeen({}, sessions, [])
  assert.deepEqual(seen, { a: 100, b: 200 })
  assert.equal(isUnread(sessions[0], seen), false)
  assert.equal(isUnread(sessions[1], seen), false)
})

test('off-screen activity after first sighting marks the row unread', () => {
  const seen = reconcileSeen({}, [session('a', 'claude', 100)], [])
  const later = session('a', 'claude', 150)
  // 'a' is not visible, so its mark is retained and the newer output is unread.
  const next = reconcileSeen(seen, [later], [])
  assert.equal(next.a, 100)
  assert.equal(isUnread(later, next), true)
})

test('a visible session is kept read even as its agent keeps working', () => {
  let seen: SeenMap = reconcileSeen({}, [session('a', 'claude', 100)], ['a'])
  const busy = session('a', 'claude', 175)
  seen = reconcileSeen(seen, [busy], ['a'])
  assert.equal(seen.a, 175)
  assert.equal(isUnread(busy, seen), false)
})

test('viewing an unread session clears it', () => {
  const seen = reconcileSeen({ a: 100 }, [session('a', 'claude', 150)], ['a'])
  assert.equal(seen.a, 150)
  assert.equal(isUnread(session('a', 'claude', 150), seen), false)
})

test('shell and ended agent sessions never participate in unread', () => {
  const shell = session('s', 'shell', 100)
  const dead = session('d', 'claude', 100, { state: 'exited' })
  const seen = reconcileSeen({}, [shell, dead], [])
  assert.deepEqual(seen, {})
  assert.equal(isUnread(shell, { s: 50 }), false)
  assert.equal(isUnread(dead, { d: 50 }), false)
})

test('reconcileSeen returns the same reference when nothing changed', () => {
  const prev: SeenMap = { a: 100 }
  const sessions = [session('a', 'claude', 100)]
  assert.equal(reconcileSeen(prev, sessions, []), prev)
})

test('reconcileSeen drops sessions that no longer exist', () => {
  const next = reconcileSeen({ a: 100, gone: 50 }, [session('a', 'claude', 100)], [])
  assert.deepEqual(next, { a: 100 })
})

test('project rail activity keeps unread independent from the strongest live state', () => {
  const sessions = [
    session('working', 'claude', 150, { project_id: 'p1', state: 'working' }),
    session('ready', 'codex', 100, { project_id: 'p1', state: 'idle' }),
    session('approval', 'claude', 100, { project_id: 'p1', state: 'awaiting' }),
    session('other', 'codex', 200, { project_id: 'p2', state: 'working' }),
  ]
  assert.deepEqual(projectRailStatus(sessions, 'p1', { working: 100, ready: 100, approval: 100 }), {
    activity: 'attention', unread: true, liveCount: 3, agentCount: 3,
  })
})

test('a collapsed section answers for every project it folded away', () => {
  const sessions = [
    session('calm', 'claude', 100, { project_id: 'p1', state: 'idle' }),
    session('waiting', 'claude', 150, { project_id: 'p2', state: 'awaiting' }),
    session('elsewhere', 'claude', 100, { project_id: 'p3', state: 'working' }),
  ]
  const seen: SeenMap = { calm: 100, waiting: 100, elsewhere: 100 }
  // The approval lives in p2, so folding p1 and p2 together must still surface it —
  // a section that reported only its first Project would hide the one thing that
  // needs the user.
  assert.deepEqual(projectSetRailStatus(sessions, ['p1', 'p2'], seen), {
    activity: 'attention', unread: true, liveCount: 2, agentCount: 2,
  })
  // Projects outside the section never leak into its aggregate.
  assert.equal(projectSetRailStatus(sessions, ['p1'], seen).activity, 'waiting')
  assert.equal(projectSetRailStatus(sessions, [], seen).activity, 'inactive')
  // A Set is accepted as-is, so the caller need not rebuild one per render.
  assert.equal(projectSetRailStatus(sessions, new Set(['p3']), seen).activity, 'working')
})

test('project rail activity distinguishes working, ready, shell-only, and empty projects', () => {
  assert.equal(projectRailStatus([session('a', 'claude', 1)], 'p1', { a: 1 }).activity, 'working')
  assert.equal(projectRailStatus([session('a', 'claude', 1, { state: 'idle' })], 'p1', { a: 1 }).activity, 'waiting')
  assert.equal(projectRailStatus([session('s', 'shell', 1, { state: 'running' })], 'p1', {}).activity, 'running')
  assert.deepEqual(projectRailStatus([session('d', 'claude', 1, { state: 'exited' })], 'p1', { d: 0 }), {
    activity: 'inactive', unread: false, liveCount: 0, agentCount: 0,
  })
})
