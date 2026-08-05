import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { reflowVisibleTerminalRenderer } from '../../src/terminalViewport'
import {
  dispatchTerminalMouseTap,
  resolveCodexCaretTarget,
  type TerminalCaretSnapshot,
} from '../../src/terminalCaretPlacement'

type DomDimensionRepairResult = {
  before: { width: string; height: string }
  afterFit: { width: string; height: string }
  afterReflow: { width: string; height: string }
  cols: number
  rows: number
}

type LetterboxExitRepairResult = {
  base: { width: string; height: string }
  letterboxed: { width: string; height: string }
  afterFontRestore: { width: string; height: string }
  afterReflow: { width: string; height: string }
  cols: number
  rows: number
}

type MobileCursorInitializationResult = {
  beforeInitialized: boolean
  afterInitialized: boolean
  inactiveBar: boolean
  mobileInputFocused: boolean
}

type SyntheticMouseTapResult = {
  tracking: string
  reports: string[]
}

type UnstyledCodexCaretResult = {
  prefixBgMode: number
  current: { column: number; row: number } | null
  target: { column: number; row: number } | null
}

declare global {
  interface Window {
    runTerminalRendererStress: () => Promise<{ renderer: 'dom' | 'webgl'; cols: number; rows: number }>
    runTerminalDomDimensionRepair: () => Promise<DomDimensionRepairResult>
    runLetterboxExitRepair: () => Promise<LetterboxExitRepairResult>
    runTerminalMobileCursorInitialization: () => Promise<MobileCursorInitializationResult>
    runTerminalSyntheticMouseTap: () => Promise<SyntheticMouseTapResult>
    runUnstyledCodexCaretResolution: () => Promise<UnstyledCodexCaretResult>
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

/**
 * The production letterbox-exit sequence, which `runTerminalDomDimensionRepair` does not
 * cover: it shrinks the *surface* directly, whereas leaving a letterbox restores the
 * **font** at a grid that has not changed. Whether xterm re-measures the surface on a font
 * change alone is the question that decides whether the exit path needs an explicit reflow
 * at all, so this reports every stage rather than asserting one.
 */
window.runLetterboxExitRepair = async () => {
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
  await writeTo(domTerm, `${'letterbox exit surface\r\n'.repeat(20)}`)
  await frame()

  const screen = domHost.querySelector<HTMLElement>('.xterm-screen')!
  const dimensions = () => ({ width: screen.style.width, height: screen.style.height })
  const base = dimensions()
  const grid = { cols: domTerm.cols, rows: domTerm.rows }

  // Enter a letterbox: another device's grid drawn at a reduced font.
  domTerm.options.fontSize = 7
  await frame()
  const letterboxed = dimensions()

  // Leave it the way `applyGeometry` does: restore the base font at the same grid, then
  // refit. FitAddon skips `term.resize` because cols/rows are unchanged.
  domTerm.resize(grid.cols, grid.rows)
  domTerm.options.fontSize = 12
  domFit.fit()
  await frame()
  const afterFontRestore = dimensions()

  reflowVisibleTerminalRenderer(domTerm, domHost)
  await frame()
  const afterReflow = dimensions()

  const result = { base, letterboxed, afterFontRestore, afterReflow, cols: domTerm.cols, rows: domTerm.rows }
  domTerm.dispose()
  return result
}

window.runTerminalMobileCursorInitialization = async () => {
  host.style.display = 'none'
  const domHost = document.querySelector<HTMLDivElement>('#dom-terminal')!
  const mobileInput = document.querySelector<HTMLTextAreaElement>('#mobile-input')!
  domHost.style.display = 'block'
  const domTerm = new Terminal({
    cursorBlink: true,
    cursorStyle: 'bar',
    cursorInactiveStyle: 'bar',
    cursorWidth: 2,
    fontFamily: 'Consolas, monospace',
    fontSize: 12,
    theme: { background: '#090a0c', foreground: '#d9dde2', cursor: '#ffffff' },
  })
  const domFit = new FitAddon()
  domTerm.loadAddon(domFit)
  domTerm.open(domHost)
  domFit.fit()

  // Codex uses the normal screen and explicitly enables the cursor. Unlike Claude's
  // alternate-screen startup, that does not initialize xterm's cursor renderer.
  await writeTo(domTerm, '\x1b[?2026h\x1b[2J\x1b[H› \x1b[0 q\x1b[?25h\x1b[1;3H\x1b[?2026l')
  await frame()
  const beforeInitialized = domHost.querySelector('.xterm-cursor') !== null

  // Production bootstraps xterm once, then leaves the external IME bridge focused.
  domTerm.focus()
  mobileInput.focus({ preventScroll: true })
  await frame()
  await frame()
  const cursor = domHost.querySelector('.xterm-cursor')
  const result = {
    beforeInitialized,
    afterInitialized: cursor !== null,
    inactiveBar: cursor?.classList.contains('xterm-cursor-bar') ?? false,
    mobileInputFocused: document.activeElement === mobileInput,
  }
  domTerm.dispose()
  return result
}

window.runTerminalSyntheticMouseTap = async () => {
  host.style.display = 'none'
  const domHost = document.querySelector<HTMLDivElement>('#dom-terminal')!
  domHost.style.display = 'block'
  const domTerm = new Terminal({fontFamily:'Consolas, monospace',fontSize:12})
  const domFit = new FitAddon()
  domTerm.loadAddon(domFit)
  domTerm.open(domHost)
  domFit.fit()
  const reports:string[]=[]
  const input=domTerm.onData(data=>reports.push(data))
  await writeTo(domTerm,'\x1b[?1000h\x1b[?1006h')
  const screen=domHost.querySelector<HTMLElement>('.xterm-screen')!
  const rect=screen.getBoundingClientRect()
  dispatchTerminalMouseTap(screen,rect.left+rect.width/2,rect.top+rect.height/2)
  const result={tracking:domTerm.modes.mouseTrackingMode,reports}
  input.dispose()
  domTerm.dispose()
  return result
}

window.runUnstyledCodexCaretResolution = async () => {
  host.style.display = 'none'
  const domHost = document.querySelector<HTMLDivElement>('#dom-terminal')!
  domHost.style.display = 'block'
  const domTerm = new Terminal({fontFamily:'Consolas, monospace',fontSize:12})
  const domFit = new FitAddon()
  domTerm.loadAddon(domFit)
  domTerm.open(domHost)
  domFit.fit()

  const promptRow=Math.max(2,domTerm.rows-4)
  await writeTo(domTerm,`\x1b[2J\x1b[H\x1b[${promptRow+1};1H\x1b[1m›\x1b[22m alpha beta`)
  await writeTo(domTerm,`\x1b[${promptRow+3};3H100% left\x1b[${promptRow+1};8H`)
  const snapshot=caretSnapshot(domTerm)
  const result=resolveCodexCaretTarget(snapshot,{column:4,row:promptRow})
  const prefixBgMode=snapshot.lines[promptRow-snapshot.viewportY]?.cells[0]?.bgMode??-1
  domTerm.dispose()
  return {prefixBgMode,current:result?.current??null,target:result?.target??null}
}

function caretSnapshot(target:Terminal):TerminalCaretSnapshot {
  const buffer=target.buffer.active
  const lines=[]
  for(let row=buffer.viewportY;row<buffer.viewportY+target.rows;row+=1){
    const line=buffer.getLine(row)
    const cells=[]
    for(let column=0;column<target.cols;column+=1){
      const cell=line?.getCell(column)
      cells.push({
        chars:cell?.getChars()??'',
        code:cell?.getCode()??0,
        width:cell?.getWidth()??1,
        bgMode:cell?.getBgColorMode()??0,
        bg:cell?.getBgColor()??0,
        dim:cell?.isDim()===1,
      })
    }
    lines.push({row,cells})
  }
  return {
    cols:target.cols,
    rows:target.rows,
    viewportY:buffer.viewportY,
    baseY:buffer.baseY,
    cursorX:buffer.cursorX,
    cursorY:buffer.cursorY,
    lines,
  }
}

function writeTo(target: Terminal, data: string): Promise<void> {
  return new Promise(resolve => target.write(data, resolve))
}
