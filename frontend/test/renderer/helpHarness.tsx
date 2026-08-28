// The help modal, mounted on its own.
//
// It needs no daemon and no session: every topic's body is generated from the feature docs
// at build time and the registry is pure, which is what makes the whole surface answerable
// in a browser without standing anything up. `?topic=` picks the topic a caller asked for,
// the same way `App` passes one through from a palette command or a drawer's help control.
import { render } from 'preact'
import { HelpModal } from '../../src/HelpModal'
import '../../src/style.css'

declare global {
  interface Window { __helpClosed: boolean; __tourStarted: boolean; __configuratorStarted: boolean }
}

const params = new URLSearchParams(location.search)
window.__helpClosed = false
window.__tourStarted = false
window.__configuratorStarted = false

const root = document.querySelector<HTMLElement>('#root')!
document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

const mark = (id: string) => {
  const node = document.createElement('p')
  node.id = id
  node.textContent = 'yes'
  document.body.appendChild(node)
}

render(
  <HelpModal
    initialTopic={params.get('topic')}
    onClose={() => { window.__helpClosed = true; mark('help-closed') }}
    onStartTutorial={() => { window.__tourStarted = true; mark('tour-started') }}
    onOpenConfigurator={() => { window.__configuratorStarted = true; mark('configurator-started') }}
    configurator={{ enabled: params.get('configurator') !== '0', reason: 'No agent CLI is available' }}
  />,
  root,
)
