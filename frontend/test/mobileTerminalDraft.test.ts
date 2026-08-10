import assert from 'node:assert/strict'
import test from 'node:test'
import {
  MOBILE_TERMINAL_DRAFT_MAX_CHARS,
  MOBILE_TERMINAL_DRAFT_MAX_ENTRIES,
  MOBILE_TERMINAL_DRAFT_RETENTION_MS,
  MOBILE_TERMINAL_DRAFT_STORAGE_KEY,
  MobileTerminalDraftStore,
  parseMobileTerminalDrafts,
} from '../src/mobileTerminalDraft.ts'

class MemoryStorage {
  values = new Map<string, string>()
  getItem(key: string) { return this.values.get(key) ?? null }
  setItem(key: string, value: string) { this.values.set(key, value) }
}

test('drafts persist independently by session and clear explicitly', () => {
  const storage = new MemoryStorage()
  let now = 1_000
  const store = new MobileTerminalDraftStore(() => storage, () => now)
  store.set('alpha', 'first draft')
  now += 1
  store.set('beta', 'second draft')
  assert.equal(store.get('alpha'), 'first draft')
  assert.equal(store.get('beta'), 'second draft')
  assert.equal(new MobileTerminalDraftStore(() => storage, () => now).get('alpha'), 'first draft')
  store.set('alpha', '')
  assert.equal(store.has('alpha'), false)
  assert.equal(store.get('beta'), 'second draft')
})

test('draft parsing drops expired and malformed entries without consulting session liveness', () => {
  const now = MOBILE_TERMINAL_DRAFT_RETENTION_MS + 10_000
  const raw = JSON.stringify({ version: 1, drafts: {
    current: { text: 'keep', updatedAt: now - 1 },
    expired: { text: 'drop', updatedAt: 1 },
    blank: { text: '', updatedAt: now },
    malformed: { text: 42, updatedAt: now },
  } })
  assert.deepEqual(parseMobileTerminalDrafts(raw, now), {
    current: { text: 'keep', updatedAt: now - 1 },
  })
  assert.deepEqual(parseMobileTerminalDrafts('{broken', now), {})
})

test('draft text is bounded before it reaches device storage', () => {
  const storage = new MemoryStorage()
  const store = new MobileTerminalDraftStore(() => storage, () => 5_000)
  const kept = store.set('alpha', 'x'.repeat(MOBILE_TERMINAL_DRAFT_MAX_CHARS + 50))
  assert.equal(kept.length, MOBILE_TERMINAL_DRAFT_MAX_CHARS)
  assert.equal(JSON.parse(storage.getItem(MOBILE_TERMINAL_DRAFT_STORAGE_KEY)!).drafts.alpha.text.length, MOBILE_TERMINAL_DRAFT_MAX_CHARS)
})

test('the registry retains only the newest bounded set of sessions', () => {
  const now = 100_000
  const drafts = Object.fromEntries(Array.from({ length: MOBILE_TERMINAL_DRAFT_MAX_ENTRIES + 5 }, (_, index) => [
    `session-${index}`,
    { text: `draft-${index}`, updatedAt: now - index },
  ]))
  const parsed = parseMobileTerminalDrafts(JSON.stringify({ version: 1, drafts }), now)
  assert.equal(Object.keys(parsed).length, MOBILE_TERMINAL_DRAFT_MAX_ENTRIES)
  assert.equal(parsed['session-0']?.text, 'draft-0')
  assert.equal(parsed[`session-${MOBILE_TERMINAL_DRAFT_MAX_ENTRIES + 4}`], undefined)
})

test('storage refusal retains drafts for the current browser process', () => {
  const store = new MobileTerminalDraftStore(() => ({
    getItem: () => { throw new Error('blocked') },
    setItem: () => { throw new Error('blocked') },
  }), () => 5_000)
  store.set('alpha', 'memory only')
  assert.equal(store.get('alpha'), 'memory only')
})
