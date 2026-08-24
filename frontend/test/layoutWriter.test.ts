import assert from 'node:assert/strict'
import test from 'node:test'
import { createLayoutWriter, type LayoutWriterPorts } from '../src/layoutWriter.ts'
import { emptyLayout, parseLayout, terminalIds, type PaneLayout } from '../src/layout.ts'
import type { Project } from '../src/types.ts'

const layoutWith = (...ids: string[]): PaneLayout => parseLayout({
  version: 7,
  root: { type: 'stack', id: 'pane', active_child_id: ids[0] ?? null, children: ids.map(id => ({ type: 'leaf', kind: 'terminal', id })) },
})

const serialize = (layout: PaneLayout) => JSON.parse(JSON.stringify(layout)) as unknown

/** Let the write chain's own microtasks run: a write reaches the daemon a tick after the call. */
const flush = () => new Promise(resolve => setTimeout(resolve, 0))

type Harness = {
  ports: LayoutWriterPorts
  shown: { projectId: string; ids: string[] }[]
  patches: { projectId: string; revision: number; ids: string[] }[]
  adopted: Project[]
  errors: string[]
  refreshes: number
}

const harness = (over: Partial<LayoutWriterPorts> = {}): Harness => {
  const shown: Harness['shown'] = []
  const patches: Harness['patches'] = []
  const adopted: Project[] = []
  const errors: string[] = []
  let refreshes = 0
  let revision = 7
  const ports: LayoutWriterPorts = {
    patch: async (projectId, layout, sentRevision) => {
      patches.push({ projectId, revision: sentRevision, ids: terminalIds(layout) })
      revision += 1
      return { id: projectId, name: projectId, layout: serialize(layout), layout_revision: revision } as unknown as Project
    },
    showLayout: (projectId, layout) => { shown.push({ projectId, ids: terminalIds(layout) }) },
    adoptProject: project => { adopted.push(project) },
    serverRevision: () => 3,
    refresh: async () => { refreshes += 1 },
    onError: message => { errors.push(message) },
    ...over,
  }
  return {
    ports, shown, patches, adopted, errors,
    get refreshes() { return refreshes },
  }
}

test('a write shows the layout before the daemon has seen it, and again once it has', async () => {
  const bench = harness()
  const writer = createLayoutWriter(bench.ports)
  assert.equal(await writer.write('p1', layoutWith('a')), true)
  assert.deepEqual(bench.shown, [{ projectId: 'p1', ids: ['a'] }, { projectId: 'p1', ids: ['a'] }])
  assert.deepEqual(bench.patches, [{ projectId: 'p1', revision: 3, ids: ['a'] }])
  assert.deepEqual(bench.adopted.map(project => project.layout_revision), [8])
  assert.equal(writer.hasPendingWrite('p1'), false)
})

test('the second write goes out after the first, carrying the revision the first came back with', async () => {
  const releases: (() => void)[] = []
  const bench = harness()
  const gated: LayoutWriterPorts = {
    ...bench.ports,
    patch: async (projectId, layout, revision) => {
      await new Promise<void>(resolve => releases.push(resolve))
      return bench.ports.patch(projectId, layout, revision)
    },
  }
  const writer = createLayoutWriter(gated)
  const first = writer.write('p1', layoutWith('a'))
  const second = writer.write('p1', layoutWith('a', 'b'))
  await flush()
  assert.equal(writer.hasPendingWrite('p1'), true)
  // Only the first has reached the daemon; the second is still queued behind it.
  assert.equal(releases.length, 1)
  releases[0]()
  await first
  await flush()
  assert.equal(releases.length, 2)
  releases[1]()
  await second
  assert.deepEqual(bench.patches.map(patch => patch.revision), [3, 8])
  assert.deepEqual(bench.patches.map(patch => patch.ids), [['a'], ['a', 'b']])
  assert.equal(writer.hasPendingWrite('p1'), false)
})

test('a reply that a newer write has superseded updates the record but not the layout', async () => {
  const releases: (() => void)[] = []
  const bench = harness()
  const gated: LayoutWriterPorts = {
    ...bench.ports,
    patch: async (projectId, layout, revision) => {
      await new Promise<void>(resolve => releases.push(resolve))
      return bench.ports.patch(projectId, layout, revision)
    },
  }
  const writer = createLayoutWriter(gated)
  const first = writer.write('p1', layoutWith('a'))
  const second = writer.write('p1', layoutWith('a', 'b'))
  await flush()
  releases[0]()
  await first
  // The optimistic 'a,b' is on screen; the reply to the 'a' write must not snap it back.
  assert.deepEqual(bench.shown.map(entry => entry.ids), [['a'], ['a', 'b']])
  assert.deepEqual(bench.adopted.map(project => project.layout_revision), [8])
  await flush()
  releases[1]()
  await second
  assert.deepEqual(bench.shown.map(entry => entry.ids), [['a'], ['a', 'b'], ['a', 'b']])
})

test('a rejected write re-reads the fleet and names the conflict', async () => {
  const bench = harness({ patch: async () => { throw new Error('stale layout revision (expected 9)') } })
  const writer = createLayoutWriter(bench.ports)
  assert.equal(await writer.write('p1', layoutWith('a')), false)
  assert.equal(bench.refreshes, 1)
  assert.deepEqual(bench.errors, ['Layout changed in another client; reloaded the current layout.'])
  // The chain is released, so the next write is not stuck behind the failure.
  assert.equal(writer.hasPendingWrite('p1'), false)
})

test('a write nobody asked for reloads quietly', async () => {
  const bench = harness({ patch: async () => { throw new Error('stale layout revision') } })
  const writer = createLayoutWriter(bench.ports)
  assert.equal(await writer.write('p1', emptyLayout(), { quiet: true }), false)
  assert.equal(bench.refreshes, 1)
  assert.deepEqual(bench.errors, [])
})

test('a failure does not poison the writes queued behind it', async () => {
  let attempts = 0
  const bench = harness()
  const flaky: LayoutWriterPorts = {
    ...bench.ports,
    patch: async (projectId, layout, revision) => {
      attempts += 1
      if (attempts === 1) throw new Error('daemon is away')
      return bench.ports.patch(projectId, layout, revision)
    },
  }
  const writer = createLayoutWriter(flaky)
  const first = writer.write('p1', layoutWith('a'))
  const second = writer.write('p1', layoutWith('a', 'b'))
  assert.equal(await first, false)
  assert.equal(await second, true)
  assert.deepEqual(bench.errors, ['daemon is away'])
})

test('a fleet snapshot advances the revision, except for a Project mid-write', async () => {
  const releases: (() => void)[] = []
  const bench = harness()
  const gated: LayoutWriterPorts = {
    ...bench.ports,
    serverRevision: () => 0,
    patch: async (projectId, layout, revision) => {
      await new Promise<void>(resolve => releases.push(resolve))
      return bench.ports.patch(projectId, layout, revision)
    },
  }
  const writer = createLayoutWriter(gated)
  const inFlight = writer.write('p1', layoutWith('a'))
  await flush()
  writer.adoptRevisions([
    { id: 'p1', layout_revision: 99 } as unknown as Project,
    { id: 'p2', layout_revision: 42 } as unknown as Project,
  ])
  releases[0]()
  await inFlight
  // p1 wrote against the revision it already held, not the one the snapshot carried.
  assert.deepEqual(bench.patches.map(patch => patch.revision), [0])
  const other = writer.write('p2', layoutWith('b'))
  await flush()
  releases[1]()
  await other
  assert.equal(bench.patches[1].revision, 42)
})

test('each Project writes on its own chain', async () => {
  const releases: (() => void)[] = []
  const bench = harness()
  const gated: LayoutWriterPorts = {
    ...bench.ports,
    patch: async (projectId, layout, revision) => {
      await new Promise<void>(resolve => releases.push(resolve))
      return bench.ports.patch(projectId, layout, revision)
    },
  }
  const writer = createLayoutWriter(gated)
  const first = writer.write('p1', layoutWith('a'))
  const second = writer.write('p2', layoutWith('b'))
  await flush()
  // Both are out at once: a slow write in one Project does not hold up another.
  assert.equal(releases.length, 2)
  releases[0]()
  releases[1]()
  assert.deepEqual(await Promise.all([first, second]), [true, true])
  assert.equal(writer.hasPendingWrite('p1'), false)
  assert.equal(writer.hasPendingWrite('p2'), false)
})
