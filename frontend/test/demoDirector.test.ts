import assert from 'node:assert/strict'
import test from 'node:test'
import {
  fixtureSeconds, mulberry32, nextOrdinal, resetOrdinals, DEMO_EPOCH_MS,
} from '../src/demo/determinism.ts'
import {
  makeLandRequest, makeQueueMessage, makeSpawnRequest, queueAutoPayload, queueMailboxPayload,
  queueMessagesPayload, queueSummaryPayload, notificationsPayload, landPayload, landEventsPayload,
} from '../src/demo/controlPlane.ts'
import { SCENARIOS, scenarioById, NUDGE_SCENARIO_ID } from '../src/demo/scenarios.ts'
import { apply, state } from '../src/demo/store.ts'
import { DEMO_PROJECT_ID } from '../src/demo/fixtures.ts'
import {
  placeCallouts, gutterSide, wirePath, unionBox, sweepAxis, sweepDelays, type Box,
} from '../src/demo/callouts.ts'
import {
  demoDirectory, demoFile, demoFileSearch, demoFileTree,
} from '../src/demo/fileFixtures.ts'
import { verifyCommandPayload, worktreesPayload } from '../src/demo/gitFixtures.ts'
import { providerAccountsPayload } from '../src/demo/supportPayloads.ts'
import { parseLandVerifyCommand } from '../src/gitLand.ts'
import { railConfigFromBlob, type RailBlob } from '../src/commandRail.ts'
import { normalizeSessionRowConfig } from '../src/sessionRowConfig.ts'
import { normalizeSessionTopbarConfig } from '../src/sessionTopbarConfig.ts'

/**
 * The demo's scenario engine, at the two seams a browser is not needed for: the
 * deterministic sources everything else is derived from, and the control plane the
 * scenarios drive.
 *
 * The engine's *timing* half is exercised end to end instead, by `capture-demo.mjs
 * --check` and by `test/renderer/demo-director.spec.ts` - a scheduler that awaits real
 * timers and reads real chrome has nothing to say to a node assertion. What is here is
 * everything that can be wrong without a screen: a fixture that is not reproducible, a
 * catalogue whose beats are out of order, a payload that would crash the view reading it.
 */

// --------------------------------------------------------------------- determinism

test('the seeded PRNG is a pure function of its seed', () => {
  const first = Array.from({ length: 8 }, mulberry32(1234))
  const second = Array.from({ length: 8 }, mulberry32(1234))
  assert.deepEqual(first, second)
  // And a different seed is a different stream, or the "seed" is decoration.
  assert.notDeepEqual(first, Array.from({ length: 8 }, mulberry32(1235)))
})

test('the seeded PRNG stays inside [0, 1)', () => {
  // A draw outside the range would be silently wrong everywhere `Math.random` is used -
  // an index off the end of the joke pool, a negative jitter on a token count.
  const draw = mulberry32(0xabcdef)
  for (let index = 0; index < 500; index += 1) {
    const value = draw()
    assert.ok(value >= 0 && value < 1, `draw ${index} was ${value}`)
  }
})

test('generated ordinals are a counter, so an id can be named twice', () => {
  resetOrdinals()
  assert.deepEqual([nextOrdinal(), nextOrdinal(), nextOrdinal()], [1, 2, 3])
  resetOrdinals()
  assert.equal(nextOrdinal(), 1)
})

test('a generated fixture timestamp is a pure function of its id', () => {
  const epoch = Math.floor(DEMO_EPOCH_MS / 1000)
  assert.equal(fixtureSeconds('s-d3'), epoch + 3)
  assert.equal(fixtureSeconds('s-d4'), epoch + 4)
  assert.throws(() => fixtureSeconds('s-live'), /has no ordinal/)
})

test('the demo epoch is a fixed instant in the past', () => {
  // Fixed, because every fixture offset is measured back from it; in the past, because a
  // fixture that subtracts thirty days from it must not land in the future.
  assert.equal(DEMO_EPOCH_MS, Date.UTC(2026, 2, 14, 9, 41, 0))
  assert.ok(DEMO_EPOCH_MS < Date.now())
})

// ------------------------------------------------------------------- the catalogue

test('every scenario has a unique id and the tour is first', () => {
  const ids = SCENARIOS.map(item => item.id)
  assert.deepEqual([...new Set(ids)], ids, 'two scenarios share an id')
  assert.equal(ids[0], 'tour', 'the walkthrough is scenario one, not a separate system')
})

test('beats run forwards', () => {
  // `at` is the scenario clock, and a beat that goes backwards would be waited on for a
  // negative time - which is silently zero, so the two beats fire together and the
  // scenario plays in an order nobody wrote.
  for (const scenario of SCENARIOS) {
    for (const beats of [scenario.beats, scenario.mobileBeats ?? []]) {
      let previous = -1
      for (const [index, beat] of beats.entries()) {
        assert.ok(beat.at >= previous, `${scenario.id} beat ${index} moves backwards`)
        previous = beat.at
      }
    }
  }
})

test('the walkthrough drives itself at every beat', () => {
  // The tour used to be all gates: it named a control and waited for a real press. It
  // performs its own acts now, so every beat has to *do* something a visitor could have
  // done - a beat with copy and no lever is a card that appears, says a thing, and leaves
  // the interface exactly as it found it, which is the failure this replaced.
  const tour = scenarioById('tour')!
  const acts = (beat: (typeof tour.beats)[number]): boolean =>
    Boolean(beat.command || beat.click || beat.type || beat.key || beat.field
      || beat.mutate || beat.show)
  for (const [name, beats] of [['desktop', tour.beats], ['phone', tour.mobileBeats ?? []]] as const) {
    // The opening card is the one exception, and it is deliberately inert: it is the frame
    // around everything after it, so it introduces rather than acts.
    for (const [index, beat] of beats.slice(1).entries()) {
      assert.ok(acts(beat), `${name} tour beat ${index + 1} narrates without doing anything`)
    }
  }
})

test('a walkthrough card is a headline and at most one short line', () => {
  // The stops were written when a gated card had a wait to fill. An autoplaying one is
  // competing with motion on screen, so three paragraphs beside a moving interface is a
  // choice between reading and watching - and a visitor who has not decided to care yet
  // makes it by not reading. The limits are deliberately generous enough to phrase a
  // clause well and tight enough that a paragraph cannot come back.
  const tour = scenarioById('tour')!
  for (const [name, beats] of [['desktop', tour.beats], ['phone', tour.mobileBeats ?? []]] as const) {
    for (const [index, beat] of beats.entries()) {
      const where = `${name} tour beat ${index}`
      assert.ok((beat.say ?? '').length <= 46, `${where}: headline is ${beat.say?.length} chars`)
      assert.ok((beat.body ?? []).length <= 1, `${where}: ${beat.body?.length} paragraphs`)
      for (const line of beat.body ?? []) {
        assert.ok(line.length <= 62, `${where}: body line is ${line.length} chars`)
      }
    }
  }
  // The card that follows the chrome and counts its stops is the walkthrough's, and it is
  // now stated rather than inferred from "does this beat have a body" - which was a proxy
  // that stopped holding the moment a stop had nothing to say beyond its headline.
  assert.equal(tour.card, 'anchored')
  for (const scenario of SCENARIOS) {
    if (scenario.id === 'tour') continue
    assert.equal(scenario.card ?? 'caption', 'caption', scenario.id)
  }
})

test('a walk is given enough of the clock to finish one pass', () => {
  // The beat's own budget is the gap to the next `at`, and a walk needs its lead plus one
  // rest per label. A nine-label walk in a two-second slot draws one label and moves on,
  // which is not a failure anything else can see: the beat still "played".
  //
  // This is load-bearing twice over now. The walk stops on its last label rather than
  // wrapping, so a beat short of clock silently drops the labels it never reached - and a
  // beat with *spare* clock rests on the last one, which is the readable end and the
  // reason wrapping went.
  const WALK_MS = 1_800
  const SWEEP_MS = 1_500
  for (const scenario of SCENARIOS) {
    for (const beats of [scenario.beats, scenario.mobileBeats ?? []]) {
      for (const [index, beat] of beats.entries()) {
        const show = typeof beat.show === 'function' ? undefined : beat.show
        if (show?.reveal !== 'walk' || !show.notes?.length) continue
        const next = beats[index + 1]
        if (!next) continue
        const hold = show.hold ?? WALK_MS
        const lead = show.sweep ? SWEEP_MS : (show.hold ?? 0)
        const needed = lead + show.notes.length * hold
        assert.ok(
          next.at - beat.at >= needed,
          `${scenario.id} beat ${index} walks ${show.notes.length} labels in `
          + `${next.at - beat.at}ms, and needs ${needed}ms`,
        )
      }
    }
  }
})

test('the idle nudge names a scenario that exists', () => {
  // Every run aborts on the first real touch now, the walkthrough included, so there is
  // no longer a class of scenario the nudge must avoid - only one that must exist.
  assert.ok(scenarioById(NUDGE_SCENARIO_ID), 'the nudge names a scenario the catalogue does not have')
})

test('no scenario writes a harness name', () => {
  // `tests/test_harness_name_literals.py` allowlists three demo files and this is not one
  // of them; a backend belongs to the fleet fixture, and a scenario reads it off a session.
  const source = JSON.stringify(SCENARIOS.map(scenario => ({
    beats: scenario.beats.map(beat => [beat.say, beat.eyebrow, beat.body, beat.type?.text]),
    label: scenario.label,
    blurb: scenario.blurb,
  })))
  for (const name of ['claude', 'codex', 'opencode']) {
    assert.equal(source.toLowerCase().includes(name), false, `a scenario names ${name}`)
  }
})

// --------------------------------------------------------------------- callouts

/** A box, from the four numbers that actually vary. */
const box = (left: number, top: number, width = 60, height = 16): Box => ({
  left, top, width, height,
  right: left + width, bottom: top + height,
  cx: left + width / 2, cy: top + height / 2,
})

const entry = (target: Box, label = 'x', width = 90, height = 20) =>
  ({ callout: { at: ['.x'], label }, target, width, height })

const VIEWPORT = { width: 1280, height: 800 }

test('labels stack rather than overlap, however close their targets are', () => {
  // The whole reason this is a function rather than `top: target.cy`: a session row is
  // about 40px tall and carries seven facts, so the naive placement puts four labels in
  // the same twenty pixels and the beat reads as one smudge.
  const placed = placeCallouts(
    [30, 38, 46, 54, 62].map(top => entry(box(20, top))),
    VIEWPORT,
  )
  assert.equal(placed.length, 5)
  for (let index = 1; index < placed.length; index += 1) {
    assert.ok(
      placed[index].top >= placed[index - 1].top + 20,
      `label ${index} at ${placed[index].top} overlaps the one above it`,
    )
  }
})

test('a label is placed in target order, not in the order the beat wrote them', () => {
  // The deconfliction pass is only correct on a sorted list, and a scenario ordering its
  // notes by importance is a reasonable thing to do.
  const placed = placeCallouts(
    [entry(box(20, 400), 'lower'), entry(box(20, 100), 'upper')],
    VIEWPORT,
  )
  assert.deepEqual(placed.map(item => item.callout.label), ['upper', 'lower'])
})

test('the gutter goes to whichever side has room', () => {
  // Left-hand chrome (the fleet column) labels to its right; right-hand chrome (the side
  // panel) labels to its left. Measured from the targets, because one beat shape has to
  // serve both.
  assert.equal(gutterSide([box(20, 100)], VIEWPORT), 'right')
  assert.equal(gutterSide([box(1_100, 100)], VIEWPORT), 'left')
  // Several targets stacked in a column: still a side gutter, and still the side with
  // room, which is what the fleet column actually looks like.
  assert.equal(gutterSide([box(20, 100), box(20, 240)], VIEWPORT), 'right')
  assert.equal(gutterSide([box(1_100, 100), box(1_100, 240)], VIEWPORT), 'left')
  // And the case a centre reading gets wrong: a row inside a nearly-full-width dialog.
  // Its centre is the middle of the screen, so a centre rule calls it left-hand chrome
  // and puts the label in the sliver on the right; both slivers are equal here, and what
  // matters is that the rule is asking about space rather than about position.
  const wide = box(119, 200, 1_042, 20)
  assert.equal(gutterSide([wide], { width: 1_280, height: 800 }), 'right')
  assert.equal(gutterSide([box(400, 200, 860, 20)], { width: 1_280, height: 800 }), 'left')
})

test('a row of targets is labelled from above, not from beside', () => {
  // The command rail: five chips side by side along the bottom of the frame. A side
  // gutter puts every label on the same line as the thing it names, with a leader line
  // running horizontally through the four chips in between - so the axis follows the
  // arrangement, and the side within it is still free space.
  const rail = [265, 335, 537, 789, 997].map(left => box(left, 867, 60, 30))
  assert.equal(gutterSide(rail, { width: 1_440, height: 900 }), 'top')
  // The same strip near the top of the frame has its room the other way.
  const banner = [200, 400, 600].map(left => box(left, 12, 60, 24))
  assert.equal(gutterSide(banner, { width: 1_440, height: 900 }), 'bottom')
  // One target has no arrangement, however wide it is: a lone label goes beside its
  // chrome, which is where every callout went before there was a choice.
  assert.equal(gutterSide([box(265, 867, 800, 30)], { width: 1_440, height: 900 }), 'right')
})

test('a row of labels stacks sideways rather than on top of each other', () => {
  // The horizontal gutter's deconfliction is the mirror of the vertical one's, and it has
  // the same job: three rail chips 40px apart want three labels in the same 40px.
  const placed = placeCallouts(
    [265, 305, 345].map(left => entry(box(left, 867, 30, 30), `n${left}`, 90, 20)),
    { width: 1_440, height: 900 },
  )
  assert.equal(placed.length, 3)
  for (const item of placed) {
    assert.equal(item.side, 'top')
    assert.ok(item.top + item.height <= 867, 'a label sat on the chrome it names')
  }
  for (let index = 1; index < placed.length; index += 1) {
    assert.ok(
      placed[index].left >= placed[index - 1].left + 90,
      `label ${index} at ${placed[index].left} overlaps the one before it`,
    )
  }
})

test('the walk keeps one gutter for the whole beat', () => {
  // A walk places one label at a time, so the side has to come from the whole set: asked
  // about the active target alone, a rail chip on the left of the strip would be labelled
  // from the right and the next one from above, and the gutter would jump between stops.
  const rail = [265, 335, 997].map(left => box(left, 867, 60, 30))
  const viewport = { width: 1_440, height: 900 }
  const side = gutterSide(rail, viewport)
  for (const target of rail) {
    const [only] = placeCallouts([entry(target, 'one', 90, 20)], viewport, side)
    assert.equal(only.side, 'top')
  }
})

test('a label stays inside the viewport on both axes', () => {
  // A wide chip beside chrome near the right edge, and a target below the fold: both
  // clamp, because the two things most worth labelling sit on the frame's edges.
  const [wide] = placeCallouts([entry(box(300, 790), 'edge', 600, 40)], VIEWPORT)
  assert.equal(wide.side, 'right')
  assert.ok(wide.left + wide.width <= VIEWPORT.width, 'ran off the right edge')
  assert.ok(wide.top + wide.height <= VIEWPORT.height, 'ran off the bottom edge')
  // And on the other side the clamp is the mirror.
  const [mirrored] = placeCallouts([entry(box(1_240, 40), 'edge', 300, 20)], VIEWPORT)
  assert.equal(mirrored.side, 'left')
  assert.ok(mirrored.left >= 0, 'ran off the left edge')
  // A row along the very top has no room above it for a chip, so the clamp is what keeps
  // the label on screen even though the side it chose is the one with more space.
  const [high] = placeCallouts(
    [entry(box(400, 2, 60, 16), 'edge', 90, 20), entry(box(600, 2, 60, 16), 'edge2', 90, 20)],
    VIEWPORT,
  )
  assert.equal(high.side, 'bottom')
  assert.ok(high.top >= 0, 'ran off the top edge')
})

test('the leader line is orthogonal, so nine of them do not cross', () => {
  const [item] = placeCallouts([entry(box(20, 300))], VIEWPORT)
  assert.match(wirePath(item), /^M [\d.]+ [\d.]+ H [\d.]+ V [\d.]+ H [\d.]+$/)
  // The horizontal gutter turns the elbow ninety degrees: out of the top of the target,
  // across to the label's column, and up into its edge.
  const rail = [entry(box(265, 867, 30, 30), 'a', 90, 20), entry(box(345, 867, 30, 30), 'b', 90, 20)]
  const [chip] = placeCallouts(rail, { width: 1_440, height: 900 })
  assert.match(wirePath(chip), /^M [\d.]+ [\d.]+ V [\d.]+ H [\d.]+ V [\d.]+$/)
})

test('the sweep wakes each label as the band reaches it', () => {
  // The band is one element crossing the column once and the labels are scheduled
  // against its position; deriving the delay from the target is what makes the two read
  // as one effect rather than two that happen to overlap.
  const column = unionBox([box(0, 0, 300, 400)])!
  const delays = sweepDelays([box(20, 40), box(20, 200), box(20, 380)], column, 1_500)
  assert.ok(delays[0] < delays[1] && delays[1] < delays[2], 'delays must follow the band')
  assert.ok(delays[2] <= 1_500, 'nothing may wake after the band has gone')
  // The band leads the labels slightly, so the topmost target has already been passed
  // when its label arrives rather than the other way round.
  assert.equal(sweepDelays([box(20, 0, 60, 8)], column, 1_500)[0], 0)
})

test('the band crosses a strip the long way', () => {
  // A band travelling down an 800x30 command rail has crossed it in one frame and reads
  // as a flash. The axis comes off the chrome's own shape, not off the gutter, because it
  // is a fact about the thing being scanned.
  const rail = unionBox([box(265, 867, 800, 30)])!
  const column = unionBox([box(0, 0, 300, 400)])!
  assert.equal(sweepAxis(rail), 'across')
  assert.equal(sweepAxis(column), 'down')
  // And the labels are then scheduled along that axis: left to right, not top to bottom.
  const delays = sweepDelays([box(1_000, 867), box(300, 867)], rail, 1_500)
  assert.ok(delays[0] > delays[1], 'a strip must wake its labels left to right')
})

test('every callout names chrome, and every scenario that has one is playable', () => {
  // A callout with no selectors would measure nothing and draw a label pointing at the
  // origin; an empty label would draw an empty chip. Both are silent in a browser.
  for (const scenario of SCENARIOS) {
    for (const beats of [scenario.beats, scenario.mobileBeats ?? []]) {
      for (const [index, beat] of beats.entries()) {
        const show = typeof beat.show === 'function' ? beat.show() : beat.show
        for (const note of show?.notes ?? []) {
          assert.ok(note.at.length, `${scenario.id} beat ${index} has a callout with no target`)
          assert.ok(note.label.trim(), `${scenario.id} beat ${index} has an empty label`)
        }
      }
    }
  }
})

test('the walkthrough labels only fields the seeded row config actually draws', () => {
  fresh()
  const config = normalizeSessionRowConfig(state.deviceSettings.desktop.sessionRows)
  const placedFields = new Set<string>([
    ...config.top.left, ...config.top.right, ...config.bottom.left, ...config.bottom.right,
  ].map(slot => slot.id))
  const named = new Set<string>()
  for (const scenario of SCENARIOS) {
    for (const beats of [scenario.beats, scenario.mobileBeats ?? []]) {
      for (const beat of beats) {
        const show = typeof beat.show === 'function' ? beat.show() : beat.show
        for (const note of show?.notes ?? []) {
          for (const selector of note.at) {
            const field = selector.match(/data-row-field="([a-zA-Z]+)"/)?.[1]
            if (field) named.add(field)
          }
        }
      }
    }
  }
  assert.ok(named.size > 0, 'the anatomy beat names no row field at all any more')
  for (const field of named) {
    assert.ok(placedFields.has(field), `a callout names "${field}", which the seed does not place`)
  }
})

// ------------------------------------------------------------------ the file explorer

type Listing = { path: string; parent: string | null; items: Array<{ name: string; path: string; kind: string }> }
type FileText = { status: string; text: string; presentation: { kind: string } }

const listing = (path: string): Listing => demoDirectory(DEMO_PROJECT_ID, path) as Listing

test('the file tree lists directories first, and every file in it opens', () => {
  // The reader does not sort, so a fixture in insertion order would put `README.md` above
  // `src/` and look wrong in a way nothing else catches.
  const root = listing('')
  assert.equal(root.parent, null)
  const kinds = root.items.map(item => item.kind)
  assert.deepEqual(kinds, [...kinds].sort((left, right) =>
    (left === right ? 0 : left === 'directory' ? -1 : 1)))
  // And every leaf really has content behind it: an empty editor reads as a file
  // somebody truncated, which is the failure the 404 exists to avoid.
  const walk = (path: string): void => {
    for (const item of listing(path).items) {
      if (item.kind === 'directory') { walk(item.path); continue }
      const file = demoFile(DEMO_PROJECT_ID, item.path) as FileText | null
      assert.ok(file, `${item.path} lists but does not open`)
      assert.equal(file.status, 'ready', item.path)
      assert.equal(file.presentation.kind, 'text', item.path)
      assert.ok(file.text.length > 40, `${item.path} is a stub`)
    }
  }
  walk('')
  assert.equal(demoFile(DEMO_PROJECT_ID, 'src/nope.js'), null)
})

test('the tree restores the root and an expanded folder in one round trip', () => {
  const tree = demoFileTree(DEMO_PROJECT_ID, ['', 'src']) as { directories: Record<string, Listing> }
  assert.deepEqual(Object.keys(tree.directories).sort(), ['', 'src'])
  assert.equal(tree.directories.src.parent, '')
  // A folder that is gone comes back missing rather than empty, which is what lets the
  // reader prune a stale saved set instead of drawing an empty branch forever.
  const stale = demoFileTree(DEMO_PROJECT_ID, ['', 'src/gone']) as { directories: Record<string, Listing> }
  assert.deepEqual(Object.keys(stale.directories), [''])
})

test('the tree carries the files the Git fixture says are changed in it', () => {
  // Three surfaces, one invented repository. A Files tab that could not open the file the
  // Git tab says a worktree has modified would demonstrate the opposite of what either
  // surface is for. Deletions are excluded, because a deleted file is exactly the one the
  // tree should *not* have.
  const paths = new Set<string>()
  const walk = (path: string): void => {
    for (const item of listing(path).items) {
      if (item.kind === 'directory') walk(item.path)
      else paths.add(item.path)
    }
  }
  walk('')
  // The *main* checkout only. The File Explorer browses the Project root, and a linked
  // worktree's branch adds files that root has not got yet - `scripts/import-coupons.mjs`
  // is exactly that, and the tree is right not to list it.
  const trees = worktreesPayload(DEMO_PROJECT_ID, 'full', '') as {
    worktrees: Array<{ main: boolean } & Record<string, unknown>>
  }
  let checked = 0
  for (const tree of trees.worktrees) {
    if (!tree.main) continue
    for (const group of ['unstaged', 'staged', 'branch_delta']) {
      const change = tree[group] as { files?: Array<{ path: string; status: string }> } | undefined
      for (const file of change?.files ?? []) {
        if (file.status === 'D') continue
        assert.ok(paths.has(file.path), `the tree has no ${file.path}`)
        checked += 1
      }
    }
  }
  assert.ok(checked > 3, 'the Git fixture named nothing, so this asserted nothing')
})

test('a content search finds a line that is really in the file', () => {
  const hits = demoFileSearch(DEMO_PROJECT_ID, 'invalidate', 'contents') as {
    items: Array<{ path: string; line: number; snippet: string }>
  }
  assert.ok(hits.items.length, 'no hit for a word the fixture contains')
  for (const hit of hits.items) {
    const file = demoFile(DEMO_PROJECT_ID, hit.path) as FileText
    assert.equal(file.text.split('\n')[hit.line - 1].trim(), hit.snippet)
  }
  assert.deepEqual((demoFileSearch(DEMO_PROJECT_ID, '', 'both') as { items: unknown[] }).items, [])
})

test('exactly one provider section reports the third quota window', () => {
  // The app draws the Fable column per provider section (`hasFableWindow`), and the demo
  // used to report none - so the switcher and the Usage overview both showed two columns
  // where a real install shows three. One section rather than all of them, because a
  // provider that has no such window must not grow an empty column.
  const payload = providerAccountsPayload() as {
    providers: string[]
    accounts: Array<{ provider: string; quota: { fable: { used_percent: number } | null } }>
  }
  const withFable = new Set(payload.accounts.filter(item => item.quota.fable).map(item => item.provider))
  assert.equal(withFable.size, 1, 'the third window must belong to one provider section')
  assert.equal([...withFable][0], payload.providers[0])
  for (const account of payload.accounts) {
    if (!account.quota.fable) continue
    assert.ok(account.quota.fable.used_percent > 0 && account.quota.fable.used_percent < 100)
  }
})

test('the land gate payload parses as a configured, approved gate', () => {
  // Parsed with the app's own parser rather than eyeballed, because the previous fixture
  // answered a 200 with entirely different key names - `command`, `grant`,
  // `approved_digest` - and every field the parser reads fell back to the empty gate. The
  // Git tab then told every visitor "No verification command. A land here would be
  // refused rather than run", directly under a scenario narrating the gate passing.
  const gate = parseLandVerifyCommand(verifyCommandPayload())
  assert.equal(gate.configured, true)
  assert.equal(gate.approved, true)
  assert.equal(gate.scriptPresent, true)
  assert.equal(gate.display, '.worktree-verify')
})

// ------------------------------------------------------------------ control plane

/** Every test starts from the seed, because the store is one module-level value. */
const fresh = (): void => { apply({ kind: 'reset' }) }

test('the prompt queue starts empty and answers with a list either way', () => {
  fresh()
  const summary = queueSummaryPayload() as { targets: unknown[] }
  assert.deepEqual(summary.targets, [])
  // The shape matters more than the emptiness: a view rendering `messages.map(...)` throws
  // on a missing list, and a throw during render tears the whole demo down.
  const target = queueMessagesPayload('s-claude') as { messages: unknown[]; pending: number }
  assert.deepEqual(target.messages, [])
  assert.equal(target.pending, 0)
})

test('a queued message reaches the summary, the target view and the fleet view', () => {
  fresh()
  const message = makeQueueMessage({
    targetSessionId: 's-claude', body: 'have another look at the coupon table', state: 'armed',
  })
  apply({ kind: 'queue-add', message })

  const summary = queueSummaryPayload() as { targets: Array<{ target_session_id: string; pending: number }> }
  assert.deepEqual(summary.targets.map(row => [row.target_session_id, row.pending]), [['s-claude', 1]])

  const view = queueMessagesPayload('s-claude') as { messages: Array<{ id: string }>; pending: number }
  assert.deepEqual(view.messages.map(row => row.id), [message.id])
  assert.equal(view.pending, 1)

  // A human wrote it, so the fleet view's default partition (non-human authors) excludes it.
  const nonHuman = queueMailboxPayload('non_human') as { messages: unknown[] }
  assert.deepEqual(nonHuman.messages, [])
  const human = queueMailboxPayload('human') as { messages: Array<{ id: string }> }
  assert.deepEqual(human.messages.map(row => row.id), [message.id])
})

test('an agent-authored message is the one the fleet view is for', () => {
  fresh()
  apply({
    kind: 'queue-add',
    message: makeQueueMessage({
      targetSessionId: 's-claude', body: 'readers are done', senderKind: 'agent',
      senderLabel: 'coupon readers', originSessionId: 's-codex', state: 'armed',
    }),
  })
  const nonHuman = queueMailboxPayload('non_human') as { messages: Array<{ sender_label: string }> }
  assert.deepEqual(nonHuman.messages.map(row => row.sender_label), ['coupon readers'])
  const everything = queueMailboxPayload('all') as { messages: unknown[] }
  assert.equal(everything.messages.length, 1)
})

test('a queue patch moves the revision, so a stale write can be refused', () => {
  fresh()
  const message = makeQueueMessage({ targetSessionId: 's-claude', body: 'first' })
  apply({ kind: 'queue-add', message })
  assert.equal(state.queue[0].revision, 1)
  apply({ kind: 'queue-patch', id: message.id, patch: { body: 'second' } })
  assert.equal(state.queue[0].revision, 2)
  assert.equal(state.queue[0].body, 'second')
})

test('a sent message stops holding a place in the queue', () => {
  fresh()
  const message = makeQueueMessage({ targetSessionId: 's-claude', body: 'go', state: 'armed' })
  apply({ kind: 'queue-add', message })
  apply({ kind: 'queue-patch', id: message.id, patch: { state: 'sent' } })
  const view = queueMessagesPayload('s-claude') as { messages: unknown[]; pending: number }
  assert.equal(view.pending, 0, 'a sent message is history, not a pending one')
  assert.equal(view.messages.length, 1, 'and it is still on the record')
})

test('auto-delivery is per session inside the install-wide switch', () => {
  fresh()
  const before = queueAutoPayload() as { master_enabled: boolean; sessions: Array<{ session_id: string; enabled: boolean }> }
  assert.equal(before.master_enabled, true, 'the demo config has auto delivery on')
  assert.equal(before.sessions.every(row => !row.enabled), true, 'and no session opted in')
  apply({ kind: 'auto-delivery-set', id: 's-working', enabled: true })
  const after = queueAutoPayload() as { sessions: Array<{ session_id: string; enabled: boolean }> }
  assert.deepEqual(
    after.sessions.filter(row => row.enabled).map(row => row.session_id),
    ['s-working'],
  )
})

test('notifications arrive one at a time and can be dismissed together', () => {
  fresh()
  assert.deepEqual((notificationsPayload() as { automation: unknown[] }).automation, [])
  apply({
    kind: 'notification-add',
    notification: {
      id: 'n-1', kind: 'queue_delivery', title: 'delivered', message: 'a queued prompt was sent',
      severity: 'info', created_at: 1,
    },
  })
  const payload = notificationsPayload() as {
    automation: Array<{ id: string; read_at?: number }>
    notifications: unknown[]
    deliveries: unknown[]
  }
  assert.deepEqual(payload.automation.map(row => row.id), ['n-1'])
  // Both lists are present and empty rather than absent: the tab reads all three.
  assert.deepEqual(payload.notifications, [])
  assert.deepEqual(payload.deliveries, [])
  apply({ kind: 'notification-read-all', read: true })
  assert.ok((notificationsPayload() as { automation: Array<{ read_at?: number }> }).automation[0].read_at)
})

test('the land queue keeps its trail, and a patch appends rather than replaces', () => {
  fresh()
  const request = makeLandRequest({
    projectId: DEMO_PROJECT_ID, branch: 'agent/coupon-table',
    worktreeRoot: '/code/.worktrees/coupon-table', requestedBy: 's-working', id: 'land-test',
  })
  apply({ kind: 'land-add', request })
  assert.equal(request.state, 'queued')
  apply({
    kind: 'land-patch', id: 'land-test', patch: { state: 'verifying' },
    event: { state: 'verifying', note: 'running the gate' },
  })
  apply({
    kind: 'land-patch', id: 'land-test', patch: { state: 'landed' },
    event: { state: 'landed', note: 'fast-forwarded' },
  })
  const events = (landEventsPayload('land-test') as { events: Array<{ state: string }> }).events
  assert.deepEqual(events.map(row => row.state), ['queued', 'verifying', 'landed'])
})

test('the land queue is per Project, and off for the Project that has no worktrees', () => {
  fresh()
  const owned = landPayload(DEMO_PROJECT_ID) as { project_enabled: boolean; requests: unknown[] }
  assert.equal(owned.project_enabled, true)
  assert.equal(owned.requests.length, 1, 'the seed carries one finished landing')
  const other = landPayload('p-garden') as { project_enabled: boolean; requests: unknown[] }
  assert.equal(other.project_enabled, false, 'per Project and off by default is the real posture')
  assert.deepEqual(other.requests, [])
})

test('a spawn request is drafted rather than started', () => {
  fresh()
  const request = makeSpawnRequest({
    projectId: DEMO_PROJECT_ID, projectName: 'rocket-shop', backend: 'shell',
    prompt: 'move the readers', name: 'coupon readers',
    reason: 'the seams are independent', fromSession: 's-claude',
  })
  apply({ kind: 'spawn-request-add', request })
  assert.equal(request.done, false)
  assert.equal(request.session_id, null, 'drafting starts nothing; approval is what acts')
  const mailbox = queueMailboxPayload('non_human') as { spawn_requests: Array<{ id: string }> }
  assert.deepEqual(mailbox.spawn_requests.map(row => row.id), [request.id])
})

test('a reset puts the control plane back', () => {
  apply({ kind: 'queue-add', message: makeQueueMessage({ targetSessionId: 's-claude', body: 'x' }) })
  apply({ kind: 'auto-delivery-set', id: 's-claude', enabled: true })
  fresh()
  assert.deepEqual(state.queue, [])
  assert.deepEqual(state.autoDelivery, [])
  assert.deepEqual(state.notifications, [])
  assert.deepEqual(state.spawnRequests, [])
})

// --------------------------------------------------------- the seeded device settings

/**
 * Two settings the demo does not take the product's default for, checked against the
 * *resolved* config rather than against the blob it is written as.
 *
 * Both are derived from the app's own defaults and then edited, which is what keeps them
 * current - and is also exactly why they need a test. A renamed action id or a reshaped
 * default rail would leave the derivation running happily and silently stop editing
 * anything, and the failure is invisible: the demo would simply go back to showing what
 * these were set to hide.
 */
test('the demo seeds a session top bar with no approval control', () => {
  fresh()
  const config = normalizeSessionTopbarConfig(state.deviceSettings.desktop.sessionTopbar)
  const items = config.rows.flatMap(row => [...row.left, ...row.right])
  assert.ok(items.length > 1, 'the seed must still carry a top bar, not just be emptied')
  assert.deepEqual(items.filter(item => item.kind === 'action' && item.id === 'approvals'), [])
  // The rest of the default row is untouched, so this is a removal rather than a rewrite.
  assert.ok(items.some(item => item.kind === 'action' && item.id === 'drawer:transcript'))
})

test("the demo seeds the phone's Actions rail with one row, and leaves the desktop's alone", () => {
  fresh()
  const rail = railConfigFromBlob(state.deviceSettings.desktop.commandRail as RailBlob)
  assert.equal(rail.layouts.mobile.strip.length, 1)
  assert.ok(rail.layouts.mobile.strip[0].items.length > 4, 'the row that is kept must be the populated one')
  assert.equal(rail.layouts.desktop.strip.length, 1)
})

test('a settings write reaches the store and merges rather than replacing the profile', () => {
  fresh()
  const before = Object.keys(state.deviceSettings.desktop).sort()
  apply({ kind: 'settings-put', profile: 'desktop', domains: { sounds: { chime: true } } })
  assert.deepEqual(
    Object.keys(state.deviceSettings.desktop).sort(),
    [...before, 'sounds'].sort(),
    'a PUT carries only the domains it touched, so the others must survive it',
  )
  assert.deepEqual(state.deviceSettings.desktop.sounds, { chime: true })
})
