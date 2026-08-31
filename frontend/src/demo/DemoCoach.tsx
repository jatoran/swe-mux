/**
 * The demo's coach: a short, hands-on walk that flashes one piece of real chrome at
 * a time and hands over as soon as the visitor does the thing.
 *
 * Deliberately not the product's own `GuidedTutorial`. That one teaches an install -
 * create a Project, sign in to a CLI, start a session - and none of those acts exist
 * here. This teaches the *interface*: where the command rail is, what the side panel
 * holds, and (on a phone) that the panels are on the ends of a swipe, which is the
 * one thing nothing on screen can say for itself.
 *
 * Every step is skippable and the whole thing is dismissible for good. A step that
 * carries an action advances the moment the visitor performs it against the real UI -
 * "until they click in and take over" is the whole design: the coach never simulates
 * the act, it waits for it.
 */
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { placeTutorialCard } from '../tutorial.ts'
import { requestCoachLead } from './mirror.ts'

const STORAGE_KEY = 'swemux-demo-coach-v1'
const DONE = 'done'

/** Wait for the app's first real paint before measuring anything. The fleet arrives
 *  a fetch after mount, and a spotlight measured before then lands on nothing. */
const START_DELAY_MS = 2_600

type Advance =
  /** Any click that lands inside one of these selectors. */
  | { kind: 'click'; selectors: string[] }
  /** A horizontal drag of at least `SWIPE_MIN` px in this direction, anywhere. */
  | { kind: 'swipe'; direction: 'left' | 'right' }
  /** A window event the app already raises. */
  | { kind: 'event'; name: string }

type Step = {
  id: string
  eyebrow: string
  title: string
  body: JSX.Element
  /** First match wins; a step with none draws its card in the middle. */
  selectors?: string[]
  advance?: Advance
  hint?: string
  /** Draw the swipe glyph over the spotlight, pointing this way. */
  gesture?: 'left' | 'right'
}

const SWIPE_MIN = 55

const DRAWER_TAB = (label: string): string[] => [
  `button[title^="${label} -"]`,
  `button[aria-label^="${label} -"]`,
]

function desktopSteps(): Step[] {
  return [
    {
      id: 'welcome',
      eyebrow: 'THE REAL INTERFACE',
      title: 'This is the actual app, running on a fake daemon.',
      body: <>
        <p>Every pane, panel and menu below is the shipped frontend. The sessions are
        invented and the agents only tell jokes, but nothing else is a mock-up.</p>
        <p>Six quick stops. Do the thing each one asks and it moves on by itself.</p>
      </>,
    },
    {
      id: 'sidebar',
      eyebrow: 'THE FLEET',
      title: 'Every session, in one column.',
      selectors: ['.sidebar'],
      advance: { kind: 'click', selectors: ['.session-row'] },
      hint: 'Click any session to focus its pane',
      body: <>
        <p>Projects group sessions; each row carries live state, the model, how long
        the current turn has been running, and what its checkout looks like.</p>
        <p>Click one - the workspace focuses that pane.</p>
      </>,
    },
    {
      id: 'rail',
      eyebrow: 'THE COMMAND RAIL',
      title: 'The keys a terminal cannot send.',
      selectors: ['.terminal-action-rail'],
      advance: { kind: 'click', selectors: ['.terminal-action-rail'] },
      hint: 'Press anything on the rail',
      body: <>
        <p>Escape, Ctrl-C, arrow keys, paste, approve, the model picker, the prompt
        library - one editable strip under every pane, on desktop and on a phone.</p>
        <p>Press one. Nothing here can break anything.</p>
      </>,
    },
    {
      id: 'compose',
      eyebrow: 'TALK TO IT',
      title: 'Type into the agent and press Enter.',
      selectors: ['.terminal-pane.focused', '.terminal-pane'],
      advance: { kind: 'event', name: 'mux:turn-ended' },
      hint: 'Type anything, then Enter',
      body: <>
        <p>The composer is the CLI's own, drawn by the CLI. swe-mux is the multiplexer
        around it, not a chat window bolted on top.</p>
        <p>Ask it something. It will answer badly, on purpose.</p>
      </>,
    },
    {
      id: 'transcript',
      eyebrow: 'THE SIDE PANEL',
      title: 'Read the conversation beside the terminal.',
      selectors: DRAWER_TAB('Transcript'),
      advance: { kind: 'click', selectors: DRAWER_TAB('Transcript') },
      hint: 'Open Transcript',
      body: <>
        <p>The same turn you just drove, merged into readable messages with the tool
        calls between them - searchable, copyable, and never scrolled away.</p>
        <p>Activity, beside it, is the behavioural timeline of the same run.</p>
      </>,
    },
    {
      id: 'git',
      eyebrow: 'THE REPOSITORY',
      title: 'Worktrees, commits, and who made them.',
      selectors: DRAWER_TAB('Git'),
      advance: { kind: 'click', selectors: DRAWER_TAB('Git') },
      hint: 'Open Git',
      body: <>
        <p>Map is one row per checkout with its changes and the sessions standing in
        it. Log is the real commit graph. Provenance connects each commit back to the
        session and run that produced it.</p>
      </>,
    },
    {
      id: 'resources',
      eyebrow: 'THE MACHINE',
      title: 'What the fleet is actually consuming.',
      // The summary button by its own class rather than "a button in the footer": the
      // footer carries several now, and any of them would have satisfied the step.
      selectors: ['.resource-usage-summary', '.sidebar-footer'],
      advance: { kind: 'click', selectors: ['.resource-usage-summary'] },
      hint: 'Open the resource summary',
      body: <>
        <p>Processes, listeners, bandwidth, disk, and the durable telemetry behind
        them - per session, per Project, and for swe-mux itself.</p>
      </>,
    },
    {
      id: 'over',
      eyebrow: 'IT IS YOURS NOW',
      title: 'Break it however you like.',
      body: <>
        <p>Spawn panes, split them, kill them, edit notes, change the keymap, switch
        the theme. Everything persists in this browser and nowhere else.</p>
        <p>"reset demo", above the frame, puts it all back.</p>
      </>,
    },
  ]
}

function mobileSteps(): Step[] {
  return [
    {
      id: 'welcome',
      eyebrow: 'THE REAL INTERFACE',
      title: 'The phone layout, not a screenshot of one.',
      body: <>
        <p>This is the same frontend the desktop runs, laid out for a thumb. The
        sessions are invented; the interface is not.</p>
        <p>Five stops, and two of them are gestures worth knowing.</p>
      </>,
    },
    {
      id: 'swipe-sidebar',
      eyebrow: 'GESTURE',
      title: 'Swipe right to reach the fleet.',
      advance: { kind: 'swipe', direction: 'right' },
      gesture: 'right',
      hint: 'Swipe right across the terminal',
      body: <>
        <p>Both panels live off the edges of the screen. Swipe right anywhere over the
        terminal and the session list comes in.</p>
      </>,
    },
    {
      id: 'swipe-drawer',
      eyebrow: 'GESTURE',
      title: 'Swipe left for the side panel.',
      advance: { kind: 'swipe', direction: 'left' },
      gesture: 'left',
      hint: 'Swipe left across the terminal',
      body: <>
        <p>The other direction opens notes, the transcript, Git, and alerts. Two
        fingers moves between tabs; every gesture is rebindable in Settings.</p>
      </>,
    },
    {
      id: 'rail',
      eyebrow: 'THE COMMAND RAIL',
      title: 'The keys a phone keyboard does not have.',
      selectors: ['.terminal-action-rail'],
      advance: { kind: 'click', selectors: ['.terminal-action-rail'] },
      hint: 'Press anything on the rail',
      body: <>
        <p>Escape, Ctrl-C, arrows, approve, paste. This strip is the reason a phone can
        actually drive an agent CLI rather than just watch one.</p>
      </>,
    },
    {
      id: 'compose',
      eyebrow: 'TALK TO IT',
      title: 'Type something and send it.',
      selectors: ['.terminal-pane'],
      advance: { kind: 'event', name: 'mux:turn-ended' },
      hint: 'Type anything, then send',
      body: <>
        <p>The agent replies badly, on purpose. The Transcript tab in the side panel
        will have the same turn, as readable messages.</p>
      </>,
    },
    {
      id: 'over',
      eyebrow: 'IT IS YOURS NOW',
      title: 'Have a look around.',
      body: <>
        <p>Everything persists in this browser and nowhere else. "reset demo", above
        the frame, puts it back.</p>
      </>,
    },
  ]
}

const firstVisible = (selectors: string[] | undefined): HTMLElement | null => {
  for (const selector of selectors || []) {
    for (const candidate of document.querySelectorAll<HTMLElement>(selector)) {
      const rect = candidate.getBoundingClientRect()
      if (rect.width > 4 && rect.height > 4) return candidate
    }
  }
  return null
}

const sameRect = (left: DOMRect | null, right: DOMRect | null): boolean =>
  left === right || Boolean(left && right
    && Math.abs(left.left - right.left) < 0.5 && Math.abs(left.top - right.top) < 0.5
    && Math.abs(left.width - right.width) < 0.5 && Math.abs(left.height - right.height) < 0.5)

const stored = (): string => {
  try { return localStorage.getItem(STORAGE_KEY) || '' } catch { return DONE }
}

export function DemoCoach() {
  const mobile = useMemo(() => window.matchMedia('(max-width: 760px)').matches, [])
  const [live, setLive] = useState(false)
  const [index, setIndex] = useState(0)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const [cardSize, setCardSize] = useState({ width: 360, height: 230 })
  const card = useRef<HTMLElement>(null)
  const steps = useMemo(() => (mobile ? mobileSteps() : desktopSteps()), [mobile])
  const step = steps[Math.min(index, steps.length - 1)]

  const finish = (): void => {
    setLive(false)
    try { localStorage.setItem(STORAGE_KEY, DONE) } catch { /* private mode */ }
  }

  // One tour across every frame. With the desktop and phone shown together they are two
  // copies of one app, and two cards flashing two different controls is noise; the
  // mirror means the winner's steps visibly drive the other frame anyway.
  const startIfLeading = (): void => {
    void requestCoachLead(mobile).then(lead => { if (lead) setLive(true) })
  }

  useEffect(() => {
    if (stored() === DONE) return
    const timer = window.setTimeout(startIfLeading, START_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [])

  // Replay from the page: the embed's own control dispatches this, so a visitor who
  // finished the walk (or a returning one) can ask for it again without clearing
  // storage - which would also throw away the fleet they have been playing with.
  useEffect(() => {
    const replay = () => { setIndex(0); startIfLeading() }
    window.addEventListener('swemux-demo:coach', replay)
    return () => window.removeEventListener('swemux-demo:coach', replay)
  }, [])

  // Where the spotlight goes. Re-measured on every DOM change rather than on a timer:
  // the chrome this points at moves when a panel opens, and a stale ring on a moved
  // control is worse than no ring at all.
  useEffect(() => {
    if (!live) return
    let frame = 0
    const measure = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const found = firstVisible(step.selectors)
        const next = found ? found.getBoundingClientRect() : null
        setRect(current => (sameRect(current, next) ? current : next))
        if (card.current) {
          const size = { width: card.current.offsetWidth, height: card.current.offsetHeight }
          setCardSize(current =>
            current.width === size.width && current.height === size.height ? current : size)
        }
      })
    }
    measure()
    const observer = new MutationObserver(measure)
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] })
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [live, step])

  // What satisfies the current step. Every listener is passive and capture-phase: the
  // coach observes the real interaction and must never consume it.
  useEffect(() => {
    if (!live || !step.advance) return
    const advance = () => setIndex(value => Math.min(steps.length - 1, value + 1))
    const rule = step.advance

    if (rule.kind === 'event') {
      const handler = () => advance()
      window.addEventListener(rule.name, handler)
      return () => window.removeEventListener(rule.name, handler)
    }

    if (rule.kind === 'click') {
      const handler = (event: MouseEvent) => {
        const target = event.target instanceof Element ? event.target : null
        if (target && rule.selectors.some(selector => target.closest(selector))) {
          // A microtask, so the app's own handler for this click runs first and the
          // next step measures the chrome the click produced rather than the old one.
          queueMicrotask(advance)
        }
      }
      document.addEventListener('click', handler, true)
      return () => document.removeEventListener('click', handler, true)
    }

    let origin: { x: number; y: number } | null = null
    const down = (event: PointerEvent) => { origin = { x: event.clientX, y: event.clientY } }
    const up = (event: PointerEvent) => {
      if (!origin) return
      const dx = event.clientX - origin.x
      const dy = event.clientY - origin.y
      origin = null
      if (Math.abs(dx) < SWIPE_MIN || Math.abs(dy) > Math.abs(dx)) return
      if ((rule.direction === 'right') === (dx > 0)) advance()
    }
    document.addEventListener('pointerdown', down, true)
    document.addEventListener('pointerup', up, true)
    return () => {
      document.removeEventListener('pointerdown', down, true)
      document.removeEventListener('pointerup', up, true)
    }
  }, [live, step, steps.length])

  if (!live) return null

  const placed = placeTutorialCard(rect, { width: innerWidth, height: innerHeight }, cardSize)
  // A gesture step has no anchor, so the card would centre itself - directly over the
  // glyph that is the whole instruction. It sits at the foot of the screen instead,
  // which is also where a thumb is.
  const position = step.gesture
    ? { ...placed, top: Math.max(16, innerHeight - cardSize.height - 20) }
    : placed
  const last = index >= steps.length - 1
  // Clamped into the viewport, because the two things most worth flashing sit on its
  // edges: the sidebar runs the full height and the command rail sits on the bottom
  // border, so an unclamped ring drew half of itself off screen.
  const ring = rect && (() => {
    const left = Math.max(3, rect.left - 6)
    const top = Math.max(3, rect.top - 6)
    return {
      left,
      top,
      width: Math.max(8, Math.min(rect.right + 6, innerWidth - 3) - left),
      height: Math.max(8, Math.min(rect.bottom + 6, innerHeight - 3) - top),
    }
  })()

  return <div class={`demo-coach ${rect ? 'targeted' : 'centered'}`} role="dialog" aria-label="Demo walkthrough">
    {ring && <div class="demo-coach-flash" style={ring} aria-hidden="true" />}
    {step.gesture && <div
      class={`demo-coach-gesture ${step.gesture}`}
      aria-hidden="true"
    >
      <svg viewBox="0 0 160 60" width="160" height="60">
        <path class="demo-coach-trail" d="M18 30 H132" />
        <path class="demo-coach-head" d="M120 18 L134 30 L120 42" />
        <circle class="demo-coach-finger" cx="18" cy="30" r="9" />
      </svg>
    </div>}
    <section ref={card} class={`demo-coach-card side-${position.side}`} style={{ left: position.left, top: position.top }}>
      <header>
        <span>{step.eyebrow}</span>
        <button onClick={finish} aria-label="Exit the walkthrough">skip tour ×</button>
      </header>
      <div class="demo-coach-copy">
        <h2>{step.title}</h2>
        {step.body}
      </div>
      <footer>
        <span class="demo-coach-count">{index + 1} / {steps.length}</span>
        <div class="demo-coach-progress" aria-hidden="true">
          <i style={{ width: `${((index + 1) / steps.length) * 100}%` }} />
        </div>
        {step.advance
          ? <>
            <em>{step.hint}</em>
            <button class="demo-coach-skip" onClick={() => (last ? finish() : setIndex(value => value + 1))}>skip step</button>
          </>
          : <button class="demo-coach-next" onClick={() => (last ? finish() : setIndex(value => value + 1))}>
            {last ? 'Start playing' : 'Next'}
          </button>}
      </footer>
    </section>
  </div>
}
