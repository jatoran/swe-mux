import { render } from 'preact'
import { useState } from 'preact/hooks'
import { Dropdown } from '../../src/Dropdown'
import '../../src/style.css'

// The dropdown on its own, in the three placements that decide whether it can replace a
// native `<select>` everywhere: a plain settings row, a control near the bottom of the
// viewport (which has to flip), and a control inside an `overflow:auto` scroller (which a
// non-portalled list would be clipped by, and which is most of the app's surfaces).
//
// The long list is deliberately long and alphabetical: opening at the current value and
// scrolling a list without choosing from it are only observable on a list taller than its
// own panel.

const LONG = Array.from({ length: 60 }, (_, index) => ({
  value: `row-${index}`,
  label: `${String.fromCharCode(97 + (index % 26))}${index} option`,
}))

const SHORT = [
  { value: 'message', label: 'message' },
  { value: 'author', label: 'author' },
  { value: 'path', label: 'path', disabled: true },
  { value: 'sonnet', label: 'sonnet' },
  { value: 'summary', label: 'summary' },
]

function Harness() {
  const [long, setLong] = useState('row-40')
  const [short, setShort] = useState('message')
  const [scrolled, setScrolled] = useState('author')
  const [low, setLow] = useState('row-3')
  return <div class="settings-panel dropdown-harness">
    <label id="long-row" for="long-picker">Catalog
      <Dropdown id="long-picker" value={long} options={LONG} onChange={setLong}/>
    </label>
    <label id="short-row">Search field
      <Dropdown value={short} options={SHORT} onChange={setShort}/>
    </label>
    <div class="dropdown-harness-scroller" id="scroller">
      <div class="dropdown-harness-tall">
        <label id="scrolled-row">Inside a scroller
          <Dropdown value={scrolled} options={SHORT} onChange={setScrolled}/>
        </label>
      </div>
    </div>
    <div class="dropdown-harness-low">
      <label id="low-row">Near the fold
        <Dropdown value={low} options={LONG} onChange={setLow}/>
      </label>
    </div>
    <output id="chosen">{`${long}|${short}|${scrolled}|${low}`}</output>
  </div>
}

const style = document.createElement('style')
style.textContent = `
  .dropdown-harness{padding:12px;display:grid;gap:12px}
  .dropdown-harness-scroller{height:120px;overflow:auto;border:1px solid var(--line)}
  .dropdown-harness-tall{height:400px;padding-top:40px}
  .dropdown-harness-low{position:fixed;left:12px;right:12px;bottom:8px}
`
document.head.append(style)

render(<Harness/>, document.querySelector('#root')!)
