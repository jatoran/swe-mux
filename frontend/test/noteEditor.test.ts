import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { insertEditorTab } from '../src/editorText.ts'
import { LOOP_SAVE_LIMIT, LOOP_WINDOW_MS } from '../src/noteEditGuard.ts'
import { createNoteRailIcon, type NoteRailIcon } from '../src/noteRailIcons.ts'
import {
  NoteSaveQueue,
  fileSaveTarget,
  globalNoteSaveTarget,
  noteQueueKey,
  noteSaveTarget,
  type NoteSaveAck,
  type NoteSaveState,
  type ResourceSaveTarget,
} from '../src/noteSaveQueue.ts'

type Deferred = { resolve: (ack: NoteSaveAck) => void; reject: (error: unknown) => void }

function makeTransport() {
  const calls: { url: string; text: string; revision: string }[] = []
  const deferreds: Deferred[] = []
  const transport = (target: ResourceSaveTarget, text: string, revision: string) => {
    calls.push({ url: target.url, text, revision })
    return new Promise<NoteSaveAck>((resolve, reject) => deferreds.push({ resolve, reject }))
  }
  return { transport, calls, deferreds }
}

const noteTarget = noteSaveTarget('p1', 'note-a')

/**
 * A note loaded and then touched by a person: the starting state every save test assumes.
 *
 * `reset` takes the loaded document because that is the baseline the save guards judge against,
 * and it deliberately leaves the entry unable to save until a local input arrives - a reload
 * must never dirty a note by itself (`noteEditGuard.ts`). Tests about the *queue* say "a user
 * is editing this" once, here, rather than restating it in every case.
 */
function loaded(
  queue: NoteSaveQueue,
  key: string,
  revision = 'rev0',
  target: ResourceSaveTarget = noteTarget,
  text = '',
): void {
  queue.reset(key, target, revision, text)
  queue.markLocalInput(key)
}

const tick = () => new Promise(resolve => setImmediate(resolve))

type FakeSvgElement = {
  tag: string
  attributes: Record<string, string>
  children: FakeSvgElement[]
  setAttribute: (name: string, value: string) => void
  append: (child: FakeSvgElement) => void
}

function fakeSvgDocument(): Document {
  return {
    createElementNS: (_namespace: string, tag: string): FakeSvgElement => {
      const element: FakeSvgElement = {
        tag,
        attributes: {},
        children: [],
        setAttribute(name, value) { this.attributes[name] = value },
        append(child) { this.children.push(child) },
      }
      return element
    },
  } as unknown as Document
}

test('note rail copy and paste actions use accessible standard SVG marks', () => {
  const expected: Record<NoteRailIcon, { tags: string[]; distinctiveAttribute: [string, string] }> = {
    copy: { tags: ['rect', 'path'], distinctiveAttribute: ['d', 'M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2'] },
    paste: { tags: ['rect', 'path'], distinctiveAttribute: ['d', 'M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2'] },
  }
  for (const kind of Object.keys(expected) as NoteRailIcon[]) {
    const icon = createNoteRailIcon(kind, fakeSvgDocument()) as unknown as FakeSvgElement
    assert.equal(icon.tag, 'svg')
    assert.equal(icon.attributes.viewBox, '0 0 24 24')
    assert.equal(icon.attributes['aria-hidden'], 'true')
    assert.equal(icon.attributes.focusable, 'false')
    assert.deepEqual(icon.children.map(child => child.tag), expected[kind].tags)
    assert.ok(icon.children.every(child => child.attributes.fill === 'none'))
    const [attribute, value] = expected[kind].distinctiveAttribute
    assert.equal(icon.children[1].attributes[attribute], value)
  }
})

test('global notes use their own project-agnostic endpoint', () => {
  const target = globalNoteSaveTarget('scratchpad')
  assert.equal(target.url, '/api/global-notes/scratchpad')
  assert.deepEqual(target.body('remember this', 'rev0'), { markdown: 'remember this', revision: 'rev0' })
})

test('Tab inserts a literal tab and replaces the active selection', () => {
  assert.deepEqual(insertEditorTab('hello world', 5, 5), { text: 'hello\t world', caret: 6 })
  assert.deepEqual(insertEditorTab('hello world', 0, 5), { text: '\t world', caret: 1 })
})

test('save queue commits text with the storage revision and advances it on ack', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  const states: NoteSaveState[] = []
  queue.subscribe(key, state => states.push(state))
  loaded(queue, key)
  queue.submit(key, 'hello')
  queue.flush(key)
  assert.deepEqual(calls, [{ url: '/api/projects/p1/notes/note-a', text: 'hello', revision: 'rev0' }])
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.getState(key).storageRevision, 'rev1')
  assert.equal(queue.getState(key).status, 'saved')
})

test('only the newest pending snapshot is sent while one save is in flight', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  loaded(queue, key)
  queue.submit(key, 'A')
  queue.flush(key) // A now in flight against rev0
  queue.submit(key, 'B') // queued behind the in-flight save
  queue.submit(key, 'C') // supersedes B; newest wins
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  queue.flush(key)
  assert.deepEqual(calls.map(call => call.text), ['A', 'C'])
  assert.equal(calls[1].revision, 'rev1') // second save uses the acked revision
})

test('a storage conflict keeps local text, blocks auto-save, and overwrite re-commits it', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  loaded(queue, key)
  queue.submit(key, 'mine')
  queue.flush(key)
  deferreds[0].reject(Object.assign(new Error('note changed externally'), { status: 409 }))
  await tick()
  const conflicted = queue.getState(key)
  assert.equal(conflicted.status, 'conflict')
  assert.equal(conflicted.storageRevision, 'rev0') // unchanged: never adopt stale server revision
  assert.ok(conflicted.banner)
  // Further typing is retained but not auto-sent while blocked (no 409 loop).
  queue.submit(key, 'mine2')
  queue.flush(key)
  assert.equal(calls.length, 1)
  // Resolve by adopting the on-disk revision and overwriting with local text.
  queue.overwrite(key, 'rev5')
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[1], { url: '/api/projects/p1/notes/note-a', text: 'mine2', revision: 'rev5' })
})

test('reset adopts a fresh revision and clears conflict/pending state', () => {
  const { transport } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  loaded(queue, key)
  queue.submit(key, 'x')
  queue.reset(key, noteTarget, 'rev9', 'reloaded from disk')
  const state = queue.getState(key)
  assert.equal(state.storageRevision, 'rev9')
  assert.equal(state.status, 'idle')
  assert.equal(state.banner, null)
})

test('live follow is allowed only for a different remote revision while locally clean', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  loaded(queue, key)
  assert.equal(queue.canFollowRemote(key, 'rev1'), true)
  assert.equal(queue.canFollowRemote(key, 'rev0'), false)

  queue.submit(key, 'local edit')
  assert.equal(queue.canFollowRemote(key, 'rev1'), false)
  queue.flush(key)
  assert.equal(queue.canFollowRemote(key, 'rev1'), false)
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.canFollowRemote(key, 'rev1'), false)
  assert.equal(queue.canFollowRemote(key, 'rev2'), true)
})

test('a revision this browser replaced is recognisable as a stale read afterwards', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  loaded(queue, key)
  queue.submit(key, 'hello world')
  queue.flush(key)
  assert.equal(queue.hasSuperseded(key, 'rev0'), false) // not yet: the write has not landed
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  // A GET answered from the pre-write file returns rev0, after our PUT has already acked.
  assert.equal(queue.hasSuperseded(key, 'rev0'), true)
  assert.equal(queue.hasSuperseded(key, 'rev1'), false) // the current revision is not stale
  assert.equal(queue.hasSuperseded(key, 'rev7'), false) // a remote edit is not ours to reject
  assert.equal(queue.hasSuperseded(key, ''), false)
  assert.equal(queue.hasSuperseded(noteQueueKey('p', 'other'), 'rev0'), false)
})

test('a save that restores earlier content makes that revision current again', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  loaded(queue, key)
  queue.submit(key, 'edited')
  queue.flush(key)
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.hasSuperseded(key, 'rev0'), true)
  // Undo the edit: the content hashes back to rev0, which is now what is on disk.
  queue.submit(key, 'original')
  queue.flush(key)
  deferreds[1].resolve({ revision: 'rev0', status: 'ready' })
  await tick()
  assert.equal(queue.hasSuperseded(key, 'rev0'), false)
  assert.equal(queue.hasSuperseded(key, 'rev1'), true)
})

test('superseded revisions stay bounded across a long editing session', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p', 'r')
  loaded(queue, key)
  for (let step = 0; step < 20; step++) {
    queue.markLocalInput(key)
    queue.submit(key, `text ${step}`)
    queue.flush(key)
    deferreds[step].resolve({ revision: `rev${step + 1}`, status: 'ready' })
    await tick()
  }
  assert.equal(queue.hasSuperseded(key, 'rev19'), true) // the most recent predecessor
  assert.equal(queue.hasSuperseded(key, 'rev0'), false) // evicted long ago
})

test('project notes retain their storage identity through queued saves', () => {
  const { transport, calls } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('project', 'note:release-plan')
  loaded(queue, key, 'rev0', noteSaveTarget('project', 'release-plan'))
  queue.submit(key, 'release context')
  queue.flush(key)
  assert.deepEqual(calls, [{ url: '/api/projects/project/notes/release-plan', text: 'release context', revision: 'rev0' }])
})

test('markdown files queue-save to the project file endpoint with their path', () => {
  const { transport, calls } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('project', 'file:docs/readme.md')
  loaded(queue, key, 'rev0', fileSaveTarget('project', 'docs/readme.md'))
  queue.submit(key, '# hi')
  queue.flush(key)
  assert.deepEqual(calls, [{ url: '/api/projects/project/file', text: '# hi', revision: 'rev0' }])
  const body = fileSaveTarget('project', 'docs/readme.md').body('# hi', 'rev0')
  assert.deepEqual(body, { path: 'docs/readme.md', text: '# hi', revision: 'rev0' })
})

test('the unload beacon sends the newest snapshot even while a save is in flight', async () => {
  // While a PUT is in flight the newest text lives only in entry.pending, and
  // that PUT is a plain non-keepalive fetch the unload can abort. Skipping the
  // entry meant guaranteed loss of everything typed during the save.
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key)
  queue.submit(key, 'saved so far')
  queue.flush(key) // in flight
  queue.submit(key, 'typed while saving')

  const beacons: { url: string; body: unknown; keepalive: boolean }[] = []
  const realFetch = globalThis.fetch
  globalThis.fetch = ((url: string, init: RequestInit) => {
    beacons.push({
      url,
      body: JSON.parse(String(init.body)),
      keepalive: Boolean(init.keepalive),
    })
    return Promise.resolve(new Response('{}'))
  }) as typeof fetch
  try {
    queue.beaconFlushAll()
  } finally {
    globalThis.fetch = realFetch
  }

  assert.equal(beacons.length, 1)
  assert.equal(beacons[0].url, '/api/projects/p1/notes/note-a')
  assert.equal(beacons[0].keepalive, true)
  assert.deepEqual(beacons[0].body, { markdown: 'typed while saving', revision: 'rev0' })
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
})

test('the unload beacon skips entries with nothing pending and blocked entries', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const clean = noteQueueKey('p1', 'clean')
  const blocked = noteQueueKey('p1', 'blocked')
  loaded(queue, clean)
  loaded(queue, blocked)
  queue.submit(blocked, 'conflicted')
  queue.flush(blocked)
  deferreds[0].resolve({ revision: 'rev9', status: 'conflict' })
  await tick()

  const beacons: string[] = []
  const realFetch = globalThis.fetch
  globalThis.fetch = ((url: string) => {
    beacons.push(url)
    return Promise.resolve(new Response('{}'))
  }) as typeof fetch
  try {
    queue.beaconFlushAll()
  } finally {
    globalThis.fetch = realFetch
  }
  assert.deepEqual(beacons, [])
})

// `pendingText` is what makes moving a note between the drawer and a pane lossless. The two
// hosts are mutually exclusive, so a move unmounts one editor and mounts another against this
// same entry, and the arriving editor's GET can be a debounce behind what was typed.

test('pendingText is null for an unknown note and for a settled one', () => {
  const { transport } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  assert.equal(queue.pendingText(noteQueueKey('p1', 'never-touched')), null)
  loaded(queue, key)
  assert.equal(queue.pendingText(key), null)
})

test('pendingText reports typing that has not committed yet', () => {
  const { transport } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key)
  queue.submit(key, 'half a sentence')
  assert.equal(queue.pendingText(key), 'half a sentence')
})

test('pendingText still reports a snapshot a running save is carrying', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key)
  queue.submit(key, 'in flight')
  queue.flush(key)
  // `start` moves the text out of `pending` for the duration of the request. Without the
  // in-flight snapshot the arriving editor would see a settled note and adopt the daemon's
  // copy, which is exactly the text this PUT is about to replace.
  assert.equal(queue.pendingText(key), 'in flight')
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.pendingText(key), null)
})

test('pendingText prefers newer typing over the snapshot in flight', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key)
  queue.submit(key, 'first')
  queue.flush(key)
  queue.submit(key, 'second')
  assert.equal(queue.pendingText(key), 'second')
  deferreds[0].resolve({ revision: 'rev1', status: 'ready' })
  await tick()
  assert.equal(queue.pendingText(key), 'second')
})

test('a failed save leaves its text recoverable through pendingText', async () => {
  const { transport, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key)
  queue.submit(key, 'offline edit')
  queue.flush(key)
  deferreds[0].reject(new Error('the daemon did not respond in time.'))
  await tick()
  assert.equal(queue.pendingText(key), 'offline edit')
})

// The save loop (2026-08-19 → 2026-08-21): one note in two live views, each following the
// other's `note_changed`, re-seeding its engine, and sending that engine's commit straight back
// out. 1904 saves across the daemon logs, none of them typed. The queue is where the three
// guards meet a real entry; `noteEditGuard.test.ts` covers the policy itself.

test('a reload followed by the engine re-emitting it saves nothing', () => {
  const { transport, calls } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  queue.reset(key, noteTarget, 'rev0', '# Note\n\nbody\n')
  // Continuity commits what it was just handed, in its own serialization.
  queue.submit(key, '# Note\r\n\r\nbody\r\n\r\n')
  queue.flush(key)
  assert.equal(calls.length, 0)
  assert.equal(queue.getState(key).status, 'idle')
  // Even a commit that differs in content is the engine's, not the user's, until they type.
  queue.submit(key, '# Note\n\nbody rewritten by nobody\n')
  queue.flush(key)
  assert.equal(calls.length, 0)
  // And the first real keystroke after the reload still saves.
  queue.markLocalInput(key)
  queue.submit(key, '# Note\n\nbody typed\n')
  queue.flush(key)
  assert.deepEqual(calls.map(call => call.text), ['# Note\n\nbody typed\n'])
})

test('a commit that only re-serializes the stored document is not a save', () => {
  const { transport, calls } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key, 'rev0', noteTarget, 'body  \nmore\n')
  queue.submit(key, '﻿body   \r\nmore\r\n\r\n')
  queue.flush(key)
  assert.equal(calls.length, 0)
  assert.equal(queue.pendingText(key), null)
  assert.equal(queue.getState(key).status, 'idle')
})

test('a re-serialized commit never withdraws typing that is still owed to the daemon', () => {
  const { transport, calls } = makeTransport()
  const queue = new NoteSaveQueue(transport)
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key, 'rev0', noteTarget, 'body')
  queue.submit(key, 'body edited')
  // The engine re-emits the same document a beat later. Refusing that commit must not take
  // the queued edit with it.
  queue.submit(key, 'body edited\n\n')
  assert.equal(queue.pendingText(key), 'body edited')
  queue.flush(key)
  assert.deepEqual(calls.map(call => call.text), ['body edited'])
})

test('a note saving with no local input pauses, says so, and reports the episode', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const reports: unknown[] = []
  const queue = new NoteSaveQueue(transport, report => reports.push(report))
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key)
  for (let step = 0; step < LOOP_SAVE_LIMIT; step++) {
    queue.submit(key, `body ${step}`)
    queue.flush(key)
    deferreds[step].resolve({ revision: `rev${step + 1}`, status: 'ready' })
    await tick()
  }
  assert.equal(calls.length, LOOP_SAVE_LIMIT)
  queue.submit(key, 'body again')
  queue.flush(key)
  assert.equal(calls.length, LOOP_SAVE_LIMIT, 'the loop stops writing')
  const paused = queue.getState(key)
  assert.equal(paused.status, 'paused')
  assert.match(String(paused.banner), /changing elsewhere/)
  assert.deepEqual(reports, [{
    kind: 'paused',
    commits: LOOP_SAVE_LIMIT,
    windowMs: LOOP_WINDOW_MS,
    resource: key,
    revision: `rev${LOOP_SAVE_LIMIT}`,
  }])
  // A paused note still follows what the note now says elsewhere.
  assert.equal(queue.canFollowRemote(key, 'rev99'), true)
  // And typing is never blocked: it releases the pause and saves.
  queue.markLocalInput(key)
  assert.equal(queue.getState(key).status, 'saved')
  queue.submit(key, 'typed over the loop')
  queue.flush(key)
  assert.equal(calls.length, LOOP_SAVE_LIMIT + 1)
})

test('resume lifts a pause by hand and commits what was held', async () => {
  const { transport, calls, deferreds } = makeTransport()
  const queue = new NoteSaveQueue(transport, () => {})
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key)
  for (let step = 0; step < LOOP_SAVE_LIMIT; step++) {
    queue.submit(key, `body ${step}`)
    queue.flush(key)
    deferreds[step].resolve({ revision: `rev${step + 1}`, status: 'ready' })
    await tick()
  }
  queue.submit(key, 'refused')
  assert.equal(queue.getState(key).status, 'paused')
  queue.resume(key)
  assert.equal(queue.getState(key).status, 'saved')
  assert.equal(queue.getState(key).banner, null)
  queue.submit(key, 'after resume')
  queue.flush(key)
  assert.equal(calls[calls.length - 1].text, 'after resume')
})

test('a save-loop report is a POST the daemon can log, and never throws at the editor', () => {
  const { transport } = makeTransport()
  const queue = new NoteSaveQueue(transport, () => { throw new Error('reporter exploded') })
  const key = noteQueueKey('p1', 'r1')
  loaded(queue, key)
  for (let step = 0; step < LOOP_SAVE_LIMIT; step++) queue.submit(key, `body ${step}`)
  assert.doesNotThrow(() => queue.submit(key, 'body again'))
  assert.equal(queue.getState(key).status, 'paused')
})

// Continuity's first render measures inline-code affordances against `offsetParent`, which is
// null inside a `display:none` subtree, so an editor started hidden throws out of its async
// start and rejects `ready` instead of ever becoming usable. Two host guarantees keep that
// from reaching the user: the element is not created until its slot has a layout box, and a
// start that fails anyway is reported as itself rather than as a bare DOM message through the
// app's global unhandled-rejection backstop. `hidden-note-editor.spec.ts` covers the same two
// in a real browser, against the real engine.
test('the note editor defers its element until its slot has a layout box', () => {
  const editor = readFileSync(join(import.meta.dirname, '..', 'src', 'ProjectNoteEditor.tsx'), 'utf8')

  assert.match(editor, /whenLayoutBox\(slot/)
  assert.match(editor, /class="note-editor-slot"/)
  assert.ok(editor.includes('if (!engineSlotReady) return'), 'the gate must precede the element')
})

test('a note editor that cannot start reports itself instead of a raw DOM message', () => {
  const editor = readFileSync(join(import.meta.dirname, '..', 'src', 'ProjectNoteEditor.tsx'), 'utf8')

  assert.match(editor, /element\.ready\.catch/)
  assert.match(editor, /The note editor failed to start/)
  // Teardown before the engine starts is ordinary lifecycle, not a failure worth a toast.
  assert.match(editor, /abandonedEditors\.add/)
  assert.match(editor, /if \(abandonedEditors\.has\(element\)\) return/)
})
