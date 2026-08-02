import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { reflowVisibleTerminalRenderer } from '../../src/terminalViewport'

type DomDimensionRepairResult = {
  before: { width: string; height: string }
  afterFit: { width: string; height: string }
  afterReflow: { width: string; height: string }
  cols: number
  rows: number
}

declare global {
  interface Window {
    runTerminalRendererStress: () => Promise<{ renderer: 'dom' | 'webgl'; cols: number; rows: number }>
    runTerminalDomDimensionRepair: () => Promise<DomDimensionRepairResult>
  }
}

const host = document.querySelector<HTMLDivElement>('#terminal')!
const term = new Terminal({
  cursorBlink: false,
  cursorInactiveStyle: 'none',
  fontFamily: 'Consolas, monospace',
  fontSize: 12,
  scrollback: 300,
  theme: { background: '#090a0c', foreground: '#d9dde2' },
})
const fit = new FitAddon()
term.loadAddon(fit)
term.open(host)
// Match production: hidden canvases must retain the pixels xterm's render model
// assumes are still present when unchanged cells are skipped on the next frame.
const webgl = new WebglAddon(true)
let webglContextLost = false
webgl.onContextLoss(() => { webglContextLost = true })
term.loadAddon(webgl)
fit.fit()

const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
const write = (data: string) => new Promise<void>(resolve => term.write(data, resolve))

window.runTerminalRendererStress = async () => {
  const seed = Array.from({ length: 900 }, (_, index) =>
    `seed ${index.toString().padStart(4, '0')} :: ${'abcdef0123456789 '.repeat(6)}\r\n`
  ).join('')
  term.write(seed)

  for (let index = 0; index < 160; index += 1) {
    if (index % 11 === 0) {
      host.style.display = 'none'
      await frame()
      host.style.display = 'block'
    }
    host.style.width = `${720 + (index % 9) * 29}px`
    host.style.height = `${360 + (index % 7) * 31}px`
    fit.fit()
    term.write(`switch ${index.toString().padStart(3, '0')} ${'viewport '.repeat(18)}\r\n`)
    if (index % 4 === 0) await frame()
  }

  await write('\r\nrender queue drained\r\n')
  host.style.display = 'block'
  host.style.width = '920px'
  host.style.height = '560px'
  fit.fit()
  await frame()
  await frame()
  return { renderer: webglContextLost ? 'dom' : 'webgl', cols: term.cols, rows: term.rows }
}

window.runTerminalDomDimensionRepair = async () => {
  host.style.display = 'none'
  const domHost = document.querySelector<HTMLDivElement>('#dom-terminal')!
  domHost.style.display = 'block'
  const domTerm = new Terminal({
    cursorBlink: false,
    fontFamily: 'Consolas, monospace',
    fontSize: 12,
    theme: { background: '#090a0c', foreground: '#d9dde2' },
  })
  const domFit = new FitAddon()
  domTerm.loadAddon(domFit)
  domTerm.open(domHost)
  domFit.fit()
  await writeTo(domTerm, `${'same-grid renderer dimensions\r\n'.repeat(20)}`)
  await frame()

  const screen = domHost.querySelector<HTMLElement>('.xterm-screen')!
  const dimensions = () => ({ width: screen.style.width, height: screen.style.height })
  const before = dimensions()
  screen.style.width = `${Number.parseFloat(before.width) / 2}px`
  screen.style.height = `${Number.parseFloat(before.height) / 2}px`

  // This is the production failure: FitAddon sees unchanged cols/rows and skips resize,
  // leaving renderer pixels at their stale upper-left dimensions.
  domFit.fit()
  const afterFit = dimensions()
  reflowVisibleTerminalRenderer(domTerm, domHost)
  await frame()
  const afterReflow = dimensions()
  const result = { before, afterFit, afterReflow, cols: domTerm.cols, rows: domTerm.rows }
  domTerm.dispose()
  return result
}

function writeTo(target: Terminal, data: string): Promise<void> {
  return new Promise(resolve => target.write(data, resolve))
}
