import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SESSION_FOCUS_HISTORY_LIMIT, forgetFocusedSession, recentFocusedSessions, recordFocusedSession,
  type SessionFocusHistory,
} from '../src/sessionFocusHistory.ts'

test('the newest focus is at the head and appears exactly once', () => {
  let history: SessionFocusHistory = {}
  history = recordFocusedSession(history, 'p1', 's1')
  history = recordFocusedSession(history, 'p1', 's2')
  history = recordFocusedSession(history, 'p1', 's3')
  assert.deepEqual(recentFocusedSessions(history, 'p1'), ['s3', 's2', 's1'])
  // Returning to an older session moves it rather than duplicating it, so the stack
  // stays a set of distinct sessions in recency order.
  history = recordFocusedSession(history, 'p1', 's1')
  assert.deepEqual(recentFocusedSessions(history, 'p1'), ['s1', 's3', 's2'])
})

test('re-recording the session already at the head changes nothing', () => {
  // The recording effect fires on any sessions/layout change, not only on a move, so
  // an unchanged head has to be a no-op by identity - otherwise every fleet poll
  // allocates a new stack.
  const history = recordFocusedSession(recordFocusedSession({}, 'p1', 's1'), 'p1', 's2')
  assert.equal(recordFocusedSession(history, 'p1', 's2'), history)
})

test('projects keep separate stacks', () => {
  let history: SessionFocusHistory = {}
  history = recordFocusedSession(history, 'p1', 's1')
  history = recordFocusedSession(history, 'p2', 'other')
  history = recordFocusedSession(history, 'p1', 's2')
  assert.deepEqual(recentFocusedSessions(history, 'p1'), ['s2', 's1'])
  assert.deepEqual(recentFocusedSessions(history, 'p2'), ['other'])
  assert.deepEqual(recentFocusedSessions(history, 'p3'), [])
})

test('the stack is bounded, dropping the oldest entry', () => {
  let history: SessionFocusHistory = {}
  const total = SESSION_FOCUS_HISTORY_LIMIT + 3
  for (let index = 0; index < total; index += 1) history = recordFocusedSession(history, 'p1', `s${index}`)
  const ids = recentFocusedSessions(history, 'p1')
  assert.equal(ids.length, SESSION_FOCUS_HISTORY_LIMIT)
  assert.equal(ids[0], `s${total - 1}`)
  assert.equal(ids.includes('s0'), false)
})

test('a killed session is forgotten everywhere, and an empty project is dropped', () => {
  let history: SessionFocusHistory = {}
  history = recordFocusedSession(history, 'p1', 's1')
  history = recordFocusedSession(history, 'p1', 's2')
  history = recordFocusedSession(history, 'p2', 's1')
  history = forgetFocusedSession(history, 's1')
  assert.deepEqual(recentFocusedSessions(history, 'p1'), ['s2'])
  assert.deepEqual(recentFocusedSessions(history, 'p2'), [])
  assert.equal('p2' in history, false)
  // Forgetting an id that was never recorded is identity, not a rebuilt copy.
  assert.equal(forgetFocusedSession(history, 'never'), history)
})

test('blank ids are refused rather than stored', () => {
  assert.deepEqual(recordFocusedSession({}, '', 's1'), {})
  assert.deepEqual(recordFocusedSession({}, 'p1', ''), {})
})
