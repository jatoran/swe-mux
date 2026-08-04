import type { Session, SessionState, StandingActivity } from './types'

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
    case 'question': return 'awaiting answer'
    case 'elicitation': return 'awaiting input'
    case 'rate_limit': return 'rate limited'
    case 'approval':
    default: return 'awaiting approval'
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
    ? 'ready · background work running'
    : 'ready · turn complete'
}

function isAgentBackend(session: Session): boolean {
  return session.backend === 'claude' || session.backend === 'codex'
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
  if (loop) badges.push({ glyph: '⟳', label: 'loop armed', title: `loop armed${loop.detail ? ` · ${loop.detail}` : ''}` })
  const cron = byKind('cron')
  if (cron) {
    const label = cron.count > 1 ? plural(cron.count, 'cron job') : 'cron scheduled'
    badges.push({ glyph: '⟳', label, title: `${label}${cron.detail ? ` · ${cron.detail}` : ''}`, count: cron.count })
  }
  const background = byKind('background_tasks')
  if (background) {
    const label = plural(background.count, 'background task')
    badges.push({ glyph: '≡', label, title: `${label} running${background.detail ? ` · ${background.detail}` : ''}`, count: background.count })
  }
  const subagents = byKind('subagents')
  if (subagents) {
    const label = plural(subagents.count, 'subagent')
    badges.push({ glyph: '⑂', label, title: `${label} running${subagents.detail ? ` · ${subagents.detail}` : ''}`, count: subagents.count })
  }
  return badges
}

/** Status line text shown next to a session; total over SessionState. */
export function sessionStatus(session: Session): string {
  if (!isAgentBackend(session)) return session.state
  const context = session.context_pct > 0 ? ` · ${Math.round(session.context_pct * 100)}%` : ''
  const badges = activityBadges(session)
  const standing = badges.map(badge => ` · ${badge.label}`).join('')
  if (session.state === 'working') return `working${session.state_detail ? ` · ${session.state_detail}` : ''}${standing}${context}`
  if (session.state === 'idle') {
    // The background annotation supersedes the derived idle_reason text: one
    // fact must not render twice ("background work running · 2 background
    // tasks"). With any badge present, "ready" alone keeps the line scannable.
    const base = badges.length ? 'ready' : idleLabel(session)
    return `${base}${standing}${context}`
  }
  if (session.state === 'awaiting') return `${awaitingLabel(session)}${session.state_detail ? ` · ${session.state_detail}` : ''}${standing}${context}`
  if (session.state === 'starting') return 'starting agent…'
  return `${session.state}${context}`
}
