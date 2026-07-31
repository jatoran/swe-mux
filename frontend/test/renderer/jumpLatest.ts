// Harness for the mobile jump-to-latest chip (`.terminal-jump-latest` in TerminalPane).
//
// It mounts the real chip markup and the real stylesheet over a real xterm terminal so a
// synthesized touch tap goes through the same hit-testing the phone does, and it drives the
// two ways the UI can reach the tail:
//
//   chip     -> `term.scrollToBottom()`, which is *relative*: CoreBrowserTerminal turns it
//               into `viewport.scrollLines(baseY - viewportY)` against the DOM scroller.
//   rail key -> `term.input(bytes, true)`, which reaches the tail as a side effect of
//               xterm's `scrollOnUserInput` option: CoreService fires onRequestScrollToBottom
//               and CoreTerminal answers it with `scrollToBottom(true)`, the *absolute*
//               path (`viewport.scrollToLine(baseY, disableSmoothScroll)`).
//
// Those are genuinely different code paths, which is why the pair is measured together in
// every state rather than the chip alone.
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import '../../src/style.css'
import { scrollTerminalToTail } from '../../src/terminalViewport'

type Probe = { viewportY: number; baseY: number; rows: number; onTail: boolean; clicks: number }
export type ScrollCase = { name: string; before: Probe; after: Probe }

declare global {
  interface Window {
    setupJumpLatest: (scenario: Scenario) => Promise<Probe>
    readJumpLatest: () => Probe
    runJumpLatestScenarios: () => Promise<ScrollCase[]>
  }
}

/** Terminal states worth distinguishing; each is set up, then scrolled off the tail. */
export type Scenario =
  | 'plain'
  | 'full-scrollback'
  | 'mouse-tracking'
  | 'alt-screen-round-trip'
  | 'wrapped-narrow'
  | 'repaint'

export const SCENARIOS: Scenario[] = [
  'plain', 'full-scrollback', 'mouse-tracking', 'alt-screen-round-trip', 'wrapped-narrow', 'repaint',
]

const host = document.querySelector<HTMLDivElement>('.terminal-host')!
const chipButton = document.querySelector<HTMLButtonElement>('#chip')!
const railButton = document.querySelector<HTMLButtonElement>('#rail-key')!
const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
const frames = async (count: number) => { for (let i = 0; i < count; i += 1) await frame() }

let term: Terminal
let fit: FitAddon
let clicks = 0

// Exactly what TerminalPane's chip and rail keys do, minus the focus call (no PTY here).
chipButton.addEventListener('click', () => { clicks += 1; scrollTerminalToTail(term) })
railButton.addEventListener('click', () => { clicks += 1; railKey() })

const probe = (): Probe => {
  const buffer = term.buffer.active
  return {
    viewportY: buffer.viewportY,
    baseY: buffer.baseY,
    rows: term.rows,
    // The same test TerminalPane's `syncTail` uses to show the chip, inverted.
    onTail: buffer.viewportY >= buffer.baseY,
    clicks,
  }
}

const write = (data: string) => new Promise<void>(resolve => term.write(data, resolve))

const lines = (count: number, width = 60) =>
  Array.from({ length: count }, (_, index) => `line ${index.toString().padStart(4, '0')} ${'x'.repeat(width)}\r\n`).join('')

const build = (scrollback: number) => {
  term?.dispose()
  host.replaceChildren()
  host.style.height = ''
  clicks = 0
  // TerminalPane's options, minus the theme. Mobile panes never load the WebGL addon
  // (`shouldLoadWebgl` is false under `max-width:760px`), so the DOM renderer here matches.
  term = new Terminal({
    cursorBlink: true, cursorStyle: 'bar', fontFamily: '"Cascadia Mono", Consolas, monospace',
    fontSize: 13, fontWeight: '600', fontWeightBold: '600', lineHeight: 1.2, scrollback,
    allowProposedApi: true, screenReaderMode: false,
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(host)
  fit.fit()
}

/** A Claude-style redraw: rewrite a multi-row box at the bottom on every tick. */
const repaint = async () => {
  await write('\r\n\r\n\r\n\r\n\r\n\r\n')
  for (let tick = 0; tick < 12; tick += 1) {
    await write('\x1b[6A\x1b[J' + Array.from({ length: 6 }, (_, row) => `box ${tick} row ${row}\r\n`).join(''))
  }
}

const setup = async (scenario: Scenario) => {
  build(scenario === 'full-scrollback' ? 200 : 1000)
  switch (scenario) {
    case 'full-scrollback': await write(lines(900)); break
    case 'mouse-tracking': await write(lines(400)); await write('\x1b[?1000h\x1b[?1002h\x1b[?1006h'); break
    case 'alt-screen-round-trip': await write(lines(400)); await write('\x1b[?1049halt\r\n\x1b[?1049l'); break
    case 'wrapped-narrow': await write(lines(400, 300)); break
    case 'repaint': await write(lines(400)); await repaint(); break
    default: await write(lines(400))
  }
  await frames(2)
}

/** Leave the viewport off the tail the way a finger drag does (TerminalPane calls scrollLines). */
window.setupJumpLatest = async (scenario: Scenario) => {
  await setup(scenario)
  term.scrollLines(-30)
  await frames(2)
  return probe()
}

window.readJumpLatest = () => probe()

// ---- programmatic matrix, including the soft keyboard opening under the pane ----

/** The soft keyboard: the pane loses height and TerminalPane refits (`scheduleFit`). */
const openKeyboard = () => { host.style.height = '260px'; fit.fit() }

/** TerminalPane's `sendKey`: xterm's own scrollOnUserInput, finished off the same way. */
const railKey = () => { term.input('\x1b[1;5F', true); scrollTerminalToTail(term) }

const runCase = async (name: string, body: () => Promise<void>): Promise<ScrollCase> => {
  await setup('plain')
  term.scrollLines(-30)
  await frames(2)
  const before = probe()
  await body()
  await frames(3)
  return { name, before, after: probe() }
}

window.runJumpLatestScenarios = async () => [
  // The chip's handler order: scroll, then focus — and focusing is what raises the keyboard.
  await runCase('chip-then-keyboard', async () => { scrollTerminalToTail(term); openKeyboard() }),
  await runCase('rail-key-then-keyboard', async () => { railKey(); openKeyboard() }),
  // The keyboard still animating open when the chip is tapped. The refit has already moved
  // baseY, but xterm republishes its scroller's range on a queued frame (`Viewport.queueSync`
  // off `onResize`), so a single scroll here is clamped to the pre-resize maximum and lands
  // `oldRows - newRows` short. This is the case `scrollTerminalToTail` exists for.
  await runCase('keyboard-then-chip', async () => { openKeyboard(); scrollTerminalToTail(term) }),
  await runCase('keyboard-then-rail-key', async () => { openKeyboard(); railKey() }),
  // Control: already on the tail when the keyboard opens, which must not push it off.
  await runCase('on-tail-then-keyboard', async () => { term.scrollLines(30); await frames(2); openKeyboard() }),
  // The regression itself: one bare `scrollToBottom()` is not enough in that window.
  await runCase('unfixed-keyboard-then-chip', async () => { openKeyboard(); term.scrollToBottom() }),
]
