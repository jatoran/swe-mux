import { codeLabel } from './localization/codeLabels.ts'
import { t } from './localization/index.ts'
import type { Session, SessionState, StandingActivity } from './types'
import { hasHarnessMeasurement, isAgentBackend, isObservedHarness } from './harnessRegistry.ts'

// Single source of truth for rendering the user-visible session status.
// Desktop sidebar rows, pane headers, tab strips, and the mobile unified-tab
// projection all render through these functions — no surface may keep an
// independent heuristic. tests/sessionStatus.test.ts asserts the mapping is
// total: every SessionState renders a non-empty label and dot class.

const STATE_DOT: Record<SessionState, string> = {
  starting: 'starting',
  running: 'running',
  working: 'working',
  idle: 'idle',
  awaiting: 'awaiting',
  exited: 'exited',
  crashed: 'crashed',
}

/** CSS class suffix for the colored state dot; total over SessionState. */
export function stateDotClass(state: SessionState | undefined): string {
  return `state-dot ${state ? STATE_DOT[state] ?? 'running' : 'running'}`
}

/**
 * Dot class for a session, adding the `standing` modifier when the root turn is
 * idle but a standing engagement is still running (live subagents or background
 * tasks).
 *
 * Rendered as a blue *ring* rather than a filled dot: blue says "an agent is
 * engaged", and the ring says "not generating right now — you can type". It is
 * deliberately a shape difference, not a pulse: `prefers-reduced-motion`
 * disables the working dot's animation, so a motion-only distinction would
 * collapse into "identical to working" for exactly the users who need it most.
 *
 * This is the one case where standing activity touches the dot at all. Green
 * still means "ready and deliverable" — and so does this, which is why the ring
 * is blue-but-hollow rather than a variant of green (hue variants of green fail
 * at a glance and fail colorblind users). `loop`/`cron` deliberately do NOT
 * qualify: an armed wakeup is a *scheduled* engagement with nothing running
 * now, and the ⟳ glyph already carries it.
 */
export function sessionDotClass(session: Session | undefined): string {
  if (!session) return stateDotClass(undefined)
  const base = stateDotClass(session.state)
  if (session.state === 'working' && session.interrupt_pending_at) return `${base} interrupting`
  return session.state === 'idle' && hasRunningActivity(session) ? `${base} standing` : base
}

/** Whether a standing annotation represents work running *right now*. */
export function hasRunningActivity(session: Session): boolean {
  const annotations: StandingActivity[] = session.standing_activity ?? []
  return annotations.some(item => item.kind === 'subagents' || item.kind === 'background_tasks')
}

/**
 * Awaiting label by typed sub-reason. `idle_prompt`-driven idle never reaches
 * here (it maps to state 'idle'), so a finished agent can never render as an
 * approval. Unknown/missing sub-reason falls back to the generic approval
 * affordance (the conservative historical default).
 */
export function awaitingLabel(session: Session): string {
  switch (session.awaiting_reason) {
    case 'authentication': return t("sessionStatus.a8ab102f93")
    case 'question': return t("sessionStatus.b8888cdfbe")
    case 'elicitation': return t("sessionStatus.bc2767c7bf")
    case 'rate_limit': return t("sessionStatus.d292012eb3")
    case 'approval':
    default: return t("sessionStatus.84e3eb92c7")
  }
}

/**
 * Idle label by typed sub-reason. The turn really did end and delivery really is
 * safe, so this stays on the idle axis — but "ready · turn complete" reads as
 * "finished, nothing more is coming", which is wrong while the agent has
 * background work that will wake it back up.
 */
export function idleLabel(session: Session): string {
  return session.idle_reason === 'waiting_on_background'
    ? t("sessionStatus.ff90af80ad")
    : t("sessionStatus.a081d707be")
}

/** One compact affordance per standing-activity annotation. */
export interface ActivityBadge {
  glyph: string
  /** Short status-line text, e.g. "loop armed", "2 background tasks". */
  label: string
  /** Tooltip: distinguishes loop vs cron, carries the annotation's detail. */
  title: string
  /** Rendered beside the glyph on dense surfaces when > 1. */
  count?: number
}

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`

/**
 * Standing-activity badges for a session, in a stable order. Annotations are
 * the fifth status axis — an armed /loop, a cron schedule, background tasks,
 * live subagents — and deliberately NOT states: the dot color and the delivery
 * gate are untouched (tests assert idle+loop still classifies as ready). Loop
 * and cron share one glyph (sidebar density beats taxonomy); the title
 * distinguishes them and carries the annotation's own detail.
 */
export function activityBadges(session: Session): ActivityBadge[] {
  const badges: ActivityBadge[] = []
  const annotations: StandingActivity[] = session.standing_activity ?? []
  const byKind = (kind: string) => annotations.find(item => item.kind === kind)
  const loop = byKind('loop')
  if (loop) badges.push({ glyph: '⟳', get label() { return t("sessionStatus.5a4b86c699") }, get title() { return t("sessionStatus.0580196d90", { value1: loop.detail ? ` · ${loop.detail}` : '' }) } })
  const cron = byKind('cron')
  if (cron) {
    const label = cron.count > 1 ? plural(cron.count, t("sessionStatus.af63f398e7")) : t("sessionStatus.b9f715cceb")
    badges.push({ glyph: '⟳', label, get title() { return t("sessionStatus.be9e11092d", { label: label, value2: cron.detail ? ` · ${cron.detail}` : '' }) }, count: cron.count })
  }
  const background = byKind('background_tasks')
  if (background) {
    const label = plural(background.count, t("sessionStatus.3614c7707a"))
    badges.push({ glyph: '≡', label, get title() { return t("sessionStatus.40b1e31923", { label: label, value2: background.detail ? ` · ${background.detail}` : '' }) }, count: background.count })
  }
  const subagents = byKind('subagents')
  if (subagents) {
    const label = plural(subagents.count, 'subagent')
    badges.push({ glyph: '⑂', label, get title() { return t("sessionStatus.40b1e31923", { label: label, value2: subagents.detail ? ` · ${subagents.detail}` : '' }) }, count: subagents.count })
  }
  return badges
}

/**
 * The faults a surface should mark on a session, as reader-facing sentences. Empty for a
 * healthy session, which is the whole point: this drives a *visible* marker, so anything
 * that is true of an ordinary session must not appear here.
 *
 * The distinction this function exists to hold is between a fault and the diagnostic text
 * that describes one. `parser_diagnostic` is set on **every** observed session as routine
 * detail (`observation.py`: `tailing <file>.jsonl`, `schema v2: 801/801 recognized (0%
 * unknown)`) - measured 2026-08-18, 17/17 live sessions carried one - so its presence says
 * nothing. `parser_status === 'degraded'` is the fault; the diagnostic is only its wording.
 * `observation_diagnostic` is the same shape: the daemon sets it beside
 * `observation_stale_since` at every site, so staleness is the fault and it is the wording.
 * Reading either string as a trigger marks every healthy session, which is an alarm that
 * has stopped meaning anything.
 *
 * Faults are deliberately not states. They do not touch the dot, the tab, or `sessionStatus`
 * - a session can be perfectly `idle` and still be reporting on a conversation it no longer
 * owns, and that is exactly the case a state axis cannot express.
 */
export function sessionFaults(session: Session): string[] {
  const faults: string[] = []
  if (session.runtime_boundary === 'remote' || session.runtime_boundary === 'unknown') {
    faults.push(
      t("sessionStatus.0b2da531c9") +
        t("sessionStatus.a4f502330a"),
    )
  }
  if (session.observation_stale_since) {
    faults.push(
      session.observation_diagnostic ||
        t("sessionStatus.bd5d5926a7"),
    )
  }
  // The degraded wording already names the schema and the unrecognized share; the fallback
  // covers a degrade recorded before any diagnostic was written.
  if (session.parser_status === 'degraded') {
    faults.push(session.parser_diagnostic || t("sessionStatus.ae5abb289c"))
  }
  return faults
}

/** Status line text shown next to a session; total over SessionState. */
export function sessionStatus(session: Session): string {
  if (session.runtime_boundary === 'unknown') return t("sessionStatus.ffb51c84d3")
  if (session.runtime_boundary === 'remote') {
    if (session.awaiting_reason === 'authentication') return t("sessionStatus.a8ab102f93")
    if (session.remote_transport_state === 'ended') return t("sessionStatus.cbd307b213")
    return t("sessionStatus.14149ec865", { value1: codeLabel(session.state) })
  }
  if (!isAgentBackend(session.backend)) return session.state
  if (!isObservedHarness(session.backend)) return t("sessionStatus.86fdf36145")
  const context = session.parser_status === 'degraded'
    ? t("sessionStatus.fb3e12bdd3")
    : hasHarnessMeasurement(session.backend) && session.context_pct > 0
      ? ` · ${Math.round(session.context_pct * 100)}%`
      : ''
  const badges = activityBadges(session)
  const standing = badges.map(badge => ` · ${badge.label}`).join('')
  if (session.state === 'working' && session.interrupt_pending_at) return t("sessionStatus.6bf7be19e7", { value1: standing, value2: context })
  if (session.state === 'working') return `working${session.state_detail ? ` · ${session.state_detail}` : ''}${standing}${context}`
  if (session.state === 'idle') {
    // The background annotation supersedes the derived idle_reason text: one
    // fact must not render twice ("background work running · 2 background
    // tasks"). With any badge present, "ready" alone keeps the line scannable.
    const base = badges.length ? 'ready' : idleLabel(session)
    return `${base}${standing}${context}`
  }
  if (session.state === 'awaiting') return `${awaitingLabel(session)}${session.state_detail ? ` · ${session.state_detail}` : ''}${standing}${context}`
  if (session.state === 'starting') return t("sessionStatus.6f58d4e6b0")
  return `${session.state}${context}`
}
