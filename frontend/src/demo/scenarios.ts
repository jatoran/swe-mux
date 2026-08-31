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
 * It is a `.ts` and its copy is plain strings on purpose. The obvious first cut had the
 * walkthrough's longer cards as JSX, which made this a `.tsx` - and Node's type stripping
 * does not handle JSX, so the unit suite could not import the catalogue at all and the
 * invariants below (unique ids, ordered beats, a gate on every walkthrough beat) had
 * nothing asserting them. Markup in a data file bought nothing and cost the tests.
 */
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

/** What a gated beat waits for. The walkthrough is built entirely out of these: it never
 *  simulates the act, it waits for the visitor to perform it against the real interface. */
export type Gate =
  /** Any real click landing inside one of these selectors. */
  | { kind: 'click'; selectors: string[] }
  /** A horizontal drag of at least `SWIPE_MIN` px in this direction, anywhere. */
  | { kind: 'swipe'; direction: 'left' | 'right' }
  /** A window event the app already raises. */
  | { kind: 'event'; name: string }
  /** The card's own Next button. */
  | { kind: 'next' }

export type Beat = {
  /**
   * Milliseconds from the start of the scenario's clock.
   *
   * The clock *pauses* while a gate is open, which is what lets one shape serve both a
   * timed scenario (no gates, so `at` is simply the timeline) and the walkthrough (all
   * gates, so `at` is the pause before each card appears).
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
  /** Wait for the visitor rather than for the clock. */
  gate?: Gate
  /** The hint under a gated card. */
  hint?: string
  /** Draw the swipe glyph over the spotlight, pointing this way. */
  gesture?: 'left' | 'right'
}

export type Scenario = {
  id: string
  /** What the page's dropdown calls it. */
  label: string
  /** One line, shown while it plays. */
  blurb: string
  /**
   * Whether a real pointerdown or keypress ends it.
   *
   * True for anything that plays by itself, because a script fighting a click is worse
   * than no script. False for the walkthrough, whose gates *are* real input - aborting on
   * the act it is waiting for would make it impossible to finish.
   */
  interruptible: boolean
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
 * The walkthrough, unchanged in intent: flash one piece of real chrome at a time and hand
 * over the moment the visitor does the thing. It is a scenario now rather than a separate
 * component, which is what stops it and a scripted scenario from driving one screen at
 * once - and it is the only entry whose beats are all gates.
 */
function tourDesktop(): Beat[] {
  return [
    {
      at: 0,
      eyebrow: 'THE REAL INTERFACE',
      say: 'This is the actual app, running on a fake daemon.',
      gate: { kind: 'next' },
      body: [
        'Every pane, panel and menu below is the shipped frontend. The sessions are'
        + ' invented and the agents only tell jokes, but nothing else is a mock-up.',
        'Eight quick stops. Do the thing each one asks and it moves on by itself.',
      ],
    },
    {
      at: 200,
      eyebrow: 'THE FLEET',
      say: 'Every session, in one column.',
      spotlight: ['.sidebar'],
      gate: { kind: 'click', selectors: ['.session-row'] },
      hint: 'Click any session to focus its pane',
      body: [
        'Projects group sessions; each row carries live state, the model, how long the'
        + ' current turn has been running, and what its checkout looks like.',
        'Click one - the workspace focuses that pane.',
      ],
    },
    {
      at: 200,
      eyebrow: 'THE COMMAND RAIL',
      say: 'The keys a terminal cannot send.',
      spotlight: ['.terminal-action-rail'],
      gate: { kind: 'click', selectors: ['.terminal-action-rail'] },
      hint: 'Press anything on the rail',
      body: [
        'Escape, Ctrl-C, arrow keys, paste, approve, the model picker, the prompt'
        + ' library - one editable strip under every pane, on desktop and on a phone.',
        'Press one. Nothing here can break anything.',
      ],
    },
    {
      at: 200,
      eyebrow: 'TALK TO IT',
      say: 'Type into the agent and press Enter.',
      spotlight: ['.terminal-pane.focused', '.terminal-pane'],
      gate: { kind: 'event', name: 'mux:turn-ended' },
      hint: 'Type anything, then Enter',
      body: [
        'The composer is the CLI\'s own, drawn by the CLI. swe-mux is the multiplexer'
        + ' around it, not a chat window bolted on top.',
        'Ask it something. It will answer badly, on purpose.',
      ],
    },
    {
      at: 200,
      eyebrow: 'THE SIDE PANEL',
      say: 'Read the conversation beside the terminal.',
      spotlight: DRAWER_TAB('Transcript'),
      gate: { kind: 'click', selectors: DRAWER_TAB('Transcript') },
      hint: 'Open Transcript',
      body: [
        'The same turn you just drove, merged into readable messages with the tool calls'
        + ' between them - searchable, copyable, and never scrolled away.',
        'Activity, beside it, is the behavioural timeline of the same run.',
      ],
    },
    {
      at: 200,
      eyebrow: 'THE REPOSITORY',
      say: 'Worktrees, commits, and who made them.',
      spotlight: DRAWER_TAB('Git'),
      gate: { kind: 'click', selectors: DRAWER_TAB('Git') },
      hint: 'Open Git',
      body: [
        'Map is one row per checkout with its changes and the sessions standing in it.'
        + ' Log is the real commit graph. Provenance connects each commit back to the'
        + ' session and run that produced it.',
      ],
    },
    {
      at: 200,
      eyebrow: 'THE MACHINE',
      say: 'What the fleet is actually consuming.',
      // The summary button by its own class rather than "a button in the footer": the
      // footer carries several now, and any of them would have satisfied the step.
      spotlight: ['.resource-usage-summary', '.sidebar-footer'],
      gate: { kind: 'click', selectors: ['.resource-usage-summary'] },
      hint: 'Open the resource summary',
      body: [
        'Processes, listeners, bandwidth, disk, and the durable telemetry behind them -'
        + ' per session, per Project, and for swe-mux itself.',
      ],
    },
    {
      at: 200,
      eyebrow: 'IT IS YOURS NOW',
      say: 'Break it however you like.',
      gate: { kind: 'next' },
      body: [
        'Spawn panes, split them, kill them, edit notes, change the keymap, switch the'
        + ' theme. Everything persists in this browser and nowhere else.',
        'The scenarios menu above the frame replays this, and plays four scripted runs'
        + ' through the control plane.',
      ],
    },
  ]
}

function tourMobile(): Beat[] {
  return [
    {
      at: 0,
      eyebrow: 'THE REAL INTERFACE',
      say: 'The phone layout, not a screenshot of one.',
      gate: { kind: 'next' },
      body: [
        'This is the same frontend the desktop runs, laid out for a thumb. The sessions'
        + ' are invented; the interface is not.',
        'Six stops, and two of them are gestures worth knowing.',
      ],
    },
    {
      at: 200,
      eyebrow: 'GESTURE',
      say: 'Swipe right to reach the fleet.',
      gate: { kind: 'swipe', direction: 'right' },
      gesture: 'right',
      hint: 'Swipe right across the terminal',
      body: [
        'Both panels live off the edges of the screen. Swipe right anywhere over the'
        + ' terminal and the session list comes in.',
      ],
    },
    {
      at: 200,
      eyebrow: 'GESTURE',
      say: 'Swipe left for the side panel.',
      gate: { kind: 'swipe', direction: 'left' },
      gesture: 'left',
      hint: 'Swipe left across the terminal',
      body: [
        'The other direction opens notes, the transcript, Git, and alerts. Two fingers'
        + ' moves between tabs; every gesture is rebindable in Settings.',
      ],
    },
    {
      at: 200,
      eyebrow: 'THE COMMAND RAIL',
      say: 'The keys a phone keyboard does not have.',
      spotlight: ['.terminal-action-rail'],
      gate: { kind: 'click', selectors: ['.terminal-action-rail'] },
      hint: 'Press anything on the rail',
      body: [
        'Escape, Ctrl-C, arrows, approve, paste. This strip is the reason a phone can'
        + ' actually drive an agent CLI rather than just watch one.',
      ],
    },
    {
      at: 200,
      eyebrow: 'TALK TO IT',
      say: 'Type something and send it.',
      spotlight: ['.terminal-pane'],
      gate: { kind: 'event', name: 'mux:turn-ended' },
      hint: 'Type anything, then send',
      body: [
        'The agent replies badly, on purpose. The Transcript tab in the side panel will'
        + ' have the same turn, as readable messages.',
      ],
    },
    {
      at: 200,
      eyebrow: 'IT IS YOURS NOW',
      say: 'Have a look around.',
      gate: { kind: 'next' },
      body: [
        'Everything persists in this browser and nowhere else. The scenarios menu above'
        + ' the frame replays this and plays four scripted runs.',
      ],
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
    },
    {
      at: 14_500,
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
    { at: 16_500, key: 'Escape', say: 'Two panes, in the same Project, standing in the same repository.' },
    {
      at: 18_500,
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
      at: 20_000,
      command: 'drawer.show:queue',
      spotlight: DRAWER_TAB('Queue'),
      click: sessionRow(lead),
    },
    {
      at: 23_000,
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
    },
    { at: 13_000, say: 'The page is the one the session is serving. Nothing is proxied and nothing leaves the machine.' },
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

// ------------------------------------------------------------------- the catalogue

export const SCENARIOS: Scenario[] = [
  {
    id: 'tour',
    label: 'replay the tour',
    blurb: 'A short hands-on walk around the interface.',
    interruptible: false,
    beats: tourDesktop(),
    mobileBeats: tourMobile(),
  },
  {
    id: 'queue',
    label: 'a queued prompt delivers',
    blurb: 'A turn ends, a waiting prompt is sent, and swe-mux says so.',
    interruptible: true,
    prepare: () => { reopenTurn('s-working') },
    beats: queueBeats(),
  },
  {
    id: 'orchestrate',
    label: 'an agent asks for agents',
    blurb: 'One session drafts two more, a human approves, and they report back.',
    interruptible: true,
    beats: orchestrateBeats(),
  },
  {
    id: 'preview',
    label: 'a served page becomes a pane',
    blurb: 'A shell starts a dev server and its listener opens as a preview.',
    interruptible: true,
    beats: previewBeats(),
  },
  {
    id: 'land',
    label: 'a branch lands through the queue',
    blurb: 'Reconcile, verify, fast-forward - one branch at a time.',
    interruptible: true,
    beats: landBeats(),
  },
]

export const scenarioById = (id: string): Scenario | undefined =>
  SCENARIOS.find(item => item.id === id)

/** The one the idle nudge plays. The cheapest, the shortest, and the one that shows the
 *  half of the product a visitor cannot discover by clicking around. */
export const NUDGE_SCENARIO_ID = 'queue'

export { DRAWER_TAB, sessionRow }
