import { dt } from './messages.ts'
/**
 * The demo's own bar, drawn only when the demo is the whole page.
 *
 * Inside the embed the landing page carries these three controls above the frame, and the
 * frame itself is nothing but the app. Opened full screen there is no landing page left,
 * so a visitor arrives in an interface with no way back, no way to play a scenario, and no
 * way to undo whatever they have done to the fixture. That is not a demo, it is a trap
 * with a Back button.
 *
 * It is deliberately not styled as app chrome. Everything below it is the shipped
 * frontend, and a strip that looked like part of it would be the one piece of this page
 * making a false claim - so it reads as what it is: the page's frame around the product.
 *
 * The app fits underneath rather than behind: `demoBar.css` shortens `.app-shell` by the
 * bar's height. The app computes `--app-height` from the visual viewport and rewrites it
 * on every keyboard open, so subtracting in CSS is the only place the arithmetic survives.
 */
import { DemoLanguage } from './DemoLanguage.tsx'
import { DemoKeymaps } from './DemoKeymaps.tsx'
import { useEffect, useState } from 'preact/hooks'
import { scenarioMenu, start, stop, subscribeDirector } from './director.ts'

/** Everything this origin's demo persists: the fixture, the app's own presentation, and
 *  the editor's. The landing page's reset button clears exactly this set. */
const DEMO_KEYS = /^(mux\.|swemux-demo|continuity-editor\.)/

function resetDemo(): void {
  try {
    const doomed: string[] = []
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index)
      if (key && DEMO_KEYS.test(key)) doomed.push(key)
    }
    for (const key of doomed) localStorage.removeItem(key)
  } catch { /* private mode: the reload alone is the reset */ }
  location.reload()
}

export function DemoBar() {
  const [running, setRunning] = useState(false)
  useEffect(() => subscribeDirector(view => setRunning(view.running)), [])
  const scenarios = scenarioMenu()

  return <div class="demo-bar">
    {/* A link rather than `history.back()`: the visitor may have arrived here directly,
        from a share or a bookmark, and a back button that does nothing is worse than a
        link that always goes somewhere. */}
    <a class="demo-bar-exit" href="/" title={dt("demo.8573bb969f")}>← swemux.dev</a>
    <span class="demo-bar-kick">{dt("demo.4ff006ca6b")}</span>
    <DemoLanguage />
    <DemoKeymaps />
    <label class="demo-bar-pick">
      <span>{dt("demo.d1a1c36265")}</span>
      <select
        value=""
        title={dt("demo.8565a725ac")}
        onChange={event => {
          const chosen = event.currentTarget.value
          event.currentTarget.value = ''
          if (chosen) void start(chosen)
        }}
      >
        <option value="">{dt("demo.d1a1c36265")}</option>
        {scenarios.map(entry => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
      </select>
    </label>
    {running && <button class="demo-bar-stop" onClick={() => stop('dismissed')}>{dt("demo.6c45cb72a3")}</button>}
    <button class="demo-bar-reset" onClick={resetDemo} title={dt("demo.8902d9ed9a")}>{dt("demo.01be30bb4a")}</button>
  </div>
}
