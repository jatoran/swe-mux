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

// --------------------------------------------------------------------- the shapes

export type Beat = {
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

const DRAWER_TAB = (label: string): string[] => [
  `button[title^="${label} -"]`,
  `button[aria-label^="${label} -"]`,
]

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
  return [
    {
      at: 0,
      eyebrow: 'THE REAL INTERFACE',
      say: 'The real app. Nothing here is a mock-up.',
      body: ['Invented sessions, shipped frontend.'],
    },
    {
      at: 3_200,
      eyebrow: 'THE FLEET',
      say: 'Every agent, at a glance.',
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
          { at: rowPart('s-working', '.ind-core'), label: 'working', sub: 'this one is mid-turn' },
          { at: rowPart('s-claude', '.ind-core'), label: 'idle', sub: 'waiting on you' },
          { at: rowPart('s-working', '.ind-fill'), label: 'the ring is context used' },
          { at: rowPart('s-rage', '.ind-fill'), label: 'and this one is nearly full' },
          { at: rowField('s-working', 'badges'), label: 'subagents it started' },
          { at: rowField('s-working', 'duration'), label: 'this turn, still running' },
          { at: rowField('s-working', 'worktree'), label: 'its own checkout' },
          // The model comes off a different row on purpose: the checkout token above
          // spends that row's width, so its model name is elided to fit and pointing at
          // it names something the visitor cannot read. A row with no worktree has the
          // room to print the name in full.
          { at: rowField('s-rage', 'model'), label: 'model' },
          { at: rowPart('s-shell', '.row-title'), label: 'a plain shell' },
        ],
      },
      body: ['State, context left, and what it started.'],
    },
    {
      at: 21_700,
      eyebrow: 'THE COMMAND RAIL',
      say: 'The acts a terminal has no key for.',
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
      // than jumping around it. Branch is the exception and comes last, in a beat of its
      // own, because it is the one chip whose point is the surface it opens.
      show: {
        reveal: 'walk',
        sweep: ['.terminal-action-rail'],
        notes: [
          { at: railItem('attach'), label: 'attach a file', sub: 'by path, into the composer' },
          { at: railItem('copyReply'), label: 'copy what it just said' },
          { at: railItem('paste'), label: 'paste, bracketed', sub: 'one block, not 400 keys' },
          { at: railItem('markdownCodeFence'), label: 'customise your common inserts', sub: 'a code fence, a rule, a signature' },
          { at: railItem('copyResume'), label: 'the command to resume it elsewhere' },
          { at: railItem('clipboardHistory'), label: 'everything you have copied' },
          { at: railItem('skills'), label: 'skills, by name' },
        ],
      },
    },
    {
      at: 36_100,
      eyebrow: 'THE CLIPBOARD',
      say: 'Two of those open a picker.',
      click: railItem('clipboardHistory'),
      spotlight: ['.rail-dropup'],
      // A raised `hold`, and this is the beat it was added for: the rows are sentences
      // rather than glyphs, and the shared 1.8s pace goes through them faster than
      // anybody can read one. The lead half of it is what gives the drop-up a moment to
      // be on screen before the first chip lands on top of it.
      show: {
        reveal: 'walk',
        hold: 2_200,
        notes: [
          { at: ['.rail-dropup-list .rail-dropup-row'], label: 'everything you have copied' },
          { at: ['.rail-dropup-row small'], label: 'from where, and how long ago' },
          { at: ['.rail-dropup-open'], label: 'the whole history is a tab' },
        ],
      },
      body: ['Panes and app both, kept per Project.'],
    },
    {
      at: 45_400,
      key: 'Escape',
      spotlight: railItem('skills'),
      body: ['The other one is the skills the CLI can run.'],
    },
    {
      at: 47_800,
      eyebrow: 'THE SKILLS',
      say: 'Every skill, by name.',
      click: railItem('skills'),
      spotlight: ['.rail-dropup'],
      show: {
        reveal: 'walk',
        hold: 2_200,
        notes: [
          { at: ['.rail-dropup-row code'], label: 'the command it sends' },
          { at: ['.rail-dropup-row .skill-flag'], label: 'and how the CLI loads it' },
          { at: ['.rail-dropup-note'], label: 'what it cannot see from out here' },
        ],
      },
      body: ['Yours, the Project\'s, and the ones a plugin brought.'],
    },
    {
      at: 57_100,
      key: 'Escape',
      spotlight: railItem('branch'),
      body: ['And the last chip opens a surface of its own.'],
    },
    {
      at: 59_500,
      eyebrow: 'BRANCHING',
      say: 'Fork the conversation from any turn.',
      click: railItem('branch'),
      spotlight: ['[aria-label="Choose where to branch this conversation"]'],
      // The real dialog, on the real transcript: the point list is this session's own
      // messages (`branchPointsPayload` in `fakeApi.ts`). Nothing is branched - the next
      // beat closes it - because the claim here is "from any point", and that is a claim
      // about the list rather than about the act.
      //
      // The slowest walk in the tour. Each row is a speaker, a timestamp, a cut and a
      // line of the conversation, and the point of the stop is that a visitor reads one.
      show: {
        reveal: 'walk',
        hold: 2_600,
        notes: [
          { at: ['.branch-point-list'], label: 'every turn, newest first' },
          { at: ['.branch-point-action'], label: 'before this one, or after it' },
          { at: ['.modal-footer span'], label: 'and exactly what it will carry' },
        ],
      },
      body: ['A wrong turn costs one branch, not the thread.'],
    },
    {
      at: 70_100,
      eyebrow: 'ALL OF THEM',
      say: 'Nothing was branched.',
      // Escape on a beat of its own, and this is the reason: `perform` runs a beat's
      // `key` *after* its `click`, so a beat that closed one surface and opened another
      // would open it and then shut it again.
      key: 'Escape',
      body: ['The strip is a ranking, not the whole set.'],
    },
    {
      at: 73_100,
      say: 'The rest are one press away.',
      click: ['.rail-more'],
      spotlight: ['.rail-overflow-popover'],
      show: {
        reveal: 'blueprint',
        notes: [
          { at: ['.rail-overflow-grid'], label: 'the complete row' },
          { at: ['.rail-overflow-configure'], label: 'and where you rebuild it' },
        ],
      },
      body: ['In the order you put them, on every pane.'],
    },
    {
      at: 78_100,
      eyebrow: 'ONE PANE, OR SEVERAL',
      say: 'One pane, every session a tab.',
      key: 'Escape',
      spotlight: ['.sidebar'],
    },
    {
      at: 81_100,
      click: sessionRow(talker),
      spotlight: sessionRow(talker),
      body: ['Picking one brings it to the front.'],
    },
    {
      at: 83_900,
      say: 'Any of them can have its own.',
      // `pane.detach` rather than a split: splitting spawns a *new* session, which would
      // put a session in the fleet that the tour never mentions. Detaching moves the tab
      // that is already there, which is the act a visitor rearranging a workspace does.
      command: 'pane.detach',
      spotlight: ['.workspace'],
      show: { arrive: [['.terminal-pane.focused']] },
      body: ['The arrangement belongs to the Project, not this browser.'],
    },
    {
      at: 87_500,
      eyebrow: 'TALK TO IT',
      say: 'The composer is the CLI\'s own.',
      type: { session: talker, text: 'is the cart endpoint still slow?', pace: 42 },
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
      body: ['A multiplexer around it, not a chat window.'],
    },
    {
      at: 90_900,
      say: 'It answers badly, on purpose.',
      mutate: () => {
        scriptedTurn({
          id: talker,
          prompt: 'is the cart endpoint still slow?',
          reply: [
            '● §Bash§(curl -w %{time_total} localhost:3000/cart)  ¶⎿ 0.011s¶',
            '● No. It has been eleven milliseconds for four hours, which is roughly',
            '  how long you have been asking me whether it is still slow.',
          ],
        })
      },
      body: ['A real process, not replayed video.'],
    },
    {
      at: 98_400,
      eyebrow: 'THE SIDE PANEL',
      say: 'Notes that outlive the context window.',
      command: 'drawer.show:notes',
      spotlight: DRAWER_TAB('Notes'),
      show: { arrive: [DRAWER_TAB('Notes')] },
      body: ['One per Project, one for the machine.'],
    },
    {
      at: 104_000,
      eyebrow: 'THE FILES',
      say: 'The checkout, beside the terminal.',
      command: 'drawer.show:files',
      spotlight: ['.drawer-files-browser'],
      body: ['Search names or contents; Recent is fed by Git.'],
    },
    {
      at: 108_000,
      click: ['.file-tree-row.directory[data-file-path="src"]'],
      spotlight: ['.file-tree'],
      body: ['Folders expand in place.'],
    },
    {
      at: 110_600,
      say: 'A file opens where you are looking.',
      click: ['.file-tree-row.file[data-file-path="src/coupons.js"]'],
      spotlight: ['.project-resource.file-editor'],
      // Both halves at once, and they are far apart: the tree down one side of the panel
      // and the editor filling the rest. A walk holds the eye on each in turn rather than
      // drawing a leader line the width of the drawer.
      show: {
        reveal: 'walk',
        notes: [
          { at: ['.file-tree-row.file[data-file-path="src/coupons.js"]'], label: 'the file you picked' },
          { at: ['.code-editor'], label: 'highlighted, editable, saved back' },
        ],
      },
      body: ['Ctrl-click opens it as a pane instead.'],
    },
    {
      at: 116_600,
      eyebrow: 'THE CONVERSATION',
      say: 'That turn, as readable messages.',
      command: 'drawer.show:transcript',
      spotlight: DRAWER_TAB('Transcript'),
      body: ['Searchable, copyable, never scrolled away.'],
    },
    {
      at: 121_600,
      eyebrow: 'THE REPOSITORY',
      say: 'Worktrees, commits, and who made them.',
      command: 'drawer.show:git',
      spotlight: DRAWER_TAB('Git'),
      body: ['Every commit traced to the run that wrote it.'],
    },
    {
      at: 126_600,
      eyebrow: 'THE MACHINE',
      say: 'What the fleet is actually consuming.',
      // The summary button by its own class rather than "a button in the footer": the
      // footer carries several now, and any of them would have satisfied the step.
      command: 'drawer.close',
      click: ['.resource-usage-summary'],
      spotlight: ['.resource-usage-popover', '.resource-usage-summary'],
    },
    {
      at: 130_200,
      say: 'And the full accounting behind it.',
      // The popover's own footer route rather than the menu command that opens the same
      // modal: the point of the beat is that the small reading is a door into the large
      // one, which is a thing about the surface rather than about the command bus.
      click: ['.resource-usage-popover footer button:last-child'],
      spotlight: ['[role="dialog"][aria-label="Resources"]'],
      show: {
        reveal: 'walk',
        crt: true,
        notes: [
          { at: ['[role="dialog"][aria-label="Resources"] [role="tab"]:nth-child(1)'], label: 'every process, per session' },
          { at: ['[role="dialog"][aria-label="Resources"] [role="tab"]:nth-child(2)'], label: 'what is listening, and on which port' },
          { at: ['[role="dialog"][aria-label="Resources"] [role="tab"]:nth-child(4)'], label: 'and the durable telemetry behind both' },
        ],
      },
      body: ['Per session, per Project, and for swe-mux itself.'],
    },
    {
      at: 136_400,
      eyebrow: 'WHOSE QUOTA',
      say: 'Every quota window, always in view.',
      key: 'Escape',
      spotlight: ['.account-summary'],
      show: {
        reveal: 'blueprint',
        notes: [{ at: ['.account-summary button'], label: 'three windows', sub: 'five-hour, weekly, and the big model' }],
      },
    },
    {
      at: 140_600,
      say: 'Several accounts, and what each is carrying.',
      click: ['.account-summary button'],
      spotlight: ['.account-popover'],
      show: {
        reveal: 'walk',
        notes: [
          { at: ['.account-popover .quota-row.has-fable'], label: 'the same three, per account' },
          { at: ['.account-popover .account-refresh-age'], label: 'and when that was last true' },
        ],
      },
      body: ['Polled, not guessed.'],
    },
    {
      at: 146_000,
      say: 'Switch which credential is current.',
      click: ['.account-popover [data-provider-account$="-work"]'],
      show: { shimmer: [['.account-popover [data-provider-account$="-work"]']] },
      body: ['Running sessions keep the one they started on.'],
    },
    {
      at: 149_600,
      click: ['.account-popover [data-provider-account$="-personal"]'],
      show: { shimmer: [['.account-popover [data-provider-account$="-personal"]']] },
      body: ['And back. Nothing was signed in or out.'],
    },
    {
      at: 153_200,
      eyebrow: 'IT IS YOURS NOW',
      say: 'It is yours. Break it.',
      key: 'Escape',
      body: ['Everything persists in this browser and nowhere else.'],
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
      eyebrow: 'THE REAL INTERFACE',
      say: 'The phone layout, not a picture of one.',
      body: ['The same frontend, laid out for a thumb.'],
    },
    {
      at: 2_800,
      eyebrow: 'GESTURE',
      say: 'Swipe right for the fleet.',
      command: 'sidebar.open',
      gesture: 'right',
    },
    {
      at: 6_200,
      eyebrow: 'THE FLEET',
      say: 'Every agent, at a glance.',
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
          { at: rowPart('s-working', '.ind-core'), label: 'working' },
          { at: rowPart('s-claude', '.ind-core'), label: 'idle' },
          { at: rowPart('s-rage', '.ind-fill'), label: 'context nearly full' },
          { at: rowField('s-working', 'badges'), label: 'subagents' },
          { at: rowField('s-working', 'duration'), label: 'this turn' },
          // Off the worktree-less row, for the reason the desktop beat gives: a phone is
          // where an elided model name is least readable, not most.
          { at: rowField('s-rage', 'model'), label: 'model' },
        ],
      },
    },
    {
      at: 18_800,
      eyebrow: 'THE COMMAND RAIL',
      say: 'Here, the rail is the keyboard.',
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
          { at: railItem('padArrows'), label: 'arrows', sub: 'drag a direction' },
          { at: railItem('ctrlC'), label: 'interrupt the turn' },
          { at: railItem('ctrlU'), label: 'clear the line' },
          { at: railItem('copyReply'), label: 'copy what it just said' },
          { at: railItem('prompts'), label: 'send a saved prompt', sub: 'no typing at all' },
        ],
      },
      body: ['No arrows and no control key on a phone.'],
    },
    {
      at: 29_400,
      eyebrow: 'TALK TO IT',
      say: 'A real turn, in a real process.',
      type: { session: talker, text: 'is the checkout test still flaky?', pace: 42 },
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
      body: ['This frame refuses the soft keyboard; open it full screen.'],
    },
    {
      at: 32_600,
      say: 'It answers badly, on purpose.',
      mutate: () => {
        scriptedTurn({
          id: talker,
          prompt: 'is the checkout test still flaky?',
          reply: [
            '● §Bash§(npm test -- checkout --repeat 40)  ¶⎿ 40 passed¶',
            '● Forty green runs. The flake was a fixed sleep, which is a bet on a',
            '  machine being idle, and this one never is.',
          ],
        })
      },
      body: ['The rail sends it; the pane is the CLI\'s own.'],
    },
    {
      at: 40_400,
      eyebrow: 'GESTURE',
      say: 'Swipe left for the side panel.',
      command: 'drawer.toggle',
      gesture: 'left',
      body: ['Notes, files, transcript, Git, alerts.'],
    },
    {
      at: 45_000,
      eyebrow: 'IT IS YOURS NOW',
      say: 'It is yours. Have a look.',
      command: 'drawer.close',
      body: ['Everything persists in this browser and nowhere else.'],
    },
  ]
}

// ----------------------------------------------------------- 2. the queued delivery

const QUEUED_PROMPT
  = 'when the tests are green, open a land request for this branch'

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
      eyebrow: 'THE CONTROL PLANE',
      say: 'A session is mid-turn. Watch what happens when it finishes.',
      click: sessionRow(target),
      spotlight: sessionRow(target),
    },
    {
      at: 1_400,
      say: 'A follow-up is queued behind the running turn. It does not interrupt it.',
      command: 'drawer.show:queue',
      spotlight: DRAWER_TAB('Queue'),
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
      say: 'Auto-delivery is switched on for this one session, inside the install-wide switch.',
      mutate: () => { apply({ kind: 'auto-delivery-set', id: target, enabled: true }) },
    },
    {
      at: 5_000,
      say: 'The turn ends.',
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
      mutate: () => {
        scriptedCompletion({
          id: target,
          ask: 'pull the coupon table out of the request path',
          reply: [
            '● §Bash§(npm test -- --runInBand)  ¶⎿ 214 passed, 0 failed¶',
            '● The table is loaded once at boot and the request path never touches',
            '  disk. p95 on the cart endpoint fell from 480ms to 11ms locally.',
            '● Nothing in production changed. The 40MB coupon file is still a',
            '  conversation for a human.',
          ],
        })
      },
    },
    {
      at: 9_800,
      say: 'The queued prompt delivers itself into the pane, as keystrokes, exactly as a person would have typed it.',
      mutate: () => {
        const queued = state.queue.find(item => item.body === QUEUED_PROMPT && item.state === 'armed')
        if (queued) {
          deliverQueuedMessage(queued.id, [
            '● §Bash§(git status --porcelain)  ¶⎿ clean¶',
            '● Tests are green and the checkout is clean, so the branch is landable.',
            '● §RequestLand§(agent/coupon-table)  ¶⎿ queued, one branch at a time¶',
          ])
        }
      },
    },
    {
      at: 15_500,
      say: 'And swe-mux tells you, rather than leaving you to notice.',
      mutate: () => {
        apply({
          kind: 'notification-add',
          notification: {
            id: demoId('note'),
            session_id: target,
            kind: 'queue_delivery',
            title: `Delivered a queued prompt to "${nameOf(target)}"`,
            message: 'The turn ended, the session went idle for eight seconds, and the head of its queue was sent.',
            severity: 'info',
            created_at: nowSeconds(),
          },
        })
      },
    },
    {
      at: 18_500,
      say: 'It arrives as an alert - ranked by what actually needs you, with every record kept behind it.',
      command: 'drawer.show:notifications',
      spotlight: DRAWER_TAB('Alerts'),
      show: { arrive: [DRAWER_TAB('Alerts')] },
    },
    { at: 22_000, say: 'That is the control plane: it watches, it waits, and it never acts on its own.' },
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
      eyebrow: 'AGENTS ASKING FOR AGENTS',
      say: 'One session, asked to split the work across two more.',
      click: sessionRow(lead),
      spotlight: sessionRow(lead),
    },
    {
      at: 1_200,
      type: { session: lead, text: 'split the coupon migration in two and report back', pace: 42 },
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
    },
    {
      at: 4_200,
      say: 'It cannot start a session. It can only ask for one.',
      mutate: () => {
        scriptedTurn({
          id: lead,
          prompt: 'split the coupon migration in two and report back',
          reply: [
            '● §Read§(src/coupons/)  ¶⎿ 31 files, two independent seams¶',
            '● The readers and the writers do not share a module, so this is two',
            '  branches rather than one with a merge conflict in the middle.',
            '● §RequestSpawn§(2 sessions)  ¶⎿ drafted, awaiting a human¶',
            '● I have asked for them. I cannot approve my own request.',
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
            name: 'coupon readers',
            prompt: 'move every coupon reader onto the cached table, keep the ids stable',
            reason: 'The reader seam is independent of the writer seam and can land on its own.',
          },
          {
            name: 'coupon writers',
            prompt: 'move the coupon writers behind the cache invalidation hook',
            reason: 'The writer seam needs the invalidation decision, which is the risky half.',
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
      say: 'Both requests land in the fleet queue, where a person decides.',
      command: 'queue.fleet',
      spotlight: ['[role="dialog"][aria-label="Fleet queue"]'],
      // A walk rather than a diagram, and only narrow targets. The card is nearly as wide
      // as the frame, so a shared gutter column would sit in the sixty pixels left over
      // beside it with leader lines running the whole way across; one label at a time is
      // placed against its own target and lands beside it.
      show: {
        reveal: 'walk',
        notes: [
          { at: ['.observation-request-tag'], label: 'a request', sub: 'not a session' },
          { at: ['.observation-request-status'], label: 'waiting on a person' },
          { at: ['.observation-request-actions .primary'], label: 'who decides here' },
        ],
      },
    },
    {
      at: 16_800,
      say: 'Approved. Now they start.',
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
      say: 'Two panes, in the same Project, standing in the same repository.',
      // The ids only exist because the previous beat made them, which is what the thunk
      // form of `show` is for. Stepped rather than eased: a row arriving in a fleet is a
      // terminal painting a line, not a panel sliding in.
      show: () => ({ arrive: spawned.map(id => sessionRow(id)) }),
    },
    {
      at: 20_800,
      say: 'They work, and they answer the session that asked - into its queue, not into its keyboard.',
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
              reason: 'reporting back on a split it was asked to take',
              body: index === 0
                ? 'Readers are done: 14 call sites moved to the cached table, ids unchanged, 214 tests green.'
                : 'Writers are blocked on one decision - who owns invalidation when the file is replaced on disk?',
            }),
          })
        }
      },
    },
    {
      at: 22_300,
      command: 'drawer.show:queue',
      spotlight: DRAWER_TAB('Queue'),
      click: sessionRow(lead),
    },
    {
      at: 25_300,
      say: 'Nothing was interrupted, nothing was auto-approved, and every step is on the record.',
    },
  ]
}

// ------------------------------------------------------------------- 4. the preview

/**
 * The most visual run, and about 80% of it already existed: a shell that answers commands
 * and a preview pane that loads a real static page. What is added is the join between
 * them - the daemon noticing a listener and offering it as a pane.
 */
function previewBeats(): Beat[] {
  const shell = 's-shell'
  return [
    {
      at: 0,
      eyebrow: 'A SHELL IS A SHELL',
      say: 'The pane beside the agents is an ordinary terminal.',
      click: sessionRow(shell),
      spotlight: sessionRow(shell),
    },
    {
      at: 1_400,
      type: { session: shell, text: 'npm run dev', submit: true, pace: 70 },
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
    },
    {
      at: 4_200,
      say: 'swe-mux watches what the session actually opened, rather than parsing what it printed.',
      command: 'drawer.show:processes',
      spotlight: DRAWER_TAB('Processes'),
    },
    {
      at: 7_000,
      say: 'A listener on the process tree is offered as a pane.',
      mutate: () => {
        createPreview({ session_id: shell })
      },
    },
    {
      at: 10_000,
      say: 'It is a pane like any other: split it, move it to a tab, send it to the phone.',
      command: 'drawer.close',
      // A walk rather than a diagram: there are two things to say and they are at
      // opposite ends of the screen, so holding the eye on each in turn beats drawing a
      // leader line the width of the frame.
      show: {
        reveal: 'walk',
        crt: true,
        notes: [
          { at: ['.preview-row'], label: 'the listener, as a row' },
          { at: ['.preview-frame'], label: 'and the page itself, as a pane' },
        ],
      },
    },
    { at: 13_800, say: 'The page is the one the session is serving. Nothing is proxied and nothing leaves the machine.' },
  ]
}

// ---------------------------------------------------------------------- 5. the land

/**
 * The land queue as a state machine rather than a finished row. Cheap, because the Git
 * fixtures already invent the repository this acts on, and worth having because reconcile
 * then verify then fast-forward is the whole argument for the surface.
 */
function landBeats(): Beat[] {
  const requester = 's-working'
  const id = 'land-demo-live'
  return [
    {
      at: 0,
      eyebrow: 'LANDING, QUEUED',
      say: 'One trunk, several checkouts, and one branch landing at a time.',
      command: 'drawer.show:git',
      spotlight: DRAWER_TAB('Git'),
    },
    {
      at: 2_000,
      say: 'The session standing in this checkout asks to land it. It never merges anything itself.',
      mutate: () => {
        if (state.lands.some(item => item.id === id)) apply({ kind: 'land-remove', id })
        apply({
          kind: 'land-add',
          request: makeLandRequest({
            id,
            projectId: DEMO_PROJECT_ID,
            branch: 'agent/coupon-table',
            worktreeRoot: DEMO_WORKTREE_COUPON,
            requestedBy: requester,
          }),
        })
      },
    },
    {
      at: 5_000,
      say: 'Reconcile first: trunk is merged into the branch, in the branch’s own worktree.',
      mutate: () => {
        apply({
          kind: 'land-patch', id,
          patch: { state: 'reconciling' },
          event: { state: 'reconciling', note: 'Merged master into agent/coupon-table cleanly.' },
        })
      },
    },
    {
      at: 8_000,
      say: 'Then the gate, on the reconciled tree rather than on what was pushed.',
      // The landing strip's three steps, which are drawn whether or not the section is
      // expanded - the expanded body is a scroll away and a callout must point at
      // something a visitor can see without one.
      show: {
        reveal: 'glitch',
        notes: [
          { at: ['.git-land-pipeline-step.gate'], label: 'the gate', sub: 'and who approved its bytes' },
          { at: ['.git-land-pipeline-step.run'], label: 'what is landing now' },
          { at: ['.git-land-pipeline-step.queue'], label: 'and what is behind it' },
        ],
      },
      mutate: () => {
        apply({
          kind: 'land-patch', id,
          patch: { state: 'verifying', verification: { kind: 'running', step: '.worktree-verify' } },
          event: { state: 'verifying', note: 'Running .worktree-verify against the reconciled tree.' },
        })
      },
    },
    {
      at: 12_500,
      say: 'Green, so the fast-forward is allowed. Red would have come back to the session that asked.',
      show: {
        reveal: 'blueprint',
        notes: [{ at: ['.git-landing-headline'], label: 'the branch, and where it got to' }],
        shimmer: [['.git-land-pipeline-step.run']],
      },
      mutate: () => {
        apply({
          kind: 'land-patch', id,
          patch: {
            state: 'landed',
            landed_at: nowSeconds(),
            verification: { kind: 'passed', reason: '4214 tests', duration_s: 47 },
          },
          event: { state: 'landed', note: 'Fast-forwarded master to agent/coupon-table.' },
        })
        apply({
          kind: 'notification-add',
          notification: {
            id: demoId('note'),
            session_id: requester,
            kind: 'land_completed',
            title: 'agent/coupon-table landed',
            message: 'Reconciled with master, verified in 47 seconds, fast-forwarded.',
            severity: 'info',
            created_at: nowSeconds(),
          },
        })
      },
    },
    { at: 16_000, say: 'A fast-forward is the only merge allowed outside a worktree, because it cannot lose work.' },
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
      eyebrow: 'ONE FIELD',
      say: 'Everything the app can do, by name.',
      command: 'palette.open',
      spotlight: ['.palette'],
      show: { keys: ['ctrl', 'shift', 'p'] },
    },
    {
      at: 1_500,
      say: 'Type what you want rather than remembering where it lives.',
      field: { at: input, text: 'transcript' },
    },
    {
      at: 4_400,
      say: 'The same field addresses four different things, and one character says which.',
      show: {
        reveal: 'walk',
        notes: [
          { at: paletteScope(1), label: 'commands', sub: '>' },
          { at: paletteScope(2), label: 'sessions', sub: '@' },
          { at: paletteScope(3), label: 'Projects', sub: '#' },
          { at: paletteScope(4), label: 'files', sub: ':' },
        ],
      },
    },
    {
      at: 11_800,
      say: 'Jumping to a session is navigation rather than a command, so it has its own scope.',
      click: paletteScope(2),
      field: { at: input, text: 'coupon', clear: true },
    },
    {
      at: 15_200,
      say: 'And every entry carries the chord that would have run it, in whichever keymap you chose.',
      show: {
        reveal: 'glitch',
        notes: [{ at: ['#command-results [role="option"] kbd'], label: 'your own binding' }],
      },
    },
    { at: 17_800, key: 'Escape', say: 'Nothing here is a second interface. It is the same commands.' },
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
      eyebrow: 'YOUR MUSCLE MEMORY',
      say: 'Five keymaps ship. Take the one your hands already know.',
      command: 'settings.open',
      spotlight: ['.settings-panel'],
    },
    {
      at: 1_600,
      say: 'They are documents the daemon owns, not a handful of chords the demo invented.',
      // Through the panel's own search rather than three presses down its nav. Both are
      // real paths a visitor has; this one is one act instead of three, and it does not
      // depend on the shape of a tab list that exists to be reorganised.
      field: { at: ['.settings-search input'], text: 'keyboard preset' },
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
          { at: card('.keymap-preset-count'), label: 'the whole table', sub: 'not a starter set' },
          { at: card('.keymap-warning'), label: 'and what it takes from the pane below' },
        ],
      },
      say: 'A prefix keymap is a real trade: swe-mux is the outer shell, so it takes the prefix first.',
    },
    {
      at: 8_600,
      say: 'Applying one replaces every binding, immediately.',
      click: card('.keymap-preset-apply'),
      show: { keys: ['ctrl', 'b', '→', 'o'], crt: true },
    },
    { at: 10_400, click: ['.keymap-confirm .primary'] },
    {
      at: 12_600,
      say: 'This is a browser tab, and a tab does not get every chord. So the table is resolved for one.',
      show: {
        reveal: 'sweep',
        sweep: ['.keybinding-policy', '.keymap-host'],
        notes: [
          { at: ['.keymap-host'], label: 'resolved for this host', sub: 'not for a hope' },
          { at: ['.keybinding-policy'], label: 'what the browser keeps', sub: 'and what it merely shares' },
        ],
      },
    },
    {
      at: 16_400,
      say: 'A chord a tab cannot receive is marked desktop-only in the preset itself, so it is never drawn as live here.',
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
      eyebrow: 'TALKING TO THE MULTIPLEXER',
      say: 'Ask the fleet something, out loud.',
      command: 'assistant.toggle',
      spotlight: ['.assistant-panel', '.voice-dock-anchor'],
      show: { keys: ['ctrl', 'shift', 'space', 'x', 'a'], crt: true },
    },
    {
      at: 1_800,
      say: 'The question is a sentence, not a command it had to be taught.',
      mutate: () => { assistantHeard('what is blocked right now?') },
    },
    {
      at: 3_400,
      mutate: () => {
        assistantSays(`One thing. "${nameOf(target)}" is waiting on a decision:`
          + ' who owns invalidation when a file is replaced on disk.')
      },
    },
    {
      at: 6_000,
      say: 'It answers from the fleet in front of you, not from a script it was given.',
      mutate: () => {
        assistantSays('Everything else is moving: two turns running, one branch already'
          + ' landed, nothing else waiting on you.')
      },
    },
    {
      at: 8_600,
      say: 'And anything it wants to do about that becomes a card, with a window to stop it.',
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
          { at: ['.assistant-action p strong'], label: 'about to, not doing' },
          { at: ['.assistant-countdown'], label: 'the window', sub: 'it runs when this ends' },
          { at: ['.assistant-action .cancel'], label: 'and the way out of it' },
        ],
      },
    },
    {
      at: 13_000,
      say: 'Left alone, it runs - and what it does is put a prompt in a queue, not keystrokes in a pane.',
      mutate: () => {
        if (proposed) assistantResolved(proposed, `Queued one prompt for "${nameOf(target)}".`)
        assistantDone()
        apply({
          kind: 'queue-add',
          message: makeQueueMessage({
            targetSessionId: target,
            senderKind: 'agent',
            senderLabel: 'the assistant',
            body: 'the file owner invalidates; treat a replaced file as a write',
            reason: 'answering a question you asked out loud',
            state: 'armed',
          }),
        })
      },
      show: { shimmer: [rowField(target, 'queue')], arrive: [DRAWER_TAB('Queue')] },
    },
    {
      at: 17_000,
      say: 'The listening half needs a microphone, so this page does not ask for one. Everything above it is real.',
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
    label: 'Take the tour of the whole interface',
    blurb: 'A hands-off walk around the whole app. Touch anything to take over.',
    // The one run whose card follows the chrome and counts its stops. Every other entry
    // is a caption at the foot of the frame: they narrate a thing happening rather than
    // walk somebody through a surface.
    card: 'anchored',
    beats: tourDesktop(),
    mobileBeats: tourMobile(),
  },
  {
    id: 'queue',
    label: 'Queue a prompt behind a running turn',
    blurb: 'A turn ends, a waiting prompt is sent, and swe-mux says so.',
    prepare: () => { reopenTurn('s-working') },
    beats: queueBeats(),
  },
  {
    id: 'orchestrate',
    label: 'Let an agent ask for two more, and approve it',
    blurb: 'One session drafts the requests, a human decides, and they report back.',
    beats: orchestrateBeats(),
  },
  {
    id: 'preview',
    label: 'Open your dev server as a pane',
    blurb: 'A shell starts one and its listener becomes a preview beside the terminal.',
    beats: previewBeats(),
  },
  {
    id: 'land',
    label: 'Land a worktree branch on the trunk',
    blurb: 'Reconcile, verify, fast-forward - one branch at a time.',
    beats: landBeats(),
  },
  {
    id: 'palette',
    label: 'Find any command or session by name',
    blurb: 'One field, four scopes, and the chord beside every answer.',
    beats: paletteBeats(),
  },
  {
    id: 'keymap',
    label: 'Bring your tmux keybindings with you',
    blurb: 'Five presets, applied live, and which chords a browser tab keeps.',
    beats: keymapBeats(),
  },
  {
    id: 'voice',
    label: 'Ask the fleet what is blocked',
    blurb: 'A spoken question, answered from the live fleet, ending in a card you resolve.',
    beats: voiceBeats(),
  },
]

export const scenarioById = (id: string): Scenario | undefined =>
  SCENARIOS.find(item => item.id === id)

/** The one the idle nudge plays. The cheapest, the shortest, and the one that shows the
 *  half of the product a visitor cannot discover by clicking around. */
export const NUDGE_SCENARIO_ID = 'queue'

export { DRAWER_TAB, sessionRow }
