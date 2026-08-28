// The guided tour, mounted over an app that carries **none** of its anchors.
//
// That is the point rather than a shortcut. Every anchor-less step is exactly the case
// the tour used to die in: a step carrying an `action` rendered its hint *instead of* a
// control, so a missing anchor left `Exit tour ×` as the only thing on the card. On a
// phone that happened for real at step 10 of 14, and it is reachable on desktop by
// hiding the Notes tab. A walk that cannot be completed from an empty page is a walk
// with a dead end in it somewhere.
//
// `?project=1` picks the branch of the step list a returning user gets; the viewport the
// spec sets before navigating picks the mobile/desktop branch, because the component
// reads `matchMedia` once at mount.
import { render } from 'preact'
import { GuidedTutorial } from '../../src/GuidedTutorial'
import '../../src/style.css'

declare global {
  interface Window { __tutorialEnded: 'exit' | 'complete' | null }
}

const params = new URLSearchParams(location.search)
window.__tutorialEnded = null

const root = document.querySelector<HTMLElement>('#root')!
document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

const draw = () => render(
  window.__tutorialEnded
    ? <p id="tutorial-ended">{window.__tutorialEnded}</p>
    : <GuidedTutorial
        hasProject={params.get('project') === '1'}
        // Deliberately inert: this harness is about the card's own controls, and the app's
        // real `navigateTutorial` would be a second implementation of them here.
        onNavigate={() => {}}
        onExit={() => { window.__tutorialEnded = 'exit'; draw() }}
        onComplete={() => { window.__tutorialEnded = 'complete'; draw() }}
      />,
  root,
)
draw()
