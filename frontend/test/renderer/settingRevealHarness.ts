// The settings panel's real scrolling shape, for measuring what a deep link actually does.
//
// `revealSetting` promises three things that only a browser can check: the control ends up
// inside the scroller's viewport, it is not left under the sticky section rail that covers the
// top of that viewport, and the same holds on a phone, where the panel is full-screen and the
// rail scrolls horizontally instead of wrapping. The structure and class names below are the
// panel's own (`Settings.tsx`, `style.css`), so the geometry under test is the shipped one.
//
// `?defer=1` withholds the target for 400ms, which is the cold-open case: the panel is asked
// to reveal a control before its data has arrived and the control has not rendered yet.
//
// `?collapsed=1` puts the target inside a closed `<details>`, which is what a settings section
// that folds its reference body away looks like (`.settings-disclosure`, Settings → Voice). A
// closed disclosure `display:none`s its body, so the control is in the DOM with no layout box:
// the reveal has to open the way in rather than wait for a box that will never appear.
import { revealSetting } from '../../src/settingReveal.ts'
import '../../src/style.css'

const params = new URLSearchParams(location.search)
const defer = params.get('defer') === '1'
const collapsed = params.get('collapsed') === '1'

const section = (index: number, extra = '') => `
  <section>
    <h3 data-settings-section="section-${index}">Section ${index}</h3>
    <p>${'Filler prose that gives the scroller something to scroll. '.repeat(6)}</p>
    <label class="check"><span>Toggle ${index}</span><input type="checkbox" /></label>
    ${extra}
  </section>`

const target = `
  <label class="check" data-setting="auto_delivery_enabled">
    <span>Allow auto-delivery for agent conversations</span>
    <input type="checkbox" />
  </label>`
const textTarget = `
  <label data-setting="scan_timeline_model">Scan timeline model<input type="text" value="deepseek" /></label>`
const fold = (body: string) => `
  <details class="settings-disclosure">
    <summary>Reference material</summary>
    ${body}
  </details>`
const targets = collapsed ? fold(target + textTarget) : target + textTarget

const root = document.querySelector<HTMLElement>('#root')!
root.innerHTML = `
  <div class="settings-layer">
    <section class="settings-panel" role="dialog" aria-modal="true" aria-label="Settings">
      <header><div><span>SETTINGS</span><h2>Settings</h2></div></header>
      <div class="settings-body">
        <nav class="settings-tabs" role="tablist" aria-label="Settings sections">
          ${[...Array(8)].map((_, index) => `<button role="tab">Tab ${index + 1}</button>`).join('')}
        </nav>
        <div class="settings-content">
          <div class="settings-section-rail">
            ${[...Array(6)].map((_, index) => `<button>Section ${index + 1}</button>`).join('')}
          </div>
          ${section(1)}${section(2)}${section(3)}
          ${section(4, defer ? '' : targets)}
          ${section(5)}${section(6)}
        </div>
      </div>
      <footer><button class="primary">Save</button></footer>
    </section>
  </div>`

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

const panel = document.querySelector<HTMLElement>('.settings-panel')!

if (defer) {
  setTimeout(() => {
    const late = document.querySelectorAll('.settings-content > section')[3]
    late.insertAdjacentHTML('beforeend', targets)
  }, 400)
}

// The harness drives the module the panel drives, with the same arguments.
Object.assign(window as unknown as Record<string, unknown>, {
  revealHarness: (setting: string, behavior: ScrollBehavior = 'auto', coarsePointer = false) =>
    revealSetting(panel, setting, { behavior, coarsePointer }),
})
