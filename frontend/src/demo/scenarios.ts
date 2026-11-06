import { dt } from './messages.ts'
import { t } from '../localization/index.ts'
/**
 * Everything the director can play, as data.
 *
 * A scripted scenario and the walkthrough are the same thing to a visitor - something
 * driving the interface - so they are the same thing here too, and the tour is simply the
 * first entry. Two systems taking turns at one screen was the alternative, and it fails
 * in the obvious way: whichever one is not driving still thinks it is.
 *
 * A beat has exactly two levers, and which one it uses is the whole design:
 *
 * - **`mutate`** is something the *daemon* did. It reaches the store, so it mirrors to
 *   the other frame, appears in the event stream, and is indistinguishable from a real
 *   backend act - because for the demo it is one.
 * - **`command` / `click` / `type` / `key`** are things the *user* did, driven through the
 *   app's own command bus and its own controls (`drive.ts`). Never through app internals,
 *   which is what keeps a scripted act honest: it can only do what a visitor could.
 *
 * Every fixture here is invented, like every other demo fixture, and no harness is named:
 * `tests/test_harness_name_literals.py` allowlists three demo files by name and this is
 * not one of them, so backends are read off the fleet (`backendOf`) rather than written.
 *
 * Nothing here waits for the visitor. The walkthrough used to, and the gates are gone;
 * see `tourDesktop` for why, and `director.ts` for the one rule that replaced them.
 *
 * It is a `.ts` and its copy is plain strings on purpose. The obvious first cut had the
 * walkthrough's longer cards as JSX, which made this a `.tsx` - and Node's type stripping
 * does not handle JSX, so the unit suite could not import the catalogue at all and the
 * invariants below (unique ids, ordered beats, every callout naming chrome) had nothing
 * asserting them. Markup in a data file bought nothing and cost the tests.
 */
import {
  assistantDone, assistantHeard, assistantProposes, assistantResolved, assistantSays,
} from './assistantFixture.ts'
import type { Show } from './callouts.ts'
import { createPreview, spawnSession } from './fakeApi.ts'
import { scriptedCompletion, scriptedTurn } from './fakeSocket.ts'
import {
  makeLandRequest, makeQueueMessage, makeSpawnRequest, deliverQueuedMessage,
} from './controlPlane.ts'
import {
  DEMO_PROJECT_ID, DEMO_WORKTREE_COUPON,
} from './fixtures.ts'
import { apply, demoId, nowSeconds, session, state } from './store.ts'
// The app's own row-layout presets and its own persistence for them. See the sidebar
// stop in `tourDesktop` for why one beat writes a setting instead of pressing a control.
import { presetConfig, type SessionRowConfig } from '../sessionRowConfig.ts'
import { loadSessionRowConfig, saveSessionRowConfig } from '../sessionRowPrefs.ts'

// --------------------------------------------------------------------- the shapes

export type Beat = {
  pasteImage?: boolean
  /**
   * Milliseconds from the start of the scenario's clock.
   *
   * The walkthrough used to be the exception here: its beats were *gates* that waited for
   * the visitor, so `at` was only the pause before each card appeared. It drives itself
   * now, so there is one shape and `at` is simply the timeline - which means a beat's
   * budget has to cover what it does. A `walk` of nine labels holding 1.8s each needs
   * eighteen seconds before the next beat may start, or the visitor sees four of them.
   */
  at: number
  /** A short line under the caption card. Persists until a later beat replaces it. */
  say?: string
  /** Small caps line above the caption. */
  eyebrow?: string
  /** Longer body, for the walkthrough's cards: one string per paragraph. */
  body?: string[]
  /** Which piece of real chrome this beat is about. First visible match wins; it is both
   *  the spotlight and, for a `command` beat, where the ghost cursor goes. */
  spotlight?: string[]
  /** Something the daemon did. */
  mutate?: () => void
  /** A named app command, dispatched on the bus every chord and menu row uses. */
  command?: string
  /** A real control to press. */
  click?: string[]
  /** A key to send at the window, for the two acts that are keys rather than commands. */
  key?: 'Escape'
  /** Text typed into a pane's composer, one keystroke at a time. */
  type?: { session: string; text: string; submit?: boolean; pace?: number }
  /** Text typed into a field of the app's own chrome - the palette, a filter - as
   *  opposed to into a pane. Runs after this beat's press, because the field is usually
   *  the one that press just opened. */
  field?: { at: string[]; text: string; pace?: number; clear?: boolean }
  /**
   * Draw the swipe glyph over the spotlight, pointing this way.
   *
   * On a beat that opens a panel by command, which is what the phone walkthrough does now
   * that it no longer waits for a real drag: the glyph is the caption for the act, saying
   * which gesture the visitor would use to do the same thing themselves.
   */
  gesture?: 'left' | 'right'
  /**
   * What this beat draws over the app beyond its one ring: labelled callouts, a radar
   * sweep, a keycap HUD, a shimmer on a value that just changed, arrival marks on chrome
   * that just appeared, scanlines. It is data here and geometry in the view; see
   * `callouts.ts` for why the two are separate.
   *
   * A thunk, for the beats whose subject did not exist when the catalogue was built: a
   * scenario that approves two spawn requests cannot write down the ids of the sessions
   * it is about to create. It is resolved when the beat is published, which is before the
   * beat's own `mutate` runs and after every earlier beat's - so a beat naming what the
   * previous beat made is the shape that works, and a beat naming its own is not.
   */
  show?: Show | (() => Show)
}

export type Scenario = {
  id: string
  /** What the page's dropdown calls it. */
  label: string
  /** One line, shown while it plays. */
  blurb: string
  /**
   * Which card this run draws.
   *
   * `anchored` follows the chrome each beat explains and carries a counter and Next;
   * `caption` (the default) is a strip at the foot of the frame. It is a property of the
   * *run* rather than of a beat - a walkthrough is a walkthrough at every stop - and it
   * is stated here because the view used to infer it from "does this beat have a body",
   * which stopped being true the moment a stop had nothing left to say beyond its
   * headline.
   */
  card?: 'anchored' | 'caption'
  /** Put the fleet into the state this scenario opens on. Deliberately not a full reset:
   *  a visitor who has been playing keeps their panes, notes and layout. */
  prepare?: () => void
  beats: Beat[]
  /** The phone's own beats, where the wide layout's chrome does not exist. */
  mobileBeats?: Beat[]
}

// -------------------------------------------------------------------- small helpers

const DRAWER_TAB = (id: string): string[] => [`[data-drawer-tab-id="${id}"]`]

const sessionRow = (id: string): string[] => [`[data-sidebar-session-id="${id}"]`]

/**
 * One field inside one session row.
 *
 * `data-row-field` carries the token engine's own field id into the DOM
 * (`SessionRowBody.tsx`), which is what lets a callout say "the model" rather than
 * matching on a tooltip's wording. The state indicator has no field id because it is not
 * a token - it is the row's dot, and the context ring is drawn inside it.
 */
const rowField = (id: string, field: string): string[] =>
  [`[data-sidebar-session-id="${id}"] [data-row-field="${field}"]`]

const rowPart = (id: string, selector: string): string[] =>
  [`[data-sidebar-session-id="${id}"] ${selector}`]

/** One chip on the command rail, by its catalog id (`data-rail-item`, TerminalPane). */
const railItem = (id: string): string[] => [`.terminal-action-rail [data-rail-item="${id}"]`]

/** A backend name read off the fleet rather than written down. */
const backendOf = (sessionId: string): string => session(sessionId)?.backend ?? 'shell'

const nameOf = (sessionId: string): string => session(sessionId)?.name ?? sessionId

/** Put a seeded pane back mid-turn, so a scenario that ends its turn can be replayed. */
function reopenTurn(id: string): void {
  const target = session(id)
  if (!target || target.state === 'working') return
  const started = nowSeconds() - 96
  apply({
    kind: 'session-patch', id,
    patch: {
      state: 'working', state_since: started, turn_started_at: started,
      running_work_since: started,
    },
  })
}

// ---------------------------------------------------------------------- 1. the tour

/**
 * The walkthrough: the same stops as before, driving itself.
 *
 * It used to be built entirely out of gates - draw a card, name a control, wait for the
 * visitor to press it. That reads well and fails in practice, because it asks somebody who
 * has not decided to care yet to do work, and stalls forever on the ones who will not. So
 * every stop performs its own act now, through the app's own commands and controls
 * exactly as the scripted scenarios do, and the first real touch hands the whole thing
 * over (`director.ts`).
 *
 * Two consequences worth knowing before editing a beat here:
 *
 * - **The copy is short on purpose.** A gated card had a wait to fill and three
 *   paragraphs to fill it with. An autoplaying one is competing with motion on screen, so
 *   it is a headline and one sentence. The `body` may not be empty, though: a beat with no
 *   body renders as a bottom caption rather than as the anchored card with the counter.
 * - **`at` has to cover what the beat does.** A nine-label walk holding 1.8s each needs
 *   eighteen seconds, not the 200ms a gated beat used to carry.
 *
 * It also builds the layout it explains. The Project seeds as one pane with every session
 * in it as a tab (`fixtures.ts`), so the split and the side panel below are produced here
 * rather than found - which is the difference between showing a workspace and showing how
 * one is made.
 */
function tourDesktop(): Beat[] {
  const talker = 's-rage'
  /** The row layout as the visitor had it, so the preset stop can hand it back. Captured
   *  at the stop rather than here, because a replay must restore what is current then. */
  let seededRows: SessionRowConfig | null = null
  return [
    {
      at: 0,
      get eyebrow() { return dt("demo.2da0ce5d12") },
      get say() { return dt("demo.f9580bc4ee") },
      body: [dt("demo.50d025f84e")],
    },
    {
      at: 3_200,
      get eyebrow() { return dt("demo.eb515701f8") },
      get say() { return dt("demo.2eca68e379") },
      spotlight: ['.sidebar'],
      // The one beat that labels rather than points, and it does it one label at a time.
      // Nine at once is nine things being pointed at: a visitor reading any of them has to
      // work out which of nine leader lines belongs to it before the label means anything,
      // and the answer is on the far side of eight others. So the band crosses the column
      // once to say "all of this", and then the labels take turns against a dimmed frame.
      show: {
        reveal: 'walk',
        sweep: ['.sidebar'],
        crt: true,
        notes: [
          { at: rowPart('s-working', '.ind-core'), get label() { return dt("demo.dd5ace9e01") }, get sub() { return dt("demo.410fdf9f1d") } },
          { at: rowPart('s-claude', '.ind-core'), get label() { return dt("demo.4fb6234885") }, get sub() { return dt("demo.40fd9e27fc") } },
          { at: rowPart('s-working', '.ind-fill'), get label() { return dt("demo.84c2149f9d") } },
          { at: rowPart('s-rage', '.ind-fill'), get label() { return dt("demo.4bb63c7233") } },
          { at: rowField('s-working', 'badges'), get label() { return dt("demo.3f978e7d48") } },
          { at: rowField('s-working', 'duration'), get label() { return dt("demo.4cbe60de8f") } },
          { at: rowField('s-working', 'worktree'), get label() { return dt("demo.94edcf3d17") } },
          // The model comes off a different row on purpose: the checkout token above
          // spends that row's width, so its model name is elided to fit and pointing at
          // it names something the visitor cannot read. A row with no worktree has the
          // room to print the name in full.
          { at: rowField('s-rage', 'model'), get label() { return dt("demo.9372c470ee") } },
          { at: rowPart('s-shell', '.row-title'), get label() { return dt("demo.696f3fc979") } },
        ],
      },
      body: [dt("demo.30e1be90bb")],
    },
    {
      at: 21_700,
      get eyebrow() { return dt("demo.daaa56c14d") },
      get say() { return dt("demo.525bce5c4b") },
      // Three presets applied to the *live* column rather than a picture of three
      // columns, because the claim is that these are the same nine sessions drawn
      // differently and a screenshot cannot make it.
      //
      // The one place in the walkthrough that reaches a module rather than a control, and
      // the reason is the beat itself: the editor is inside a modal that would cover the
      // sidebar this stop exists to show changing, so driving it would demonstrate the
      // setting and hide its effect. `saveSessionRowConfig` is the app's own persistence
      // path - byte for byte what the editor's preset button calls - so the settings
      // store, the PUT and the redraw are all the real ones.
      mutate: () => {
        seededRows = loadSessionRowConfig()
        void saveSessionRowConfig(presetConfig('minimal'))
      },
      spotlight: ['.sidebar'],
      show: { shimmer: [['.sidebar']] },
      body: [dt("demo.bd44ad36cd")],
    },
    {
      at: 25_100,
      get say() { return dt("demo.e62ce8e2d8") },
      mutate: () => { void saveSessionRowConfig(presetConfig('detailed')) },
      spotlight: ['.sidebar'],
      show: { shimmer: [['.sidebar']] },
      body: [dt("demo.14527db97d")],
    },
    {
      at: 28_500,
      get say() { return dt("demo.d973ede81b") },
      mutate: () => { if (seededRows) void saveSessionRowConfig(seededRows) },
      spotlight: ['.sidebar'],
      show: { shimmer: [['.sidebar']] },
      body: [dt("demo.07bdcb8286")],
    },
    {
      at: 31_900,
      get eyebrow() { return dt("demo.0ac60907e0") },
      get say() { return dt("demo.2888d0c50a") },
      spotlight: ['.terminal-action-rail'],
      // This stop comes *before* the split, and the order is load-bearing: splitting the
      // workspace halves the pane, which halves the rail, and half of what this beat names
      // scrolls off the right edge. A callout whose target is off screen is dropped
      // (`DemoShow`), so the beat would silently lose four of its seven stops.
      //
      // The desktop rail and the phone rail are the opposite trade, and the tour used to
      // describe the phone's on both. You have a keyboard here: arrows and Ctrl-C are
      // already under your fingers, and what the rail is for is the things no key sends -
      // taking a conversation somewhere else, getting back into one, and pasting into a
      // TUI without it being read as a hundred keystrokes.
      //
      // In the rail's own left-to-right order, so a visitor's eye tracks the strip rather
      // than jumping around it. Branch is the exception and comes last, after the two
      // pickers and the overflow, because it is the one chip whose point is the surface it
      // opens and the tour should have finished with the strip before it goes there.
      show: {
        reveal: 'walk',
        sweep: ['.terminal-action-rail'],
        notes: [
          { at: railItem('attach'), get label() { return dt("demo.bf7e7432b5") }, get sub() { return dt("demo.87a74feefb") } },
          { at: railItem('copyReply'), get label() { return dt("demo.f90e805392") } },
          { at: railItem('paste'), get label() { return dt("demo.104849f4e4") }, get sub() { return dt("demo.e16a01effb") } },
          { at: railItem('markdownCodeFence'), get label() { return dt("demo.a6d24db96c") }, get sub() { return dt("demo.204897d9c1") } },
          { at: railItem('copyResume'), get label() { return dt("demo.00ec658d98") } },
          { at: railItem('clipboardHistory'), get label() { return dt("demo.d7e4d84748") } },
          { at: railItem('skills'), get label() { return dt("demo.8a2770e36e") } },
        ],
      },
    },
    {
      at: 46_400,
      get eyebrow() { return dt("demo.fcd06cb0b8") },
      get say() { return dt("demo.5fa8fc6130") },
      click: railItem('clipboardHistory'),
      spotlight: ['.rail-dropup'],
      // A raised `hold`, and this is the beat it was added for: the rows are sentences
      // rather than glyphs, and the shared pace goes through them faster than anybody can
      // read one. The lead half of it is what gives the drop-up a moment to be on screen
      // before the first chip lands on top of it.
      show: {
        reveal: 'walk',
        hold: 2_200,
        notes: [
          { at: ['.rail-dropup-list .rail-dropup-row'], get label() { return dt("demo.d7e4d84748") } },
          { at: ['.rail-dropup-row small'], get label() { return dt("demo.672efe61c1") } },
          { at: ['.rail-dropup-open'], get label() { return dt("demo.bd351b245b") } },
        ],
      },
      body: [dt("demo.7d5454bba7")],
    },
    {
      at: 55_300,
      key: 'Escape',
      spotlight: railItem('skills'),
      body: [dt("demo.edb00dc7e8")],
    },
    {
      at: 57_700,
      get eyebrow() { return dt("demo.ab30ce608f") },
      get say() { return dt("demo.8572fc04ac") },
      click: railItem('skills'),
      spotlight: ['.rail-dropup'],
      show: {
        reveal: 'walk',
        hold: 2_200,
        notes: [
          { at: ['.rail-dropup-row code'], get label() { return dt("demo.e4556a2ae0") } },
          { at: ['.rail-dropup-row .skill-flag'], get label() { return dt("demo.1d0a53ae90") } },
          { at: ['.rail-dropup-note'], get label() { return dt("demo.b94b3f7e14") } },
        ],
      },
      body: [dt("demo.6d9575418f")],
    },
    {
      at: 66_600,
      get eyebrow() { return dt("demo.3412ecf2bf") },
      get say() { return dt("demo.171694e927") },
      // Escape on a beat of its own, and this is the reason: `perform` runs a beat's
      // `key` *after* its `click`, so a beat that closed one surface and opened another
      // would open it and then shut it again.
      key: 'Escape',
      spotlight: ['.rail-more'],
      body: [dt("demo.98db710ca8")],
    },
    {
      at: 69_000,
      get say() { return dt("demo.41ba5941b7") },
      click: ['.rail-more'],
      spotlight: ['.rail-overflow-popover'],
      show: {
        reveal: 'blueprint',
        notes: [
          { at: ['.rail-overflow-grid'], get label() { return dt("demo.07fce09fce") } },
          { at: ['.rail-overflow-configure'], get label() { return dt("demo.345e78efee") } },
        ],
      },
      body: [dt("demo.0b32a26c98")],
    },
    {
      at: 74_000,
      get eyebrow() { return dt("demo.c4f6624585") },
      get say() { return dt("demo.6b4e5edb7b") },
      // A long stop for a short sentence, deliberately. The next beat opens a dialog over
      // the whole frame, and going straight there from a ring on a 30px chip gives nobody
      // time to connect the two.
      key: 'Escape',
      spotlight: railItem('branch'),
      body: [dt("demo.defcf58309")],
    },
    {
      at: 78_600,
      get say() { return dt("demo.e01b5bebd6") },
      click: railItem('branch'),
      spotlight: ['[data-dialog-id="branch"]'],
      // The real dialog, on the real transcript: the point list is this session's own
      // messages (`branchPointsPayload` in `fakeApi.ts`). Nothing is branched - the next
      // beat closes it - because the claim here is "from any point", and that is a claim
      // about the list rather than about the act.
      //
      // The slowest walk in the tour. Each row is a speaker, a timestamp, a cut and a line
      // of the conversation, and the point of the stop is that a visitor reads one.
      show: {
        reveal: 'walk',
        hold: 3_000,
        notes: [
          { at: ['.branch-point-list'], get label() { return dt("demo.23ec131796") } },
          { at: ['.branch-point-action'], get label() { return dt("demo.7f7b5e918c") } },
          { at: ['.modal-footer span'], get label() { return dt("demo.0a28a70faf") } },
        ],
      },
      body: [dt("demo.015bb85df4")],
    },
    {
      at: 90_900,
      get say() { return dt("demo.b951034871") },
      key: 'Escape',
      body: [dt("demo.c323cc0bba")],
    },
    {
      at: 93_900,
      get eyebrow() { return dt("demo.cb44185241") },
      get say() { return dt("demo.a22e0b142b") },
      key: 'Escape',
      spotlight: ['.sidebar'],
    },
    {
      at: 96_900,
      click: sessionRow(talker),
      spotlight: sessionRow(talker),
      body: [dt("demo.97e908eaef")],
    },
    {
      at: 99_700,
      get say() { return dt("demo.d5f9beb686") },
      // `pane.detach` rather than a split: splitting spawns a *new* session, which would
      // put a session in the fleet that the tour never mentions. Detaching moves the tab
      // that is already there, which is the act a visitor rearranging a workspace does.
      command: 'pane.detach',
      spotlight: ['.workspace'],
      show: { arrive: [['.terminal-pane.focused']] },
      body: [dt("demo.2e0ca3775f")],
    },
    {
      at: 103_300,
      get eyebrow() { return dt("demo.ae22c605c6") },
      get say() { return dt("demo.15decafe50") },
      type: { session: talker, text: dt("demo.faa3aa5f71"), pace: 42 },
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
      body: [dt("demo.9fe23cedae")],
    },
    {
      at: 106_700,
      get say() { return dt("demo.ea1fca2c78") },
      mutate: () => {
        scriptedTurn({
          id: talker,
          prompt: dt("demo.faa3aa5f71"),
          reply: [
            '● §Bash§(curl -w %{time_total} localhost:3000/cart)  ¶⎿ 0.011s¶',
            dt("demo.0e3ab9d7f0"),
            dt("demo.69cef7f9cb"),
          ],
        })
      },
      body: [dt("demo.918e6975e9")],
    },
    {
      at: 114_200,
      get eyebrow() { return dt("demo.24ceabcc9f") },
      get say() { return dt("demo.2b5b50cdc7") },
      command: 'drawer.show:notes',
      spotlight: DRAWER_TAB('notes'),
      show: { arrive: [DRAWER_TAB('notes')] },
      body: [dt("demo.092609b525")],
    },
    {
      at: 119_800,
      get eyebrow() { return dt("demo.b287de241d") },
      get say() { return dt("demo.7c82f83419") },
      command: 'drawer.show:files',
      spotlight: ['.drawer-files-browser'],
      body: [dt("demo.4b5d6c2392")],
    },
    {
      at: 123_800,
      click: ['.file-tree-row.directory[data-file-path="src"]'],
      spotlight: ['.file-tree'],
      body: [dt("demo.787b7cb540")],
    },
    {
      at: 126_400,
      get say() { return dt("demo.fba59e3964") },
      click: ['.file-tree-row.file[data-file-path="src/coupons.js"]'],
      spotlight: ['.project-resource.file-editor'],
      // Both halves at once, and they are far apart: the tree down one side of the panel
      // and the editor filling the rest. A walk holds the eye on each in turn rather than
      // drawing a leader line the width of the drawer.
      show: {
        reveal: 'walk',
        notes: [
          { at: ['.file-tree-row.file[data-file-path="src/coupons.js"]'], get label() { return dt("demo.55586c0d6e") } },
          { at: ['.code-editor'], get label() { return dt("demo.e6cb9a1f72") } },
        ],
      },
      body: [dt("demo.734ec6db2c")],
    },
    {
      at: 132_400,
      get eyebrow() { return dt("demo.d36274518e") },
      get say() { return dt("demo.b6e67ced43") },
      command: 'drawer.show:transcript',
      spotlight: DRAWER_TAB('transcript'),
      body: [dt("demo.ced7e8c3e6")],
    },
    {
      at: 137_400,
      get eyebrow() { return dt("demo.60cf072bca") },
      get say() { return dt("demo.26b7d72b65") },
      command: 'drawer.show:git',
      spotlight: DRAWER_TAB('git'),
      body: [dt("demo.b41328805a")],
    },
    {
      at: 142_400,
      get eyebrow() { return dt("demo.01f9e45ec7") },
      get say() { return dt("demo.f1045ae284") },
      // The summary button by its own class rather than "a button in the footer": the
      // footer carries several now, and any of them would have satisfied the step.
      command: 'drawer.close',
      click: ['.resource-usage-summary'],
      spotlight: ['.resource-usage-popover', '.resource-usage-summary'],
    },
    {
      at: 146_000,
      get say() { return dt("demo.18d7ab4c9e") },
      // The popover's own footer route rather than the menu command that opens the same
      // modal: the point of the beat is that the small reading is a door into the large
      // one, which is a thing about the surface rather than about the command bus.
      click: ['.resource-usage-popover footer button:last-child'],
      spotlight: ['[data-dialog-id="resources"]'],
      show: {
        reveal: 'walk',
        crt: true,
        notes: [
          { at: ['[data-dialog-id="resources"] [role="tab"]:nth-child(1)'], get label() { return dt("demo.41105616db") } },
          { at: ['[data-dialog-id="resources"] [role="tab"]:nth-child(2)'], get label() { return dt("demo.4a800faa0e") } },
          { at: ['[data-dialog-id="resources"] [role="tab"]:nth-child(4)'], get label() { return dt("demo.7fb2630a3b") } },
        ],
      },
      body: [dt("demo.b97452d8f7")],
    },
    {
      at: 152_200,
      get eyebrow() { return dt("demo.425dfdefd5") },
      get say() { return dt("demo.2008b9bd31") },
      key: 'Escape',
      spotlight: ['.account-summary'],
      show: {
        reveal: 'blueprint',
        notes: [{ at: ['.account-summary button'], get label() { return dt("demo.ccdba0bea4") }, get sub() { return dt("demo.e821a74643") } }],
      },
    },
    {
      at: 156_400,
      get say() { return dt("demo.7d20b099f8") },
      click: ['.account-summary button'],
      spotlight: ['.account-popover'],
      show: {
        reveal: 'walk',
        notes: [
          { at: ['.account-popover .quota-row.has-fable'], get label() { return dt("demo.1b016f2833") } },
          { at: ['.account-popover .account-refresh-age'], get label() { return dt("demo.36d6cd1964") } },
        ],
      },
      body: [dt("demo.0ff8cc0cbe")],
    },
    {
      at: 161_800,
      get say() { return dt("demo.7e405af21f") },
      click: ['.account-popover [data-provider-account$="-work"]'],
      show: { shimmer: [['.account-popover [data-provider-account$="-work"]']] },
      // Not "running sessions keep the one they started on", which is what this said and
      // is false for one of the two providers: one CLI stats its credential file ahead of
      // each request and follows the switch, the other reads its login once at startup.
      // The popover says which, per provider, off a fact the daemon declares
      // (`switch_reaches_live`) rather than off the provider's name.
      body: [dt("demo.db81036e3d")],
    },
    {
      at: 165_400,
      click: ['.account-popover [data-provider-account$="-personal"]'],
      show: { shimmer: [['.account-popover [data-provider-account$="-personal"]']] },
      body: [dt("demo.fe23435484")],
    },
    {
      at: 169_000,
      get eyebrow() { return dt("demo.effd00600f") },
      get say() { return dt("demo.06a1fd6ce7") },
      key: 'Escape',
      body: [dt("demo.e694c7caab")],
    },
  ]
}

/**
 * The phone walkthrough.
 *
 * Its two gestures used to be gates waiting for a real drag. They are commands now, with
 * the swipe glyph still drawn over the act - which is the honest arrangement rather than a
 * weaker one: the glyph is a caption saying which gesture does this, and the panel really
 * opens through the same command the gesture dispatches.
 */
function tourMobile(): Beat[] {
  const talker = 's-claude'
  return [
    {
      at: 0,
      get eyebrow() { return dt("demo.2da0ce5d12") },
      get say() { return dt("demo.df037137a9") },
      body: [dt("demo.89b3357286")],
    },
    {
      at: 2_800,
      get eyebrow() { return dt("demo.d5dff3d126") },
      get say() { return dt("demo.48fe91c80f") },
      command: 'sidebar.open',
      gesture: 'right',
    },
    {
      at: 6_200,
      get eyebrow() { return dt("demo.eb515701f8") },
      get say() { return dt("demo.2eca68e379") },
      // A walk rather than the desktop's swept column: an open panel covers most of a
      // phone, so there is no gutter to lay six labels out in, and the targets that stay
      // narrow enough to point at individually are the ones worth naming anyway. The
      // cutout dims what is not being talked about, which a 390px screen needs more than
      // a monitor does.
      show: {
        reveal: 'walk',
        sweep: ['.sidebar'],
        crt: true,
        notes: [
          { at: rowPart('s-working', '.ind-core'), get label() { return dt("demo.dd5ace9e01") } },
          { at: rowPart('s-claude', '.ind-core'), get label() { return dt("demo.4fb6234885") } },
          { at: rowPart('s-rage', '.ind-fill'), get label() { return dt("demo.6d94bc9b7b") } },
          { at: rowField('s-working', 'badges'), get label() { return dt("demo.d298391b07") } },
          { at: rowField('s-working', 'duration'), get label() { return dt("demo.9a915caa61") } },
          // Off the worktree-less row, for the reason the desktop beat gives: a phone is
          // where an elided model name is least readable, not most.
          { at: rowField('s-rage', 'model'), get label() { return dt("demo.9372c470ee") } },
        ],
      },
    },
    {
      at: 18_800,
      get eyebrow() { return dt("demo.0ac60907e0") },
      get say() { return dt("demo.a04e421161") },
      command: 'sidebar.close',
      spotlight: ['.terminal-action-rail'],
      // The opposite trade from the desktop stop, and the reason the two say different
      // things: a phone keyboard has no arrows and no control key, and those are most of
      // what driving an agent CLI is.
      //
      // Every one of these five is a chip a 390px phone has *on screen*. The rail scrolls,
      // and it used to name `esc` and `tab`, which sit past the right edge at that width:
      // the label was clamped back into the frame while its leader line ran off towards a
      // target nobody could see. `DemoShow` now drops a note whose target is wholly off
      // screen, so that failure is silent rather than wrong - which is a reason to choose
      // targets that fit, not a substitute for it.
      show: {
        reveal: 'walk',
        sweep: ['.terminal-action-rail'],
        notes: [
          { at: railItem('padArrows'), get label() { return dt("demo.d93e854302") }, get sub() { return dt("demo.2938b04ce2") } },
          { at: railItem('ctrlC'), get label() { return dt("demo.1ac80084a1") } },
          { at: railItem('ctrlU'), get label() { return dt("demo.6a136f7843") } },
          { at: railItem('copyReply'), get label() { return dt("demo.f90e805392") } },
          { at: railItem('prompts'), get label() { return dt("demo.bbebbc803a") }, get sub() { return dt("demo.6437dcd6ea") } },
        ],
      },
      body: [dt("demo.5a040f2e2e")],
    },
    {
      at: 29_400,
      get eyebrow() { return dt("demo.ae22c605c6") },
      get say() { return dt("demo.80512be257") },
      type: { session: talker, text: dt("demo.a6313eb304"), pace: 42 },
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
      body: [dt("demo.f04699421d")],
    },
    {
      at: 32_600,
      get say() { return dt("demo.ea1fca2c78") },
      mutate: () => {
        scriptedTurn({
          id: talker,
          prompt: dt("demo.a6313eb304"),
          reply: [
            dt("demo.152d8a61c5"),
            dt("demo.e80cee6a5b"),
            dt("demo.b470f0b099"),
          ],
        })
      },
      body: [dt("demo.dbba9b165b")],
    },
    {
      at: 40_400,
      get eyebrow() { return dt("demo.d5dff3d126") },
      get say() { return dt("demo.11ff4c3261") },
      command: 'drawer.toggle',
      gesture: 'left',
      body: ['Notes, files, transcript, Git, alerts.'],
    },
    {
      at: 45_000,
      get eyebrow() { return dt("demo.effd00600f") },
      get say() { return dt("demo.2560f58316") },
      command: 'drawer.close',
      body: [dt("demo.e694c7caab")],
    },
  ]
}

// ----------------------------------------------------------- 2. the queued delivery

const QUEUED_PROMPT
  = dt("demo.446e4692b5")

/**
 * The cheapest scenario and the one that demonstrates the part that is not commodity: a
 * turn ending, a prompt that was waiting behind it delivering itself, and swe-mux saying
 * so. Nothing here is about the terminal.
 */
function queueBeats(): Beat[] {
  const target = 's-working'
  return [
    {
      at: 0,
      get eyebrow() { return dt("demo.d5078497fa") },
      get say() { return dt("demo.ff985f5b29") },
      click: sessionRow(target),
      spotlight: sessionRow(target),
    },
    {
      at: 1_400,
      get say() { return dt("demo.66bf0c677b") },
      command: 'drawer.show:queue',
      spotlight: DRAWER_TAB('queue'),
      // The row's own depth token changes under the visitor at exactly this moment, and
      // it is two characters wide. A shimmer where it sits is the difference between a
      // fact the demo demonstrated and one it merely contained.
      show: { shimmer: [rowField(target, 'queue')] },
      mutate: () => {
        apply({
          kind: 'queue-add',
          message: makeQueueMessage({
            targetSessionId: target,
            body: QUEUED_PROMPT,
            state: 'armed',
          }),
        })
      },
    },
    {
      at: 3_400,
      get say() { return dt("demo.9f17332d4c") },
      mutate: () => { apply({ kind: 'auto-delivery-set', id: target, enabled: true }) },
    },
    {
      at: 5_000,
      get say() { return dt("demo.7cd8d705ed") },
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
      mutate: () => {
        scriptedCompletion({
          id: target,
          ask: dt("demo.a9516a5fe3"),
          reply: [
            dt("demo.1c6df3b0a6"),
            dt("demo.f66cd99a36"),
            dt("demo.d3484d0784"),
            dt("demo.bdf20de261"),
            dt("demo.ff39153505"),
          ],
        })
      },
    },
    {
      at: 9_800,
      get say() { return dt("demo.5ea2c2310b") },
      mutate: () => {
        const queued = state.queue.find(item => item.body === QUEUED_PROMPT && item.state === 'armed')
        if (queued) {
          deliverQueuedMessage(queued.id, [
            dt("demo.c77e15d039"),
            dt("demo.dcd506a17d"),
            dt("demo.a206905ad9"),
          ])
        }
      },
    },
    {
      at: 15_500,
      get say() { return dt("demo.c59e8e03b5") },
      mutate: () => {
        apply({
          kind: 'notification-add',
          notification: {
            id: demoId('note'),
            session_id: target,
            kind: 'queue_delivery',
            get title() { return dt("demo.67e2b51acd", { value1: nameOf(target) }) },
            message: dt("demo.711f5b93e3"),
            severity: 'info',
            created_at: nowSeconds(),
          },
        })
      },
    },
    {
      at: 18_500,
      get say() { return dt("demo.1bf5dd5c5b") },
      command: 'drawer.show:notifications',
      spotlight: DRAWER_TAB('notifications'),
      show: { arrive: [DRAWER_TAB('notifications')] },
    },
    { at: 22_000, get say() { return dt("demo.29345795e5") } },
  ]
}

// -------------------------------------------------------------- 3. the orchestration

/**
 * The highest-pitch run: one agent asked to split its work, and the boundary that makes
 * it safe. It drafts. A human approves. Only then does anything start.
 */
function orchestrateBeats(): Beat[] {
  const lead = 's-claude'
  const spawned: string[] = []
  return [
    {
      at: 0,
      get eyebrow() { return dt("demo.06b5d7d914") },
      get say() { return dt("demo.b7582087d8") },
      click: sessionRow(lead),
      spotlight: sessionRow(lead),
    },
    {
      at: 1_200,
      type: { session: lead, text: dt("demo.1d4cb73fd9"), pace: 42 },
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
    },
    {
      at: 4_200,
      get say() { return dt("demo.d106e428e4") },
      mutate: () => {
        scriptedTurn({
          id: lead,
          prompt: dt("demo.1d4cb73fd9"),
          reply: [
            dt("demo.b28b6ba574"),
            dt("demo.517247c3b1"),
            dt("demo.6c489001e8"),
            dt("demo.70c7446fb2"),
            dt("demo.99dcd056d0"),
          ],
        })
      },
    },
    {
      at: 10_500,
      mutate: () => {
        const backend = backendOf(lead)
        for (const draft of [
          {
            name: dt('demo.session.readers'),
            prompt: dt("demo.ca03e65064"),
            reason: dt("demo.2e894aff7c"),
          },
          {
            name: dt('demo.session.writers'),
            prompt: dt("demo.a803bf31dd"),
            reason: dt("demo.7dcd324612"),
          },
        ]) {
          apply({
            kind: 'spawn-request-add',
            request: makeSpawnRequest({
              projectId: DEMO_PROJECT_ID,
              projectName: 'rocket-shop',
              backend,
              fromSession: lead,
              ...draft,
            }),
          })
        }
      },
    },
    {
      at: 11_200,
      get say() { return dt("demo.7cd57c7ee3") },
      command: 'queue.fleet',
      spotlight: ['[data-dialog-id="fleet"]'],
      // A walk rather than a diagram, and only narrow targets. The card is nearly as wide
      // as the frame, so a shared gutter column would sit in the sixty pixels left over
      // beside it with leader lines running the whole way across; one label at a time is
      // placed against its own target and lands beside it.
      show: {
        reveal: 'walk',
        notes: [
          { at: ['.observation-request-tag'], get label() { return dt("demo.af0bf2a562") }, get sub() { return dt("demo.4b8d58cdfa") } },
          { at: ['.observation-request-status'], get label() { return dt("demo.3726868849") } },
          { at: ['.observation-request-actions .primary'], get label() { return dt("demo.cc4b1bcaa5") } },
        ],
      },
    },
    {
      at: 16_800,
      get say() { return dt("demo.1916bf6861") },
      mutate: () => {
        for (const request of state.spawnRequests.filter(item => !item.done)) {
          const created = spawnSession({
            backend: request.backend,
            project_id: request.project_id,
          }) as { id?: string } | undefined
          const id = created?.id
          if (id) {
            spawned.push(id)
            apply({ kind: 'session-patch', id, patch: { name: request.name } })
          }
          apply({
            kind: 'spawn-request-patch',
            id: request.id,
            patch: { done: true, status: 'approved', decided_by: 'you', session_id: id ?? null },
          })
        }
      },
    },
    {
      at: 18_800,
      key: 'Escape',
      get say() { return dt("demo.89c4db2782") },
      // The ids only exist because the previous beat made them, which is what the thunk
      // form of `show` is for. Stepped rather than eased: a row arriving in a fleet is a
      // terminal painting a line, not a panel sliding in.
      show: () => ({ arrive: spawned.map(id => sessionRow(id)) }),
    },
    {
      at: 20_800,
      get say() { return dt("demo.020f0878f3") },
      mutate: () => {
        for (const [index, id] of spawned.entries()) {
          const label = nameOf(id)
          apply({
            kind: 'queue-add',
            message: makeQueueMessage({
              targetSessionId: lead,
              senderKind: 'agent',
              senderLabel: label,
              originSessionId: id,
              state: 'armed',
              reason: dt("demo.afff4c83b9"),
              body: index === 0
                ? dt("demo.38150edcff")
                : dt("demo.6c2f926b5d"),
            }),
          })
        }
      },
    },
    {
      at: 22_300,
      command: 'drawer.show:queue',
      spotlight: DRAWER_TAB('queue'),
      click: sessionRow(lead),
    },
    {
      at: 25_300,
      get say() { return dt("demo.7e7db62d58") },
    },
  ]
}

// ------------------------------------------------------------------- 4. the preview

/**
 * The most visual run, and about 80% of it already existed: a shell that answers commands
 * and a preview pane that loads a real static page. What is added is the join between
 * them - the daemon noticing a listener and offering it as a pane.
 */
function previewBeats(mobile = false): Beat[] {
  const shell = 's-shell'
  return [
    { at: 0, eyebrow: mobile ? dt("demo.d429913cdf") : dt("demo.4ce25930f5"),
      get say() { return dt("demo.edff985c2b") }, click: sessionRow(shell), spotlight: sessionRow(shell) },
    { at: 3_000, say: mobile ? dt("demo.900f6598f8") : dt("demo.d649fc59ac"),
      ...(mobile ? {} : { command: 'pane.detach' }), spotlight: ['.terminal-pane.focused'] },
    { at: 7_000, get say() { return dt("demo.dba1a3cd84") }, type: { session: shell, text: 'npm run dev', submit: true, pace: 70 },
      spotlight: ['.terminal-pane.focused'] },
    { at: 12_000, get say() { return dt("demo.dc4a3b162c") },
      mutate: () => { createPreview({ session_id: shell }) } },
    // A phone has one pane, so the new preview arrives as a tab that is not selected; the
    // beat has to press it or the caption describes a preview nobody can see.
    { at: 17_000, say: mobile ? dt("demo.965d8e07a8") : dt("demo.8efcb3f58d"),
      ...(mobile ? { click: ['button[role="tab"][data-pane-kind="preview"]'] } : {}),
      spotlight: ['.preview-frame'] },
    { at: 22_000, get say() { return dt("demo.b6ba533d39") },
      body: [dt("demo.7a28075ed8")] },
    { at: 28_000, get say() { return dt("demo.2f63d84a6b") },
      body: [dt("demo.0810650b7f")],
      show: { notes: [{ at: ['.preview-frame'], get label() { return dt("demo.fae1d8395a") } }] } },
  ]
}

// ---------------------------------------------------------------------- 5. the land

/**
 * The land queue as a state machine rather than a finished row. Cheap, because the Git
 * fixtures already invent the repository this acts on, and worth having because reconcile
 * then verify then fast-forward is the whole argument for the surface.
 */
function landBeats(failure = false): Beat[] {
  const id = failure ? 'land-demo-failed' : 'land-demo-live'
  const branch = 'agent/coupon-table'
  return [
    {
      at: 0, get eyebrow() { return dt("demo.869c264ede") },
      get say() { return dt("demo.2ad6a35dc8") },
      body: [dt("demo.d0ed066386")],
      command: 'drawer.show:git', spotlight: DRAWER_TAB('git'),
    },
    {
      at: 5_000, get say() { return dt("demo.89c8cf914c") },
      mutate: () => {
        if (state.lands.some(item => item.id === id)) apply({ kind: 'land-remove', id })
        apply({ kind: 'land-add', request: makeLandRequest({
          id, projectId: DEMO_PROJECT_ID, branch, worktreeRoot: DEMO_WORKTREE_COUPON,
          requestedBy: 's-working',
        }) })
      },
      show: { notes: [{ at: ['.git-land-pipeline-step.queue'], get label() { return dt("demo.2ecf14c167") } }] },
    },
    {
      at: 11_000, get say() { return dt("demo.bdec91eb30") },
      body: [dt("demo.5c1c195b93")],
      mutate: () => { apply({ kind: 'land-patch', id, patch: { state: 'reconciling' },
        event: { state: 'reconciling', note: dt("demo.6c5a5ce914") } }) },
      show: { notes: [{ at: ['.git-land-pipeline-step.run'], get label() { return dt("demo.caf1ca9c62") } }] },
    },
    {
      at: 17_000, get say() { return dt("demo.e7d124a107") },
      body: [dt("demo.ed94009be7")],
      mutate: () => { apply({ kind: 'land-patch', id,
        patch: { state: 'verifying', verification: { kind: 'running', step: '.worktree-verify' } },
        event: { state: 'verifying', note: dt("demo.7bf033221e") } }) },
      show: { notes: [{ at: ['.git-land-pipeline-step.gate'], get label() { return dt("demo.46da621683") } }] },
    },
    {
      at: 24_000,
      say: failure ? dt("demo.fcd9260525") : dt("demo.6950983b7c"),
      mutate: () => {
        apply({ kind: 'land-patch', id,
          patch: failure
            ? { state: 'failed', error: dt("demo.73935a9f70"), verification: { kind: 'failed', reason: dt("demo.73935a9f70") } }
            : { state: 'landed', landed_at: nowSeconds(), verification: { kind: 'passed', reason: dt("demo.8822e6cc18") } },
          event: { state: failure ? 'failed' : 'landed', note: failure ? dt("demo.65e5987858") : dt("demo.79e3e2a224") },
        })
        if (failure) apply({ kind: 'queue-add', message: makeQueueMessage({
          targetSessionId: 's-working', body: 'Landing stopped: the coupon expiry test failed. Fix the branch and request landing again.',
          senderKind: 'rule', senderLabel: dt("demo.088fc313fe"),
        }) })
        else apply({ kind: 'notification-add', notification: {
          id: demoId('note'), session_id: 's-working', kind: 'land_completed',
          get title() { return dt("demo.ca370632cb") }, message: dt("demo.d8b5a7a742"),
          severity: 'info', created_at: nowSeconds(),
        } })
      },
      show: { notes: [{ at: ['.git-landing-headline', '.git-land-pipeline-step.run'], label: failure ? dt("demo.ec22261b12") : 'landed' }] },
    },
    {
      at: 31_000,
      say: failure ? dt("demo.da780e39e5") : dt("demo.ed9bfc130b"),
      body: [failure ? dt("demo.40bd7935a8") : dt("demo.0acba6d7af")],
      ...(failure ? { command: 'drawer.show:queue', spotlight: DRAWER_TAB('queue') } : {}),
    },
  ]
}

function statusBeats(): Beat[] {
  return [
    { at: 0, mutate: () => apply({ kind: 'session-patch', id: 's-migrate', patch: { state: 'awaiting', awaiting_reason: 'question' } }), get eyebrow() { return dt("demo.e343355f5f") }, get say() { return dt("demo.7cd597290d") },
      spotlight: ['.sidebar'], show: { reveal: 'walk', notes: [
        { at: rowPart('s-working', '.ind-core'), get label() { return dt("demo.dd5ace9e01") } },
        { at: rowPart('s-migrate', '.ind-core'), get label() { return dt("demo.d197592962") } },
      ] } },
    { at: 7_000, get say() { return dt("demo.cdcfb0ddcc") },
      mutate: () => apply({ kind: 'notification-add', notification: {
        id: demoId('note'), session_id: 's-migrate', kind: 'awaiting',
        get title() { return dt("demo.1ddf55a332") }, message: dt("demo.1b5ff8a75b"),
        severity: 'warning', created_at: nowSeconds(),
      } }), spotlight: ['.sidebar'] },
    { at: 13_000, get say() { return dt("demo.85204ef23f") }, click: sessionRow('s-migrate'),
      spotlight: ['.terminal-pane.focused'] },
    { at: 19_000, get say() { return dt("demo.c03eacac13") },
      body: [dt("demo.aebe5b6a12")],
      command: 'settings.open', field: { at: ['.settings-search input'], text: 'alerts' } },
  ]
}

function inputBeats(): Beat[] {
  const target = 's-rage'
  return [
    { at: 0, get eyebrow() { return dt("demo.46a18cf4c1") }, get say() { return dt("demo.115b378269") },
      click: sessionRow(target), spotlight: sessionRow(target),
      mutate: () => apply({ kind: 'term-input', id: target, data: '\x03' }) },
    { at: 4_000, get say() { return dt("demo.0da641295d") }, type: { session: target, text: dt("demo.41e63c1410"), pace: 85 },
      spotlight: ['.terminal-pane.focused'] },
    { at: 10_000, get say() { return dt("demo.43f72ae0c9") },
      show: { keys: ['ctrl', 'backspace'] },
      mutate: () => apply({ kind: 'term-input', id: target, data: '\x17' }) },
    { at: 15_000, get say() { return dt("demo.29e1f4244c") },
      show: { keys: ['shift', 'enter'] },
      mutate: () => apply({ kind: 'term-input', id: target, data: 'flow\x1b\r' }),
      type: { session: target, text: dt("demo.781e1b6a5b"), pace: 85 } },
    { at: 22_000, get say() { return dt("demo.0f5155bcf1") },
      body: [dt("demo.d63efb94fc")],
      spotlight: ['.terminal-pane.focused'] },
  ]
}

function attachmentBeats(): Beat[] {
  return [
    { at: 0, get eyebrow() { return dt("demo.f52fe85e35") }, get say() { return dt("demo.2919a7a096") },
      click: sessionRow('s-rage'), spotlight: sessionRow('s-rage'),
      mutate: () => apply({ kind: 'term-input', id: 's-rage', data: '\x03' }) },
    { at: 4_000, get say() { return dt("demo.1b96f8ddb8") }, pasteImage: true,
      show: { keys: ['ctrl', 'v'] }, spotlight: ['.terminal-pane.focused'] },
    { at: 8_000, get say() { return dt("demo.622d49c80b") },
      type: { session: 's-rage', text: ' Explain.', pace: 95 },
      spotlight: ['.terminal-pane.focused'] },
    { at: 14_000, get say() { return dt("demo.11a8773de9") },
      body: [dt("demo.7dd36cb69e")] },
  ]
}

function clipboardBeats(): Beat[] {
  return [
    { at: 0, get eyebrow() { return dt("demo.94b026b2ec") }, get say() { return dt("demo.bf1c57372b") },
      click: railItem('clipboardHistory'), spotlight: ['.clipboard-panel', '.utility-drawer'] },
    { at: 6_000, get say() { return dt("demo.b76e673dbc") },
      body: [dt("demo.6ab23ec2f6")],
      spotlight: ['.clipboard-panel', '.utility-drawer'] },
    { at: 12_000, get say() { return dt("demo.9a19c63b43") },
      body: [dt("demo.fa39f2f8b3")] },
  ]
}

function historyBeats(): Beat[] {
  return [
    { at: 0, get eyebrow() { return dt("demo.7d20a5cebe") }, get say() { return dt("demo.8dc3d0728c") },
      command: 'history.open', spotlight: ['.history-browser', '.history-panel'] },
    { at: 7_000, get say() { return dt("demo.b07ecf76c2") },
      body: [dt("demo.b3c067ce02")],
      command: 'drawer.show:transcript', key: 'Escape', spotlight: DRAWER_TAB('transcript') },
    { at: 14_000, get say() { return dt("demo.ab02750a60") },
      command: 'drawer.show:git', spotlight: DRAWER_TAB('git'),
      body: [dt("demo.67898bebd2")] },
  ]
}

// -------------------------------------------------------------------- 6. the palette

/** The scope tabs, in the order `PALETTE_PREFIXES` declares them. */
const paletteScope = (index: number): string[] =>
  [`.palette-scopes [role="tab"]:nth-child(${index})`]

/**
 * One field, four scopes, and the chord beside every answer.
 *
 * The palette is the surface that makes a keyboard-first product legible to somebody who
 * has never used it, and it is invisible until you know it is there - which is exactly
 * what a demo is for. The scenario types into the app's own input rather than into a
 * pane (`field`), so what a visitor sees is the real filter narrowing real entries.
 */
function paletteBeats(): Beat[] {
  const input = ['.palette input']
  return [
    {
      at: 0,
      get eyebrow() { return dt("demo.f1aa00c550") },
      get say() { return dt("demo.0e552a391c") },
      command: 'palette.open',
      spotlight: ['.palette'],
      show: { keys: ['ctrl', 'shift', 'p'] },
    },
    {
      at: 1_500,
      get say() { return dt("demo.8fbff35002") },
      field: { at: input, text: dt('demo.searchTranscript') },
    },
    {
      at: 4_400,
      get say() { return dt("demo.a13ef57407") },
      show: {
        reveal: 'walk',
        notes: [
          { at: paletteScope(1), get label() { return dt("demo.10f5f77eef") }, sub: '>' },
          { at: paletteScope(2), get label() { return dt("demo.1225ae6c1a") }, sub: '@' },
          { at: paletteScope(3), get label() { return dt("demo.04e2a9728a") }, sub: '#' },
          { at: paletteScope(4), get label() { return dt("demo.3d7db37d08") }, sub: ':' },
        ],
      },
    },
    {
      at: 11_800,
      get say() { return dt("demo.f871e3beee") },
      click: paletteScope(2),
      field: { at: input, text: session('s-working')?.name || 'coupon', clear: true },
    },
    {
      at: 15_200,
      get say() { return dt("demo.54a917470e") },
      show: {
        reveal: 'glitch',
        notes: [{ at: ['#command-results [role="option"] kbd'], get label() { return dt("demo.164cb894c8") } }],
      },
    },
    { at: 17_800, key: 'Escape', get say() { return dt("demo.1033f1f217") } },
  ]
}

// -------------------------------------------------------------------- 7. the keymap

/**
 * Five keymaps, and the honest half: which of their chords a browser tab will never see.
 *
 * The presets are the daemon's own documents (`keymapFixture.ts` is generated from
 * `swe_mux.keymaps`), so the chords a visitor switches to here are the ones the product
 * ships. The undeliverable list is generated the same way, for host `browser`, which is
 * why this scenario can point at a dead chord and say so rather than drawing it as
 * though it were live.
 */
function keymapBeats(): Beat[] {
  // The preset this scenario switches to, named once. Read off the fixture rather than
  // written twice, so a regenerated catalogue cannot leave the scenario pointing at a
  // preset the daemon no longer ships.
  const target = 'tmux'
  const card = (part = ''): string[] => [`[data-keymap-preset="${target}"] ${part}`.trim()]
  return [
    {
      at: 0,
      get eyebrow() { return dt("demo.2fb89d89d7") },
      get say() { return dt("demo.56db70ed93") },
      command: 'settings.open',
      spotlight: ['.settings-panel'],
    },
    {
      at: 1_600,
      get say() { return dt("demo.d8865d4740") },
      // Through the panel's own search rather than three presses down its nav. Both are
      // real paths a visitor has; this one is one act instead of three, and it does not
      // depend on the shape of a tab list that exists to be reorganised.
      field: { at: ['.settings-search input'], text: t('heading.f5281d5e03') },
    },
    {
      at: 3_800,
      click: ['#settings-search-results [role="option"]'],
      spotlight: ['.keymap-presets'],
    },
    {
      at: 5_400,
      show: {
        reveal: 'blueprint',
        notes: [
          { at: card('.keymap-preset-count'), get label() { return dt("demo.3197a9f3f1") }, get sub() { return dt("demo.86126cf910") } },
          { at: card('.keymap-warning'), get label() { return dt("demo.fc05f0f36b") } },
        ],
      },
      get say() { return dt("demo.f894e739fb") },
    },
    {
      at: 8_600,
      get say() { return dt("demo.79b936757d") },
      click: card('.keymap-preset-apply'),
      show: { keys: ['ctrl', 'b', '→', 'o'], crt: true },
    },
    { at: 10_400, click: ['.keymap-confirm .primary'] },
    {
      at: 12_600,
      get say() { return dt("demo.640ceab1a1") },
      show: {
        reveal: 'sweep',
        sweep: ['.keybinding-policy', '.keymap-host'],
        notes: [
          { at: ['.keymap-host'], get label() { return dt("demo.d0943086b7") }, get sub() { return dt("demo.fee9d137c7") } },
          { at: ['.keybinding-policy'], get label() { return dt("demo.66e85abf8b") }, get sub() { return dt("demo.262452b759") } },
        ],
      },
    },
    {
      at: 16_400,
      get say() { return dt("demo.6335ff1e01") },
    },
  ]
}

// --------------------------------------------------------------------- 8. the voice

/**
 * The assistant, as a conversation rather than as a microphone.
 *
 * There is no speech here in either direction and that is the design, not a gap: asking a
 * marketing page's visitor for microphone permission is a bad trade, and a demo whose
 * point depended on audio would fail silently on every phone with autoplay locked down.
 * What is worth showing is what happens after it understands - it answers out of the live
 * fleet, and anything it wants to *do* to that fleet becomes a card a person resolves
 * inside a visible window. All of that is on screen, so all of that is what this plays.
 */
function voiceBeats(): Beat[] {
  const target = 's-migrate'
  let proposed = ''
  return [
    {
      at: 0,
      get eyebrow() { return dt("demo.a7adac1b9f") },
      get say() { return dt("demo.8b7f382376") },
      command: 'assistant.toggle',
      spotlight: ['.assistant-panel', '.voice-dock-anchor'],
      show: { keys: ['ctrl', 'shift', 'space', 'x', 'a'], crt: true },
    },
    {
      at: 1_800,
      get say() { return dt("demo.cffa879e08") },
      mutate: () => { assistantHeard('what needs my attention?') },
    },
    {
      at: 3_400,
      mutate: () => {
        assistantSays(dt("demo.9832ae978d", { value1: nameOf(target) })
          + dt("demo.50cc6530a4"))
      },
    },
    {
      at: 6_000,
      get say() { return dt("demo.ae411d81b0") },
      mutate: () => {
        assistantSays(dt("demo.2acaf4112a")
          + dt("demo.fb3e19ecf9"))
      },
    },
    {
      at: 8_600,
      get say() { return dt("demo.bf6233b75d") },
      mutate: () => {
        proposed = assistantProposes({
          kind: 'queue_message',
          restatement: `send that decision to "${nameOf(target)}" as a queued prompt`,
          arguments: { target_session_id: target, body: 'the file owner invalidates; treat a replaced file as a write' },
        }).id
      },
      show: {
        reveal: 'blueprint',
        notes: [
          { at: ['.assistant-action p strong'], get label() { return dt("demo.a062975343") } },
          { at: ['.assistant-countdown'], get label() { return dt("demo.62f9384cfe") } },
          { at: ['.assistant-action .cancel'], get label() { return dt("demo.8f0d965ab8") } },
        ],
      },
    },
    {
      at: 13_000,
      get say() { return dt("demo.eb9649081e") },
      mutate: () => {
        if (proposed) assistantResolved(proposed, dt("demo.7a08f4b8ef", { value1: nameOf(target) }))
        assistantDone()
        apply({
          kind: 'queue-add',
          message: makeQueueMessage({
            targetSessionId: target,
            senderKind: 'agent',
            senderLabel: dt("demo.02cc4028cc"),
            body: 'the file owner invalidates; treat a replaced file as a write',
            reason: dt("demo.4a893ffc1e"),
            state: 'armed',
          }),
        })
      },
      show: { shimmer: [rowField(target, 'queue')], arrive: [DRAWER_TAB('queue')] },
    },
    {
      at: 17_000,
      get say() { return dt("demo.88c3c29164") },
    },
  ]
}

// ------------------------------------------------------------------- the catalogue

/**
 * The catalogue, and the one rule about its labels.
 *
 * A label is a dropdown row on a marketing page, so it has to answer "why would I click
 * that" before anything else can happen. Every entry names a thing a developer already
 * wants and states the outcome: "one field finds everything" describes the mechanism and
 * could be any product's search box, while "Find any command or session by name" is a
 * thing you might be trying to do this afternoon. The blurb underneath is where the
 * mechanism goes.
 */
export const SCENARIOS: Scenario[] = [
  {
    id: 'tour',
    get label() { return dt("demo.5da8039a7c") },
    get blurb() { return dt("demo.eb69def6b7") },
    // The one run whose card follows the chrome and counts its stops. Every other entry
    // is a caption at the foot of the frame: they narrate a thing happening rather than
    // walk somebody through a surface.
    card: 'anchored',
    get beats() { return tourDesktop() },
    get mobileBeats() { return tourMobile() },
  },
  {
    id: 'status',
    get label() { return dt("demo.782f20e6e0") },
    get blurb() { return dt("demo.f965b29a6c") },
    get beats() { return statusBeats() },
  },
  {
    id: 'input',
    get label() { return dt("demo.16f58626eb") },
    get blurb() { return dt("demo.d7fdd0cd36") },
    get beats() { return inputBeats() },
  },
  {
    id: 'attachment',
    get label() { return dt("demo.93dc658750") },
    get blurb() { return dt("demo.8f72d5a525") },
    get beats() { return attachmentBeats() },
  },
  {
    id: 'clipboard',
    get label() { return dt("demo.3e5ab6a962") },
    get blurb() { return dt("demo.bcf5e39313") },
    get beats() { return clipboardBeats() },
  },
  {
    id: 'history',
    get label() { return dt("demo.fd433189f9") },
    get blurb() { return dt("demo.d413e2a225") },
    get beats() { return historyBeats() },
  },
  {
    id: 'queue',
    get label() { return dt("demo.6a72438272") },
    get blurb() { return dt("demo.82ebdf3cec") },
    prepare: () => { reopenTurn('s-working') },
    get beats() { return queueBeats() },
  },
  {
    id: 'orchestrate',
    get label() { return dt("demo.fbb5a3e583") },
    get blurb() { return dt("demo.1abab7e4f6") },
    get beats() { return orchestrateBeats() },
  },
  {
    id: 'preview',
    get label() { return dt("demo.329a666b8d") },
    get blurb() { return dt("demo.adbe6a3722") },
    get beats() { return previewBeats() },
    get mobileBeats() { return previewBeats(true) },
  },
  {
    id: 'land',
    get label() { return dt("demo.c830a60471") },
    get blurb() { return dt("demo.26d0b0f811") },
    get beats() { return landBeats() },
  },
  {
    id: 'landfailure',
    get label() { return dt("demo.5d07a05335") },
    get blurb() { return dt("demo.1fddc49a2f") },
    get beats() { return landBeats(true) },
  },
  {
    id: 'palette',
    get label() { return dt("demo.d60124a9be") },
    get blurb() { return dt("demo.44fc8bf30b") },
    get beats() { return paletteBeats() },
  },
  {
    id: 'keymap',
    get label() { return dt("demo.a8874acc87") },
    get blurb() { return dt("demo.6df6105217") },
    get beats() { return keymapBeats() },
  },
  {
    id: 'voice',
    get label() { return dt("demo.4840454222") },
    get blurb() { return dt("demo.4793c1125d") },
    get beats() { return voiceBeats() },
  },
]

export const scenarioById = (id: string): Scenario | undefined =>
  SCENARIOS.find(item => item.id === id)

/** The one the idle nudge plays. The cheapest, the shortest, and the one that shows the
 *  half of the product a visitor cannot discover by clicking around. */
export const NUDGE_SCENARIO_ID = 'queue'

export { DRAWER_TAB, sessionRow }
