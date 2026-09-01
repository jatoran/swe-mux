/**
 * One director, driving the demo.
 *
 * There used to be a walkthrough and nothing else, and the moment a second thing wanted
 * to move the interface the obvious design was two of them. It is the wrong one: a
 * visitor cannot tell a tour from a script - both are "something is driving the UI" - and
 * two drivers on one screen do not take turns, they interleave. So there is one runner,
 * the tour is its first scenario (`scenarios.ts`), and everything below is true of both.
 *
 * **It runs in exactly one frame.** The demo can be embedded twice, desktop beside phone,
 * and two copies of a script racing the same fixture drift within a beat or two and look
 * broken. The existing leader election in `mirror.ts` decides which frame runs; the view
 * mirror then carries the winner's acts to the other, which demonstrates more than a
 * second copy would.
 *
 * **Nothing plays by itself except one nudge.** The demo's pitch is "this is the real app,
 * touch it", and something moving on its own converts that into "wait and watch". A
 * script that fights a click is worse than no script. So scenarios are chosen from the
 * page's own menu, with a single exception: a demo that has been on screen and untouched
 * for ten seconds plays one short scenario, once, and **any** real pointerdown, keydown
 * or touch ends it instantly and permanently for that visit.
 *
 * **Highlights are for scripted acts only.** A ghost cursor, a ripple and a caption are
 * essential when the script presses something, because otherwise the interface appears to
 * move on its own. They are noise when the visitor presses something - people can see
 * their own cursor - so real input draws nothing, except under `?highlightInput=1`, which
 * only the capture rig sets.
 */
import { apply, state } from './store.ts'
import type { Show } from './callouts.ts'
import { DEMO_EPOCH_MS, DEMO_SEED, DETERMINISTIC } from './determinism.ts'
import {
  clearField, clickFirst, delay, firstVisible, focusField, narrow, runCommand, typeCharacter,
} from './drive.ts'
import { releaseDirectorLead, requestDirectorLead } from './mirror.ts'
import {
  NUDGE_SCENARIO_ID, SCENARIOS, scenarioById, type Beat, type Gate, type Scenario,
} from './scenarios.ts'

/** Where the walkthrough records that it is finished. Unchanged from the coach's key, so
 *  a returning visitor is not toured again by a rename. */
const DONE_KEY = 'swemux-demo-coach-v1'
const DONE = 'done'

/** Wait for the app's first real paint before the tour measures anything. The fleet
 *  arrives a fetch after mount, and a spotlight measured before then lands on nothing. */
const TOUR_START_MS = 2_600

/** How long a visible, untouched demo waits before the nudge. */
const IDLE_NUDGE_MS = 10_000

/** The ghost cursor's travel, then the pause on the control before the act. Long enough
 *  to read as a deliberate press, short enough not to pad a twenty-second scenario. */
const TRAVEL_MS = 420
const PRESS_MS = 170

/** A horizontal drag this long counts as a swipe, for the phone tour's gestures. */
const SWIPE_MIN = 55

const params = (): URLSearchParams => {
  try { return new URLSearchParams(location.search) } catch { return new URLSearchParams() }
}

/** Draw a marker where the *visitor* pressed. Capture only: see the header. */
export const HIGHLIGHT_INPUT = params().get('highlightInput') === '1'

export type DirectorSnapshot = {
  running: boolean
  scenarioId: string
  blurb: string
  /** 1-based, for the card's counter. */
  index: number
  total: number
  eyebrow: string
  say: string
  body: string[]
  hint: string
  gesture: 'left' | 'right' | null
  spotlight: string[] | null
  /** Whether the current beat is waiting for the visitor, and how. */
  gate: Gate | null
  /** Where the ghost cursor is, or null when it is not on screen. */
  pointer: { x: number; y: number } | null
  /** Bumped on every scripted press, so the view can key one ripple per act. */
  press: number
  /** Bumped on every *real* press while `HIGHLIGHT_INPUT` is on. */
  echo: { x: number; y: number; seq: number } | null
  /**
   * Everything this beat draws over the app besides its one ring: callouts, the radar
   * sweep, the keycap HUD, shimmers, arrival marks, scanlines. The runner carries it and
   * never measures it - all of that is geometry, and geometry belongs to the view.
   */
  show: Show | null
  /**
   * Bumped once per beat that carries a `show`.
   *
   * The view keys its chips off this rather than off the `show` object, so a beat that
   * repeats the previous beat's callouts still replays their reveal. Without it, two
   * consecutive identical shows would draw once and look like a frozen overlay.
   */
  showSeq: number
}

const EMPTY: DirectorSnapshot = {
  running: false, scenarioId: '', blurb: '', index: 0, total: 0,
  eyebrow: '', say: '', body: [], hint: '', gesture: null, spotlight: null,
  gate: null, pointer: null, press: 0, echo: null, show: null, showSeq: 0,
}

let snapshot: DirectorSnapshot = EMPTY
const listeners = new Set<(view: DirectorSnapshot) => void>()

export function subscribeDirector(listener: (view: DirectorSnapshot) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export const directorSnapshot = (): DirectorSnapshot => snapshot

function publish(patch: Partial<DirectorSnapshot>): void {
  snapshot = { ...snapshot, ...patch }
  for (const listener of listeners) listener(snapshot)
}

// ---------------------------------------------------------------------- the runner

/**
 * Which run is current.
 *
 * Every await in the runner checks it on the way back, which is the whole abort
 * mechanism: stopping increments the token, and every timer already in flight returns
 * false and unwinds instead of writing into a run nobody is watching. A cancellation flag
 * would have needed the same check anyway, and a token also makes a *restart* safe.
 */
let token = 0
/** Resolves the open gate, or null when nothing is gated. */
let openGate: ((satisfied: boolean) => void) | null = null
/** Set once the nudge has played or been refused, for the life of this page. */
let nudgeSpent = false
let lastRealInput = 0
/** When the demo came into view, or 0 while it is out of it. The nudge's countdown runs
 *  from the later of this and the last real input, so scrolling past does not spend it. */
let visibleSince = 0

const tourDone = (): boolean => {
  try { return localStorage.getItem(DONE_KEY) === DONE } catch { return true }
}

const markTourDone = (): void => {
  try { localStorage.setItem(DONE_KEY, DONE) } catch { /* private mode */ }
}

/** Sleep, and report whether the run that started it is still the current one. */
async function pause(ms: number): Promise<boolean> {
  const mine = token
  if (ms > 0) await delay(ms)
  return mine === token
}

/** Move the ghost cursor onto a control and press it, so a scripted act is legible as
 *  one. Returns false if the run was abandoned mid-gesture. */
async function pressAt(element: HTMLElement | null, act: () => void): Promise<boolean> {
  if (!element) { act(); return true }
  const rect = element.getBoundingClientRect()
  publish({ pointer: { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } })
  if (!(await pause(TRAVEL_MS))) return false
  publish({ press: snapshot.press + 1 })
  if (!(await pause(PRESS_MS))) return false
  act()
  return true
}

/** Type into a pane the way a visitor does: one keystroke at a time, through the same
 *  mutation the real keyboard produces, so the composer fills in every attached frame. */
async function typeInto(
  input: { session: string; text: string; submit?: boolean; pace?: number },
): Promise<boolean> {
  const pace = input.pace ?? 46
  for (const character of input.text) {
    apply({ kind: 'term-input', id: input.session, data: character })
    if (!(await pause(pace))) return false
  }
  if (input.submit) {
    if (!(await pause(320))) return false
    apply({ kind: 'term-input', id: input.session, data: '\r' })
  }
  return true
}

/**
 * Type into a field of the app's own chrome, one character at a time.
 *
 * Separate from `typeInto` because they are different destinations, not different paces:
 * that one sends keystrokes to a pane through the fake PTY, this one drives a controlled
 * input. Sharing a helper would mean one of the two lying about where its characters
 * went.
 */
async function typeIntoField(
  input: { at: string[]; text: string; pace?: number; clear?: boolean },
): Promise<boolean> {
  const field = focusField(input.at)
  if (!field) return true
  if (input.clear) clearField(field)
  const pace = input.pace ?? 58
  for (const character of input.text) {
    typeCharacter(field, character)
    if (!(await pause(pace))) return false
  }
  return true
}

async function perform(beat: Beat): Promise<boolean> {
  // The daemon's half first: a beat that both mutates and presses is describing an act
  // whose consequence the press then reveals, never the other way round.
  beat.mutate?.()
  if (beat.type && !(await typeInto(beat.type))) return false
  if (beat.command) {
    const ok = await pressAt(firstVisible(beat.spotlight), () => runCommand(beat.command!))
    if (!ok) return false
  }
  if (beat.click) {
    const target = firstVisible(beat.click)
    const ok = await pressAt(target, () => { if (target) target.click(); else clickFirst(beat.click!) })
    if (!ok) return false
  }
  // After the press, because the field a beat types into is usually the one the press
  // just opened.
  if (beat.field && !(await typeIntoField(beat.field))) return false
  if (beat.key === 'Escape') {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  }
  publish({ pointer: null })
  return true
}

/**
 * Wait for the visitor to satisfy a gate.
 *
 * Every listener is passive and capture-phase: the director observes the real interaction
 * and must never consume it. That is the walkthrough's original contract and it is what
 * separates a gate from a scripted act - the gate does not simulate anything, it waits.
 */
function waitForGate(gate: Gate): Promise<boolean> {
  return new Promise(resolve => {
    const mine = token
    let cleanup = (): void => {}
    const settle = (satisfied: boolean): void => {
      cleanup()
      openGate = null
      resolve(satisfied && mine === token)
    }
    openGate = settle

    if (gate.kind === 'next') return
    if (gate.kind === 'event') {
      const handler = (): void => settle(true)
      window.addEventListener(gate.name, handler)
      cleanup = () => window.removeEventListener(gate.name, handler)
      return
    }
    if (gate.kind === 'click') {
      const handler = (event: MouseEvent): void => {
        const target = event.target instanceof Element ? event.target : null
        // A microtask, so the app's own handler for this click runs first and the next
        // beat measures the chrome the click produced rather than the old one.
        if (target && gate.selectors.some(selector => target.closest(selector))) {
          queueMicrotask(() => settle(true))
        }
      }
      document.addEventListener('click', handler, true)
      cleanup = () => document.removeEventListener('click', handler, true)
      return
    }
    let origin: { x: number; y: number } | null = null
    const down = (event: PointerEvent): void => { origin = { x: event.clientX, y: event.clientY } }
    const up = (event: PointerEvent): void => {
      if (!origin) return
      const dx = event.clientX - origin.x
      const dy = event.clientY - origin.y
      origin = null
      if (Math.abs(dx) < SWIPE_MIN || Math.abs(dy) > Math.abs(dx)) return
      if ((gate.direction === 'right') === (dx > 0)) settle(true)
    }
    document.addEventListener('pointerdown', down, true)
    document.addEventListener('pointerup', up, true)
    cleanup = () => {
      document.removeEventListener('pointerdown', down, true)
      document.removeEventListener('pointerup', up, true)
    }
  })
}

async function play(scenario: Scenario, beats: Beat[]): Promise<void> {
  const mine = token
  scenario.prepare?.()
  let elapsed = 0
  for (const [index, beat] of beats.entries()) {
    if (!(await pause(Math.max(0, beat.at - elapsed)))) return
    elapsed = Math.max(elapsed, beat.at)
    const show = typeof beat.show === 'function' ? beat.show() : beat.show
    publish({
      index: index + 1,
      total: beats.length,
      eyebrow: beat.eyebrow ?? snapshot.eyebrow,
      say: beat.say ?? (beat.body ? '' : snapshot.say),
      body: beat.body ?? [],
      hint: beat.hint ?? '',
      gesture: beat.gesture ?? null,
      spotlight: beat.spotlight ?? null,
      gate: beat.gate ?? null,
      pointer: null,
      show: show ?? null,
      showSeq: show ? snapshot.showSeq + 1 : snapshot.showSeq,
    })
    if (!(await perform(beat))) return
    if (beat.gate) {
      const satisfied = await waitForGate(beat.gate)
      if (!satisfied || mine !== token) return
      publish({ gate: null })
    }
  }
  if (mine !== token) return
  if (scenario.id === 'tour') markTourDone()
  stop('finished')
}

// ------------------------------------------------------------------------- control

/** End whatever is playing. Idempotent, and the only way a run ever stops. */
export function stop(reason: 'finished' | 'interrupted' | 'dismissed' | 'replaced'): void {
  if (!snapshot.running && reason !== 'dismissed') return
  token += 1
  openGate?.(false)
  openGate = null
  if (reason === 'dismissed' && snapshot.scenarioId === 'tour') markTourDone()
  releaseDirectorLead()
  publish({ ...EMPTY, echo: snapshot.echo, press: snapshot.press, showSeq: snapshot.showSeq })
}

/**
 * Play a scenario, in this frame if it wins the election.
 *
 * The election is re-run per start rather than held: a demo shown alone must win it
 * instantly (nobody answers), and a demo shown twice must be able to hand the lead over
 * when the visitor asks for a second scenario minutes later.
 */
export async function start(scenarioId: string): Promise<boolean> {
  const scenario = scenarioById(scenarioId)
  if (!scenario) return false
  stop('replaced')
  const mobile = narrow()
  if (!(await requestDirectorLead(mobile))) return false
  token += 1
  const beats = mobile && scenario.mobileBeats ? scenario.mobileBeats : scenario.beats
  publish({
    ...EMPTY,
    running: true,
    scenarioId: scenario.id,
    blurb: scenario.blurb,
    total: beats.length,
    press: snapshot.press,
    showSeq: snapshot.showSeq,
  })
  void play(scenario, beats)
  return true
}

/** The card's Next button, and its "skip this step". Both resolve the open gate; there is
 *  no separate skip path, because "the visitor moved on" is one thing however it happened. */
export function advanceGate(): void {
  openGate?.(true)
}

export const scenarioMenu = (): Array<{ id: string; label: string }> =>
  SCENARIOS.map(item => ({ id: item.id, label: item.label }))

/**
 * A stable reading of everything a scenario is allowed to have changed.
 *
 * This is the capture rig's determinism oracle, and it is deliberately a projection of
 * the *store* rather than of the pixels: two runs of the same scenario at the same seed
 * must produce the same ids, the same timestamps, the same queue and the same land trail,
 * and that is a claim a diff can settle. Comparing screenshots instead would fail on a
 * one-frame difference in a cursor blink and prove nothing about the fixture.
 */
export function demoFingerprint(): string {
  return JSON.stringify({
    deterministic: DETERMINISTIC,
    epoch: DEMO_EPOCH_MS,
    seed: DEMO_SEED,
    sessions: state.sessions.map(item => [item.id, item.created_at, item.pid, item.state, item.name]),
    previews: state.previews.map(item => item.id),
    queue: state.queue.map(item => [item.id, item.state, item.sender_kind, item.body]),
    spawnRequests: state.spawnRequests.map(item => [item.id, item.status, item.name]),
    lands: state.lands.map(item => [item.id, item.state, item.events.length]),
    notifications: state.notifications.map(item => [item.id, item.kind, item.title]),
    autoDelivery: [...state.autoDelivery].sort(),
  })
}

// -------------------------------------------------------------------- installation

/**
 * Wire the director to the page.
 *
 * Called once from `main.tsx`. Everything it installs is passive: the abort listeners are
 * capture-phase and `passive: true`, so they can observe a real interaction without ever
 * being able to swallow it.
 */
export function installDirector(): void {
  /**
   * The capture rig's handle on the running demo.
   *
   * Only the demo build evaluates this module, so nothing here can reach the product
   * bundle. It exists because the alternative for a headless driver is guessing: sleeping
   * for "about long enough" and shooting, which produces a capture that is wrong whenever
   * the machine is busy and gives no way to say which beat a still belongs to.
   */
  ;(window as unknown as Record<string, unknown>).__demoDirector = {
    start, stop, scenarios: scenarioMenu, snapshot: directorSnapshot,
    fingerprint: demoFingerprint,
  }

  const onRealInput = (event: Event): void => {
    // `isTrusted` is the discriminator, and it is exact: a scripted press is
    // `element.click()`, which is untrusted and produces no `pointerdown` at all, so a
    // scenario can never abort itself. Anything reaching here came from a person.
    if (!event.isTrusted) return
    lastRealInput = Date.now()
    if (HIGHLIGHT_INPUT && event instanceof PointerEvent) {
      publish({ echo: { x: event.clientX, y: event.clientY, seq: (snapshot.echo?.seq ?? 0) + 1 } })
    }
    // The nudge is spent by the first touch whether or not it was playing: a visitor who
    // arrived and started clicking has already answered the question the nudge asks.
    nudgeSpent = true
    if (!snapshot.running) return
    const scenario = scenarioById(snapshot.scenarioId)
    // A gated scenario is *waiting* for exactly this. Aborting on it would make the
    // walkthrough impossible to finish.
    if (scenario?.interruptible) stop('interrupted')
  }
  for (const name of ['pointerdown', 'keydown', 'touchstart']) {
    document.addEventListener(name, onRealInput, { capture: true, passive: true })
  }

  // The page asks for a scenario by name; the frames are same-origin, so the embed's own
  // menu dispatches this into both of them and the election picks one.
  window.addEventListener('swemux-demo:scenario', event => {
    const requested = (event as CustomEvent<string>).detail
    void start(typeof requested === 'string' && requested ? requested : 'tour')
  })
  window.addEventListener('swemux-demo:stop', () => stop('dismissed'))
  // The landing page reports when the demo is actually on screen, because a frame cannot
  // see its own position in the parent document: an IntersectionObserver inside an iframe
  // measures against the iframe's viewport, not the page's, and would say "visible" for a
  // demo eight screens below the fold.
  window.addEventListener('swemux-demo:visible', () => {
    if (!visibleSince) visibleSince = Date.now()
  })
  /**
   * A frame nobody can see gives the lead back.
   *
   * The election is what stops two frames driving one fixture, and it works by the leader
   * answering every rival claim with "taken" for as long as it is running. That is right
   * while both frames are on screen and wrong the moment one of them is not: switching the
   * embed from the desktop view to the phone view leaves the desktop frame alive,
   * `display: none`, still mid-scenario and still answering - so the phone frame claims,
   * loses, and the visitor's scenario silently does nothing. Measured on the live site:
   * the desktop frame ran on to `queue 5/8` while the phone frame sat at `0/0`.
   *
   * Stopping is the whole fix, because `stop` releases the lead. It also covers the second
   * shape of the same bug, which is worse to debug: the channel is per origin, not per
   * page, so a forgotten second tab of the site held the lead over the tab being used.
   */
  window.addEventListener('swemux-demo:hidden', () => {
    visibleSince = 0
    stop('interrupted')
  })

  const requested = params().get('scenario')
  if (requested) {
    // The capture rig's entry point. Deliberately not gated on the tour marker or on
    // visibility: a headless run has no visitor to interrupt and no page around it.
    window.setTimeout(() => { void start(requested) }, TOUR_START_MS)
    return
  }

  if (!tourDone()) {
    window.setTimeout(() => { void start('tour') }, TOUR_START_MS)
    return
  }

  // The one exception to "nothing plays by itself".
  const tick = window.setInterval(() => {
    if (nudgeSpent || snapshot.running) return
    if (!visibleSince || document.visibilityState !== 'visible') return
    // From the later of "it came into view" and "they last touched it", so a visitor who
    // scrolled past and came back gets the full ten seconds rather than an instant start.
    if (Date.now() - Math.max(visibleSince, lastRealInput) < IDLE_NUDGE_MS) return
    nudgeSpent = true
    window.clearInterval(tick)
    void start(NUDGE_SCENARIO_ID)
  }, 1_000)
}
