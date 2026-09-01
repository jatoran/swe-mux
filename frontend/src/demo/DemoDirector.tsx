/**
 * Everything the director draws: a caption, a spotlight ring, a ghost cursor, a ripple,
 * and the two controls a visitor needs (advance, stop).
 *
 * It renders *beside* `<App/>` rather than inside it, so the product build cannot
 * accidentally ship it, and it holds no state of its own beyond what it measures - the
 * run lives in `director.ts`, which knows nothing about the DOM it is being drawn into.
 * That split is what lets the engine be unit-tested without a browser and lets this file
 * stay a view.
 *
 * The one piece of real logic here is the ring: it is re-measured on every DOM change
 * rather than on a timer, because the chrome a beat points at moves when a panel opens,
 * and a stale ring on a moved control is worse than no ring at all.
 */
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { placeTutorialCard } from '../tutorial.ts'
import { DemoShow } from './DemoShow.tsx'
import {
  advanceBeat, directorSnapshot, dismissResume, resume, stop, subscribeDirector,
  type DirectorSnapshot,
} from './director.ts'
import { firstVisible } from './drive.ts'

/**
 * Where a dodging card lands when it goes upwards.
 *
 * Clear of the full-screen demo's own bar (`--demo-bar-h`, 34px), which is drawn outside
 * the app and would otherwise clip the card's header and its stop button. In the embed
 * there is no bar and this is simply an inset.
 */
const TOP_DODGE = 44

const sameRect = (left: DOMRect | null, right: DOMRect | null): boolean =>
  left === right || Boolean(left && right
    && Math.abs(left.left - right.left) < 0.5 && Math.abs(left.top - right.top) < 0.5
    && Math.abs(left.width - right.width) < 0.5 && Math.abs(left.height - right.height) < 0.5)

export function DemoDirector() {
  const [view, setView] = useState<DirectorSnapshot>(directorSnapshot)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const [cardSize, setCardSize] = useState({ width: 360, height: 230 })
  const card = useRef<HTMLElement>(null)

  useEffect(() => subscribeDirector(setView), [])

  const selectors = view.spotlight
  const key = useMemo(() => (selectors || []).join('|'), [selectors])

  useEffect(() => {
    if (!view.running) { setRect(null); return }
    let frame = 0
    const measure = (): void => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const found = firstVisible(selectors || undefined)
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
    observer.observe(document.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'],
    })
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [view.running, key])

  // A run the visitor's own press ended collapses to an offer rather than vanishing. It
  // is deliberately small and out of the way: they took over on purpose, and the demo has
  // no business asking them to justify it. The wrapper carries `demo-director` so the
  // abort listener's own exemption covers these two buttons.
  if (!view.running) {
    if (!view.paused) return null
    return <div class="demo-director demo-director-resume" role="status">
      <button
        class="demo-director-next"
        title={view.paused.label}
        onClick={() => void resume()}
      >resume</button>
      <span class="demo-director-count">{view.paused.index} / {view.paused.total}</span>
      <button aria-label="Dismiss the demo walkthrough" onClick={dismissResume}>×</button>
    </div>
  }

  // A card with a body is the walkthrough's, and it follows the chrome it explains. A
  // caption belongs to a scripted run and stays at the foot of the frame - see the CSS.
  const anchored = view.body.length > 0
  const placed = placeTutorialCard(rect, { width: innerWidth, height: innerHeight }, cardSize)
  // A gesture step has no anchor, so the card would centre itself - directly over the
  // glyph that is the whole instruction. It sits at the foot of the screen instead,
  // which is also where a thumb is.
  //
  // A beat with callouts moves for a different reason and the same distance: the card is
  // placed beside the chrome it explains, which is exactly where that beat's labels are,
  // and it covered three of the six on the fleet step. The labels are the instruction
  // there, so the card is what gives way.
  //
  // *Which* way it gives is measured off the subject rather than fixed, because "the foot
  // of the frame" stopped being empty when the callouts learned a horizontal gutter: the
  // command rail sits on the bottom border, its labels are now drawn just above it, and a
  // card parked at the foot covered them. So a subject in the lower part of the frame
  // sends the card to the top and everything else keeps the foot, which is where a card
  // belongs when the thing it explains is the sidebar or a dialog.
  const dodges = view.gesture || (view.show?.notes?.length ?? 0) > 0
  const lowSubject = Boolean(rect) && rect!.top + rect!.height / 2 > innerHeight * 0.6
  const position = dodges
    ? { ...placed, top: lowSubject ? TOP_DODGE : Math.max(16, innerHeight - cardSize.height - 20) }
    : placed
  const last = view.index >= view.total
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

  const cardStyle = anchored
    ? { left: position.left, top: position.top }
    : { bottom: 18, top: 'auto' as const }

  return <div class={`demo-director ${rect ? 'targeted' : 'centered'}`} role="dialog" aria-label="Demo walkthrough">
    {/* The beat's own show layer. Keyed by the sequence number so a new beat mounts a
        fresh one rather than reusing the previous beat's elements, which would inherit
        their finished animations and draw nothing. */}
    {view.show && <DemoShow key={`show-${view.showSeq}`} show={view.show} seq={view.showSeq} />}
    {ring && <div class="demo-director-flash" style={ring} aria-hidden="true" />}
    {view.pointer && <svg
      class="demo-director-ghost"
      style={{ left: view.pointer.x, top: view.pointer.y }}
      viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"
    >
      <path d="M4 2 L4 18 L8.4 14.2 L11.2 20.6 L14.2 19.3 L11.5 13.2 L17.4 12.6 Z" />
    </svg>}
    {view.pointer && view.press > 0 && <i
      key={`press-${view.press}`}
      class="demo-director-ripple"
      style={{ left: view.pointer.x, top: view.pointer.y }}
      aria-hidden="true"
    />}
    {view.echo && <i
      key={`echo-${view.echo.seq}`}
      class="demo-director-ripple echo"
      style={{ left: view.echo.x, top: view.echo.y }}
      aria-hidden="true"
    />}
    {view.gesture && <div class={`demo-director-gesture ${view.gesture}`} aria-hidden="true">
      <svg viewBox="0 0 160 60" width="160" height="60">
        <path class="demo-director-trail" d="M18 30 H132" />
        <path class="demo-director-head" d="M120 18 L134 30 L120 42" />
        <circle class="demo-director-finger" cx="18" cy="30" r="9" />
      </svg>
    </div>}
    <section
      ref={card}
      class={`demo-director-card ${anchored ? `side-${position.side}` : 'caption'}`}
      style={cardStyle}
    >
      <header>
        <span>{view.eyebrow || view.blurb}</span>
        {/* The explicit stop. Present for every run, gated or not: something driving the
            screen must always be dismissible by the person watching it. */}
        <button onClick={() => stop('dismissed')} aria-label="Stop the demo walkthrough">stop ×</button>
      </header>
      <div class="demo-director-copy">
        {view.say && <h2>{view.say}</h2>}
        {view.body.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
      </div>
      {anchored && <footer>
        <span class="demo-director-count">{view.index} / {view.total}</span>
        <div class="demo-director-progress" aria-hidden="true">
          <i style={{ width: `${(view.index / Math.max(1, view.total)) * 100}%` }} />
        </div>
        {/* An offer rather than a demand: the run advances on its own, and this is for
            somebody who reads faster than it plays. There used to be a second branch here
            for a gated beat ("skip step", beside the instruction it was waiting on); the
            walkthrough drives itself now, so there is one button and it always means the
            same thing. */}
        <button class="demo-director-next" onClick={() => (last ? stop('dismissed') : advanceBeat())}>
          {last ? 'Start playing' : 'Next'}
        </button>
      </footer>}
    </section>
  </div>
}
