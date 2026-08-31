/**
 * The demo's cross-frame view mirror: act on the desktop, watch the phone follow.
 *
 * `store.ts` already mirrors the *fleet* - sessions, layouts, notes, config, terminal
 * bytes - because all of that is the fake daemon's state and both frames read the same
 * copy. What it cannot mirror is everything the app keeps in its own head: which modal
 * is open, whether the navigation sidebar is out, which side-panel tab is selected,
 * which session is focused. Those are per-frame, so the "both" view used to be two
 * independent apps sharing a database.
 *
 * The mechanism is deliberately a **state** mirror rather than an event mirror. Each
 * frame reads its own view state out of the DOM it already renders, broadcasts it, and
 * a frame receiving someone else's drives itself toward it using the app's own command
 * bus (`mux:command`) and its own controls. That matters for three reasons:
 *
 *  - It is idempotent. Converging on a state cannot double-apply, whereas replaying a
 *    click could spawn two sessions out of one press.
 *  - It is indifferent to how the act happened. A modal opened by a menu row, a
 *    keyboard chord, the command palette or a voice phrase all read the same way here.
 *  - It degrades to nothing. A surface this module does not know how to open simply is
 *    not mirrored; it never closes something it cannot reopen.
 *
 * Pane geometry is out of scope on purpose, and needs nothing: the pane tree lives in
 * the Project record, so a split made on the desktop is already in the phone's copy -
 * it just draws it as a tab rail, which is what the phone layout is *for*.
 */
import { trueRandom } from './determinism.ts'
import {
  clickProject, clickSession, clickTab, delay, mirrorableTablist, narrow,
  pressEscape, runCommand as run, text, visible,
} from './drive.ts'

const CHANNEL_NAME = 'swemux-demo-view-v1'
/** The real entropy source even under determinism, like the store's frame id: two frames
 *  that minted the same identity would each discard everything the other said. */
const FRAME_ID = `view-${trueRandom().toString(36).slice(2, 10)}`

/** One correction per tick, then look again: an act can change more than it names. */
const SETTLE_MS = 140
/** Convergence is bounded rather than trusted. A state this frame cannot reach (a
 *  Project row inside a collapsed group, a modal with no command) must cost a handful
 *  of frames and then stop, not spin. */
const MAX_STEPS = 12
/**
 * How long after applying a remote state this frame stays quiet.
 *
 * Without it the follower would observe the change it was just told to make and
 * broadcast it back as though it were the visitor's, and the two frames would trade
 * the same state forever. Long enough to cover a converge pass, short enough that a
 * visitor who turns to the other frame is not locked out of it.
 */
const QUIET_MS = 1200
/** A late-arriving frame still gets a full picture without polling hard. */
const HEARTBEAT_MS = 900

type ViewState = {
  /** `aria-label` of the topmost open modal, or '' when none is. */
  overlay: string
  menuOpen: boolean
  /**
   * Whether the navigation sidebar is on screen, or `null` for "this frame cannot say".
   *
   * A phone showing the side panel has its sidebar shut *because* the panel is open,
   * not because anyone chose that. Reporting the forced value made the constraint
   * travel: the desktop, told the sidebar was closed, collapsed its own - so opening a
   * panel on the wide frame quietly cost it the fleet column.
   */
  sidebarOpen: boolean | null
  /** Selected side-panel tab, or '' when the panel is shut. */
  drawerTab: string
  drawerSegment: string
  projectId: string
  sessionId: string
  /** Every other segmented control on screen, as `aria-label` to selected label. */
  tabs: Record<string, string>
}

/**
 * What opens each modal, by the label it announces itself with.
 *
 * Keyed on `aria-label` rather than on a component name because that is the only
 * identity a frame can read from the outside, and it is the one the app already
 * maintains for screen readers - so it cannot silently drift out of use.
 *
 * `Settings` covers the Plugins row too: that row opens Settings on its own section,
 * and the section is mirrored by the generic tablist rule below.
 */
const OVERLAY_COMMANDS: Record<string, string> = {
  Resources: 'resources.open',
  Usage: 'usage.open',
  'Fleet queue': 'queue.fleet',
  'Agent session history': 'history.open',
  'Manage projects': 'project.create',
  'Prompt library': 'prompts.open',
  Help: 'help.open',
  Automation: 'hooks.open',
  // Configure Actions is not here because it is no longer a modal: it is a section of
  // Settings, so it mirrors as `Settings` plus the generic tablist rule below.
  Settings: 'settings.open',
  'Command palette': 'palette.open',
}

/**
 * A modal, as opposed to a panel that happens to be one on a phone.
 *
 * The phone lays the side panel out as an overlay and marks it `role="dialog"`, which
 * is correct for a screen reader and wrong for this reading: the panel is already
 * mirrored by its own tab, and counting it here made the two frames permanently
 * disagree about "which modal is open" - the phone naming a drawer the desktop docks.
 */
const isModal = (element: Element): boolean =>
  visible(element) && !element.classList.contains('utility-drawer')

/**
 * The sidebar is the one surface whose *resting* state differs by layout: a desktop
 * draws it as a column that is open by default, a phone as an overlay that is shut by
 * default. Mirrored naively, the two frames disagree the moment they boot, and whoever
 * publishes last wins - which on the first load meant the phone silently collapsing the
 * desktop's fleet column before the visitor had touched anything.
 *
 * So a frame stays quiet about its sidebar until the value has actually moved. After
 * that it has an opinion, and every later toggle mirrors.
 */
let sidebarBaseline: boolean | null = null
let sidebarIntent = false

function sidebarReading(raw: boolean, panelForcesItShut: boolean): boolean | null {
  if (sidebarBaseline === null) sidebarBaseline = raw
  if (raw !== sidebarBaseline) sidebarIntent = true
  // A phone showing the side panel has its sidebar shut *because* the panel is open,
  // not because anyone chose that. Reporting the forced value made the constraint
  // travel: the desktop, told the sidebar was closed, collapsed its own.
  if (panelForcesItShut) return null
  return sidebarIntent ? raw : null
}

function readView(): ViewState {
  const dialogs = [...document.querySelectorAll('[role="dialog"][aria-modal="true"][aria-label]')]
    .filter(isModal)
  const drawerTab = document.querySelector<HTMLElement>('[data-drawer-tab-id][aria-selected="true"]')
    ?.dataset.drawerTabId || ''
  const segmentBody = drawerTab
    ? document.querySelector(`.drawer-body-${drawerTab} .drawer-segment-body:not([hidden])`)
    : null
  const segment = [...(segmentBody?.classList || [])]
    .find(name => name.startsWith('drawer-segment-') && name !== 'drawer-segment-body')
  const workspace = document.querySelector('.workspace')
  const params = new URLSearchParams(location.search)

  const tabs: Record<string, string> = {}
  for (const strip of document.querySelectorAll('[role="tablist"][aria-label]')) {
    if (!mirrorableTablist(strip) || !visible(strip)) continue
    const selected = strip.querySelector('[role="tab"][aria-selected="true"]')
    if (selected) tabs[strip.getAttribute('aria-label') || ''] = text(selected.textContent)
  }

  return {
    overlay: dialogs.length ? dialogs[dialogs.length - 1].getAttribute('aria-label') || '' : '',
    menuOpen: Boolean(document.querySelector('[data-tutorial="main-menu"]')),
    // One reading for two layouts: the phone slides the sidebar in over the workspace,
    // the desktop collapses it to a rail. "Is the fleet on screen" is the same question,
    // asked through `sidebarReading` because the two layouts disagree about the answer
    // at rest.
    sidebarOpen: sidebarReading(
      narrow()
        ? Boolean(document.querySelector('.sidebar.open'))
        : Boolean(workspace) && !workspace!.classList.contains('sidebar-collapsed'),
      Boolean(narrow() && drawerTab),
    ),
    drawerTab,
    drawerSegment: segment ? segment.slice('drawer-segment-'.length) : '',
    // The app rewrites `?project=&session=` on every focus change, which makes the URL
    // a more honest reading of focus than any element: it is written from the settled
    // state rather than from whichever control happened to cause it.
    projectId: params.get('project') || '',
    sessionId: params.get('session') || '',
    tabs,
  }
}

const same = (left: ViewState, right: ViewState): boolean =>
  JSON.stringify(left) === JSON.stringify(right)

/**
 * One step toward the other frame's state, or `false` when there is nothing left to do.
 *
 * Ordered outside-in, because the enclosing surfaces decide whether the inner ones are
 * even rendered: a segment inside a modal cannot be selected until the modal is open,
 * and on a phone the sidebar and the side panel are mutually exclusive, so the panel
 * has to settle after the sidebar rather than fight it.
 */
function applyOneDifference(mine: ViewState, want: ViewState, tried: Set<string>): boolean {
  /** One attempt per field per received state.
   *
   *  A frame can be told to reach something it cannot: a Project row inside a collapsed
   *  group, a modal with no command, a control the other layout does not draw. Without
   *  this the corrective act would fire on every step of the pass and, because the act
   *  does change something, the two frames would take turns undoing each other. Trying
   *  once and moving on degrades to "that part did not mirror", which is the right
   *  failure. */
  const once = (field: string, act: () => boolean): boolean => {
    if (tried.has(field)) return false
    tried.add(field)
    return act()
  }

  if (mine.overlay !== want.overlay) {
    // Close before opening: the reading is "the topmost dialog", and two of them
    // stacked would report the wrong one forever. Closing is not rationed - it is the
    // step that makes the next one possible.
    if (mine.overlay) { pressEscape(); return true }
    const command = OVERLAY_COMMANDS[want.overlay]
    // A modal with no command is one this frame cannot reproduce. Leaving it alone is
    // the honest failure; closing what this frame has open instead would be worse.
    if (command && once('overlay', () => { run(command); return true })) return true
  }
  if (mine.menuOpen !== want.menuOpen && once('menu', () => { run('menu.toggle'); return true })) return true

  // A phone cannot hold both panels, so a desktop frame showing both has to be
  // reduced. The side panel wins: it is the surface the visitor selected a tab in,
  // whereas the sidebar is navigation they have already finished using.
  const wantSidebar = want.sidebarOpen !== null && want.sidebarOpen && !(narrow() && want.drawerTab)
  if (want.sidebarOpen !== null && mine.sidebarOpen !== wantSidebar && once('sidebar', () => {
    run(wantSidebar ? 'sidebar.open' : 'sidebar.close')
    return true
  })) return true

  if (!want.drawerTab && mine.drawerTab && once('drawer', () => { run('drawer.close'); return true })) return true
  if (want.drawerTab && mine.drawerTab !== want.drawerTab && once('drawer', () => {
    // `drawer.show:` rather than `drawer.`: the latter toggles the panel shut when the
    // tab is already the selected one, which is the opposite of converging on it.
    run(`drawer.show:${want.drawerTab}`)
    return true
  })) return true
  if (want.drawerTab && want.drawerSegment && mine.drawerSegment !== want.drawerSegment
    && once('drawerSegment', () => { run(`drawer.${want.drawerTab}.${want.drawerSegment}`); return true })) return true

  if (want.projectId && mine.projectId !== want.projectId
    && once('project', () => clickProject(want.projectId))) return true
  if (want.sessionId && mine.sessionId !== want.sessionId
    && once('session', () => clickSession(want.sessionId))) return true

  for (const [label, selected] of Object.entries(want.tabs)) {
    if (mine.tabs[label] === selected) continue
    if (once(`tab:${label}`, () => clickTab(label, selected))) return true
  }
  return false
}

// The director's leadership, decided over the same channel. Module state rather than
// closure state because `requestDirectorLead` is called by the director, which knows
// nothing about the mirror beyond wanting an answer.
let mirrorChannel: BroadcastChannel | null = null
let directorLead = false
/** The best rival claim heard recently, and when a live leader last said so.
 *
 *  Both are *accumulated* rather than cleared when this frame claims. The frames boot
 *  together, so a rival's claim routinely lands before this one is even sent, and an
 *  election that reset its own tally on claiming threw that away and elected everybody. */
let rivalClaim = { score: 0, at: 0 }
let takenAt = 0
/** How long a heard claim stays relevant. Long enough to cover both frames booting,
 *  short enough that a replay minutes later is a fresh election. */
const CLAIM_TTL_MS = 4_000

export function installViewMirror(): void {
  if (typeof BroadcastChannel !== 'function') return
  const channel = new BroadcastChannel(CHANNEL_NAME)
  mirrorChannel = channel
  let published: ViewState | null = null
  let desired: ViewState | null = null
  let quietUntil = 0
  let converging = false
  /**
   * Whether anyone is listening.
   *
   * A demo shown on its own is the common case, and there the mutation-driven read is
   * pure cost: a streaming terminal mutates the DOM continuously, and re-reading the
   * view eleven times a second to tell nobody is the sort of thing that shows up on a
   * phone. Until a peer speaks, this frame publishes on the heartbeat only.
   */
  let peerSeen = false

  const publish = (): void => {
    // Nothing to say until the app has drawn itself. An empty reading broadcast during
    // boot would tell the other frame to close everything it has open.
    if (!document.querySelector('.workspace')) return
    const view = readView()
    if (published && same(view, published)) return
    const echo = Date.now() < quietUntil
    published = view
    if (!echo) channel.postMessage({ kind: 'view', from: FRAME_ID, view })
  }

  const converge = async (): Promise<void> => {
    if (converging) return
    converging = true
    const tried = new Set<string>()
    try {
      for (let step = 0; step < MAX_STEPS; step += 1) {
        if (!desired) break
        quietUntil = Date.now() + QUIET_MS
        if (!applyOneDifference(readView(), desired, tried)) break
        await delay(SETTLE_MS)
      }
    } finally {
      converging = false
      // Whatever this frame actually reached is now its own reading, and must not be
      // re-broadcast as though the visitor had done it.
      published = document.querySelector('.workspace') ? readView() : published
    }
  }

  channel.addEventListener('message', event => {
    const payload = event.data as { kind?: string; from?: string; view?: ViewState; score?: number }
    if (!payload || payload.from === FRAME_ID) return
    peerSeen = true
    if (payload.kind === 'director-claim') {
      if (directorLead) { channel.postMessage({ kind: 'director-taken', from: FRAME_ID }); return }
      if (typeof payload.score !== 'number') return
      const stale = Date.now() - rivalClaim.at > CLAIM_TTL_MS
      rivalClaim = {
        score: stale ? payload.score : Math.max(rivalClaim.score, payload.score),
        at: Date.now(),
      }
      return
    }
    if (payload.kind === 'director-taken') { takenAt = Date.now(); return }
    if (payload.kind !== 'view' || !payload.view) return
    desired = payload.view
    void converge()
  })

  // The DOM is the source, so the DOM is what is watched. A coarse interval underneath
  // it catches the readings no mutation announces - the URL rewrite behind a focus
  // change, above all, which `history.replaceState` performs silently.
  let pending: number | undefined
  const schedule = (): void => {
    if (!peerSeen) return
    window.clearTimeout(pending)
    pending = window.setTimeout(publish, 90)
  }
  new MutationObserver(schedule).observe(document.body, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ['class', 'aria-selected', 'hidden', 'data-drawer-tab-id'],
  })
  window.setInterval(publish, HEARTBEAT_MS)
  window.setTimeout(publish, 1_500)
}

/**
 * Whether this frame should run the director - the walkthrough, or any scenario.
 *
 * With the desktop and phone frames side by side they are two copies of one app, and two
 * scripts driving two different controls at once is noise rather than instruction -
 * especially on a 320px phone frame, where the caption covers the thing it points at.
 * So exactly one frame runs it, the wider one by preference, and the mirror means its
 * steps visibly drive the other: pressing the rail on the desktop lights the phone up
 * too, which demonstrates more than a second copy would.
 *
 * A frame that is alone always wins, because nobody answers.
 */
export async function requestDirectorLead(mobile: boolean): Promise<boolean> {
  if (!mirrorChannel) return true
  // The wider frame leads, with a random low half so two frames of the same width
  // still settle on one rather than both deciding they won. `trueRandom` for the same
  // reason as the frame id: a seeded draw would give both frames the same tiebreak.
  const score = (mobile ? 1 : 2) * 1_000_000 + Math.floor(trueRandom() * 1_000_000)
  mirrorChannel.postMessage({ kind: 'director-claim', from: FRAME_ID, score })
  await delay(600)
  const heardRecently = Date.now() - rivalClaim.at < CLAIM_TTL_MS
  if (Date.now() - takenAt < CLAIM_TTL_MS) return false
  if (heardRecently && rivalClaim.score > score) return false
  directorLead = true
  return true
}

/**
 * Give the lead back.
 *
 * The election was written for a walkthrough that ran once and was over. A director runs
 * a scenario, ends, and may be asked for another one minutes later - and a frame that
 * kept `directorLead` forever would answer every future claim with "taken", so the other
 * frame could never lead even after this one had finished. Releasing on end makes each
 * run its own election, which is what `CLAIM_TTL_MS` already assumed.
 */
export function releaseDirectorLead(): void {
  directorLead = false
  rivalClaim = { score: 0, at: 0 }
  takenAt = 0
}
