import assert from 'node:assert/strict'
import test from 'node:test'

// assistant.ts reads localStorage for the remembered dialog id and dispatches a
// window event when it swaps one, so both are stubbed before it is imported.
// Installed only when absent: sibling test files share this process, and
// clobbering a store another file is already using would break it.
const globals = globalThis as unknown as Record<string, unknown>
if (!globals.localStorage) {
  const store = new Map<string, string>()
  globals.localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, value) },
    removeItem: (key: string) => { store.delete(key) },
    clear: () => store.clear(),
    key: () => null,
    length: 0,
  }
}

type DispatchedEvent = { type: string; detail?: unknown }

/**
 * A `window` only for as long as the dispatch is being observed.
 *
 * Installing one for the whole file is not harmless here: several modules read
 * `typeof window === 'undefined'` to decide whether they have a browser, and a
 * stub that answers "yes" without `setInterval` breaks their tests instead.
 */
const withWindow = async (body: (dispatched: DispatchedEvent[]) => Promise<void>) => {
  const dispatched: DispatchedEvent[] = []
  const previous = globals.window
  globals.window = {
    dispatchEvent: (event: DispatchedEvent) => { dispatched.push(event); return true },
    addEventListener: () => {},
    removeEventListener: () => {},
  }
  try { await body(dispatched) } finally { globals.window = previous }
}

const {
  ASSISTANT_DIALOG_RESET_EVENT, NEW_CONVERSATION_PHRASES, NEW_CONVERSATION_REPLY,
  rememberDialogId, startNewDialog, storedDialogId,
} = await import('../src/assistant.ts')
const { resolveVoiceIntent } = await import('../src/voiceIntents.ts')
const { sessionLaunchVoicePhrases } = await import('../src/voiceLaunch.ts')
const { registeredVoiceReference } = await import('../src/voiceCommandReference.ts')
type Command = import('../src/commands.ts').Command

/** Save/restore per call rather than in a suite hook: several test files in this
 *  process stub `fetch`, and a shared teardown would restore the wrong one. */
const withFetch = async (impl: typeof fetch, body: () => Promise<void>) => {
  const real = globalThis.fetch
  globalThis.fetch = impl
  try { await body() } finally { globalThis.fetch = real }
}

const createDialogFetch = (id: string, seen: string[]) => (async (input: string | URL | Request, init?: RequestInit) => {
  const url = String(input)
  seen.push(`${init?.method || 'GET'} ${url}`)
  if (url === '/api/assistant/dialogs') {
    return new Response(JSON.stringify({ id }), { status: 201, headers: { 'Content-Type': 'application/json' } })
  }
  return new Response(JSON.stringify({ id: url.split('/').pop() }), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

test('a new conversation abandons the remembered dialog rather than reusing it', async () => {
  // The bug this pins is the whole feature inverted: `ensureDialog` deliberately
  // resumes the stored dialog, so clearing context has to forget it *first* or
  // "new conversation" silently keeps every message it claimed to clear.
  rememberDialogId('old-dialog')
  const seen: string[] = []
  await withFetch(createDialogFetch('fresh-dialog', seen), async () => {
    const id = await startNewDialog()
    assert.equal(id, 'fresh-dialog')
  })
  assert.deepEqual(seen, ['POST /api/assistant/dialogs'], 'the stored dialog is never fetched')
  assert.equal(storedDialogId(), 'fresh-dialog', 'and the fresh one becomes this device’s dialog')
})

test('the swap is announced, because two surfaces start it and one holds the view', async () => {
  await withWindow(async dispatched => {
    await withFetch(createDialogFetch('announced-dialog', []), async () => { await startNewDialog() })
    const reset = dispatched.filter(event => event.type === ASSISTANT_DIALOG_RESET_EVENT)
    assert.equal(reset.length, 1)
    assert.deepEqual((reset[0] as { detail?: { dialog_id?: string } }).detail, { dialog_id: 'announced-dialog' })
  })
})

test('a headless host still gets its dialog, because the announcement is guarded', async () => {
  // `startNewDialog` is the only path either surface uses; throwing where there
  // is no DOM would make the panelless caller the broken one.
  assert.equal(typeof (globals.window), 'undefined', 'the stub must not have leaked out of the test above')
  await withFetch(createDialogFetch('headless-dialog', []), async () => {
    assert.equal(await startNewDialog(), 'headless-dialog')
  })
})

test('a failed create leaves the device without a dialog rather than a stale one', async () => {
  // Half-clearing is the dangerous outcome: a remembered id that survived a
  // failure would put the "cleared" context straight back on the next turn.
  rememberDialogId('old-dialog')
  const failing = (async () => new Response(JSON.stringify({ error: 'the assistant is disabled' }), { status: 400 })) as typeof fetch
  await withFetch(failing, async () => {
    await assert.rejects(startNewDialog(), /assistant is disabled/)
  })
  assert.equal(storedDialogId(), null)
})

const newConversation = (available = true): Command => ({
  id: 'assistant.newConversation', label: 'Start a new assistant conversation', category: 'voice',
  available, run: () => {}, voice: { phrases: NEW_CONVERSATION_PHRASES },
})
const spawnClaude: Command = {
  id: 'session.spawn:claude', label: 'New Claude in Alpha', category: 'session', available: true, run: () => {},
  voice: { phrases: sessionLaunchVoicePhrases({
    backend: 'claude', displayName: 'Claude Code', projectName: 'Alpha', projectNumber: 1, currentProject: true,
  }) },
}
const assistantToggle: Command = {
  id: 'assistant.toggle', label: 'Open the assistant chat', category: 'voice', available: true, run: () => {},
  voice: { phrases: ['assistant', 'open assistant', 'chat', 'close assistant'] },
}
const catchAll: Command = {
  id: 'voice.query', label: 'Ask a deterministic voice lookup', category: 'voice', available: true, run: () => {},
  voice: { phrases: ['{text}'] },
}

test('every declared phrase resolves deterministically, never through the catch-all', async () => {
  const registry = [newConversation(), spawnClaude, assistantToggle, catchAll]
  for (const phrase of NEW_CONVERSATION_PHRASES) {
    const resolved = resolveVoiceIntent(registry, phrase)
    assert.equal(resolved.match?.command.id, 'assistant.newConversation', `“${phrase}” must be deterministic`)
    assert.equal(resolved.confidence, 1, `“${phrase}” must be an exact alias, not a slot capture`)
  }
})

test('the aliases do not collide with spawning a session or opening the chat', async () => {
  const registry = [newConversation(), spawnClaude, assistantToggle, catchAll]
  // `new claude`/`new claude {text}` is the shape a careless alias would be
  // eaten by (or would eat); `chat` is the neighbouring assistant command.
  assert.equal(resolveVoiceIntent(registry, 'new claude').match?.command.id, 'session.spawn:claude')
  assert.equal(resolveVoiceIntent(registry, 'new claude fix the tests').match?.command.id, 'session.spawn:claude')
  assert.equal(resolveVoiceIntent(registry, 'chat').match?.command.id, 'assistant.toggle')
  // Filler and punctuation are normalized away before matching, so the tail the
  // wake-word split hands over lands on the same command.
  assert.equal(resolveVoiceIntent(registry, 'please start a new conversation.').match?.command.id,
    'assistant.newConversation')
})

test('the reply says both halves: cleared, and the old conversation is still there', async () => {
  // Half of this sentence is the trust boundary. Clearing runs with no
  // confirmation card only because nothing is destroyed, and the operator has
  // no way to know that unless the reply says so.
  assert.match(NEW_CONVERSATION_REPLY, /clear/i)
  assert.match(NEW_CONVERSATION_REPLY, /previous conversation is still there/i)
  assert.doesNotMatch(NEW_CONVERSATION_REPLY, /—/, 'spoken text keeps to plain dashes')
})

test('the command is discoverable in the voice catalog, and states its requirement when off', async () => {
  const groups = registeredVoiceReference([newConversation(false), catchAll])
  const entry = groups.flatMap(group => group.commands).find(command => command.id === 'assistant.newConversation')
  assert.ok(entry, 'a voice-only command must still be listed while unavailable')
  assert.equal(entry?.available, false)
  assert.deepEqual(entry?.phrases, NEW_CONVERSATION_PHRASES)
})
