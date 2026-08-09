import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { DEFAULT_CLAUDE_MAX_COLUMNS, claudeHostMaxWidth, reflowVisibleTerminalRenderer, terminalWidthPolicyFontSize } from '../../src/terminalViewport'
import { terminalRenderControl } from '../../src/terminalRenderPause'
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

type DecrqmProbeResult = {
  painted: string
  responses: string[]
  writeCompleted: boolean
}

type TerminalWidthPolicyResult = {
  codex: { initialCols: number; finalCols: number; fontSize: number }
  claude: { cols: number; hostWidth: number; repeatedCols: number[]; repeatedWidths: number[] }
}

type WarmPaneRenderCostResult = {
  writes: number
  warmRenders: number
  displayNoneRenders: number
  pauseAvailable: boolean
  pausedRenders: number
  resumedRenders: number
  resumedRowText: string
}

type WarmPaneCycleSoakResult = {
  cols: number
  rows: number
  proposedCols: number
  proposedRows: number
  renderedRowElements: number
  baseY: number
  viewportY: number
  scrollbackLines: number
}

declare global {
  interface Window {
    runTerminalRendererStress: () => Promise<{ renderer: 'dom' | 'webgl'; cols: number; rows: number }>
    runTerminalDomDimensionRepair: () => Promise<DomDimensionRepairResult>
    runLetterboxExitRepair: () => Promise<LetterboxExitRepairResult>
    runTerminalMobileCursorInitialization: () => Promise<MobileCursorInitializationResult>
    runTerminalSyntheticMouseTap: () => Promise<SyntheticMouseTapResult>
    runUnstyledCodexCaretResolution: () => Promise<UnstyledCodexCaretResult>
    runDecrqmProbe: () => Promise<DecrqmProbeResult>
    runTerminalWidthPolicies: () => Promise<TerminalWidthPolicyResult>
    runWarmPaneRenderCost: () => Promise<WarmPaneRenderCostResult>
    runWarmPaneCycleSoak: () => Promise<WarmPaneCycleSoakResult>
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

/**
 * oh-my-pi's measured startup probe barrage, DECRQM included. xterm 6.0.0's
 * DECRQM handler is the one sequence the production bundler used to break
 * (see scripts/patch-xterm-requestmode.mjs): the first `$p` threw and killed
 * the write loop, so nothing written afterwards ever rendered. The probe
 * asserts the write completes, the paint after the queries reaches the grid,
 * and every mode query got a DECRPM `$y` answer on the onData path.
 */
window.runDecrqmProbe = async () => {
  host.style.display = 'none'
  const domHost = document.querySelector<HTMLDivElement>('#dom-terminal')!
  domHost.style.display = 'block'
  const domTerm = new Terminal({fontFamily:'Consolas, monospace',fontSize:12})
  const domFit = new FitAddon()
  domTerm.loadAddon(domFit)
  domTerm.open(domHost)
  domFit.fit()
  const responses:string[]=[]
  const input=domTerm.onData(data=>responses.push(data))
  // Captured 2026-08-06 from installed omp 17.2.10 on ConPTY: kitty query,
  // repeated DA1, OSC 11 background probe, then five DECRQM mode queries.
  // A parser crash never invokes write callbacks again, so the writes are
  // raced against a timeout instead of awaited: the broken-bundle failure
  // mode is a hang, and the probe must report it rather than inherit it.
  let writeCompleted=false
  const writes=(async()=>{
    await writeTo(
      domTerm,
      '\x1b[?2004h\x1b[?1l\x1b>\x1b[?u\x1b[c\x1b]11;?\x07\x1b[c\x1b[?2031h\x1b[?2026$p\x1b[c\x1b[?2048$p\x1b[c\x1b[?2031$p\x1b[c\x1b[?1010$p\x1b[c\x1b[?1011$p\x1b[c\x1b[$p',
    )
    await writeTo(domTerm,'\r\nomp probe survived\r\n')
    writeCompleted=true
  })()
  await Promise.race([writes,new Promise(resolve=>setTimeout(resolve,3000))])
  await frame()
  const buffer=domTerm.buffer.active
  const lines:string[]=[]
  for(let row=0;row<domTerm.rows;row+=1)lines.push(buffer.getLine(row)?.translateToString(true)??'')
  const result={painted:lines.join('\n'),responses,writeCompleted}
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

window.runTerminalWidthPolicies = async () => {
  host.style.display = 'none'
  const domHost = document.querySelector<HTMLDivElement>('#dom-terminal')!
  domHost.style.display = 'block'
  domHost.style.width = '460px'
  domHost.style.height = '420px'
  domHost.style.maxWidth = 'none'
  const codexTerm = new Terminal({fontFamily:'Consolas, monospace',fontSize:11})
  const codexFit = new FitAddon()
  codexTerm.loadAddon(codexFit)
  codexTerm.open(domHost)
  const initial = codexFit.proposeDimensions()!
  const fontSize = terminalWidthPolicyFontSize('codex', false, initial.cols, 11)
  codexTerm.options.fontSize = fontSize
  const final = codexFit.proposeDimensions()!
  codexTerm.resize(final.cols, final.rows)
  const codex = {initialCols:initial.cols,finalCols:codexTerm.cols,fontSize}
  codexTerm.dispose()

  const claudeGrid = document.createElement('div')
  claudeGrid.style.display = 'grid'
  claudeGrid.style.width = '1800px'
  claudeGrid.style.height = '420px'
  domHost.parentElement!.insertBefore(claudeGrid, domHost)
  claudeGrid.append(domHost)
  domHost.style.width = '100%'
  domHost.style.height = '420px'
  domHost.style.maxWidth = claudeHostMaxWidth(DEFAULT_CLAUDE_MAX_COLUMNS)
  domHost.style.justifySelf = 'center'
  domHost.style.fontFamily = 'Consolas, monospace'
  domHost.style.fontSize = '11px'
  domHost.style.fontWeight = '600'
  const claudeTerm = new Terminal({fontFamily:'Consolas, monospace',fontSize:11,fontWeight:'600'})
  const claudeFit = new FitAddon()
  claudeTerm.loadAddon(claudeFit)
  claudeTerm.open(domHost)
  claudeFit.fit()
  const repeatedCols = [claudeTerm.cols]
  const repeatedWidths = [domHost.clientWidth]
  for (let attempt = 0; attempt < 8; attempt += 1) {
    claudeFit.fit()
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    repeatedCols.push(claudeTerm.cols)
    repeatedWidths.push(domHost.clientWidth)
  }
  const claude = {cols:claudeTerm.cols,hostWidth:domHost.clientWidth,repeatedCols,repeatedWidths}
  claudeTerm.dispose()
  claudeGrid.replaceWith(domHost)
  return {codex,claude}
}

// Production `.pane-warm` CSS (style.css): measurable box, no paint, no interaction.
const applyWarmPaneCss = (element: HTMLElement) => {
  element.style.position = 'absolute'
  element.style.inset = '0'
  element.style.width = '100%'
  element.style.height = '100%'
  element.style.visibility = 'hidden'
  element.style.pointerEvents = 'none'
}

const clearWarmPaneCss = (element: HTMLElement) => {
  element.style.position = ''
  element.style.inset = ''
  element.style.visibility = ''
  element.style.pointerEvents = ''
  element.style.width = '100%'
  element.style.height = '100%'
}

const makeStack = (): HTMLDivElement => {
  const stack = document.createElement('div')
  stack.style.position = 'relative'
  stack.style.width = '640px'
  stack.style.height = '384px'
  document.body.append(stack)
  return stack
}

window.runWarmPaneRenderCost = async () => {
  // xterm pauses its render service from an IntersectionObserver, which is geometric:
  // a `visibility:hidden` box with real dimensions still intersects, while
  // `display:none` does not. This measures what that difference costs a warm pane
  // that keeps receiving live output — the deliberate price of keeping cell metrics
  // valid so the terminal model never parses against a zero-sized renderer.
  host.style.display = 'none'
  const stack = makeStack()
  const makePane = () => {
    const paneHost = document.createElement('div')
    paneHost.style.width = '100%'
    paneHost.style.height = '100%'
    stack.append(paneHost)
    const paneTerm = new Terminal({ fontFamily: 'Consolas, monospace', fontSize: 12, scrollback: 300 })
    const paneFit = new FitAddon()
    paneTerm.loadAddon(paneFit)
    paneTerm.open(paneHost)
    paneFit.fit()
    return { paneHost, paneTerm }
  }
  const warm = makePane()
  const none = makePane()
  const paused = makePane()
  applyWarmPaneCss(warm.paneHost)
  none.paneHost.style.display = 'none'
  // The production warm pane: measurable box, renderer explicitly paused.
  applyWarmPaneCss(paused.paneHost)
  const pausedControl = terminalRenderControl(paused.paneTerm)
  const pauseAvailable = pausedControl.available && pausedControl.pause()
  // Let the IntersectionObserver deliver the styled states before streaming.
  await frame()
  await frame()

  let warmRenders = 0
  let displayNoneRenders = 0
  let pausedRenders = 0
  const warmRender = warm.paneTerm.onRender(() => { warmRenders += 1 })
  const noneRender = none.paneTerm.onRender(() => { displayNoneRenders += 1 })
  const pausedRender = paused.paneTerm.onRender(() => { pausedRenders += 1 })
  let writes = 0
  // An OMP-style live region: rewrite one line per tick, no committed newlines.
  for (let tick = 0; tick < 45; tick += 1) {
    const line = `\r\x1b[K spinner ${tick.toString().padStart(3, '0')} waiting`
    warm.paneTerm.write(line)
    none.paneTerm.write(line)
    paused.paneTerm.write(line)
    writes += 1
    await frame()
  }
  await new Promise<void>(resolve => warm.paneTerm.write('', resolve))
  await new Promise<void>(resolve => none.paneTerm.write('', resolve))
  await new Promise<void>(resolve => paused.paneTerm.write('', resolve))
  await frame()
  const pausedRendersWhileHidden = pausedRenders
  // Reveal the paused pane the way production does: clear the CSS, resume, and the
  // renderer's own recovery repaints everything parsed while paused.
  clearWarmPaneCss(paused.paneHost)
  pausedControl.resume()
  await frame()
  await frame()
  const resumedRenders = pausedRenders - pausedRendersWhileHidden
  const buffer = paused.paneTerm.buffer.active
  const activeRow = buffer.getLine(buffer.viewportY + buffer.cursorY)
  const resumedRowText = activeRow?.translateToString(true) ?? ''
  warmRender.dispose()
  noneRender.dispose()
  pausedRender.dispose()
  warm.paneTerm.dispose()
  none.paneTerm.dispose()
  paused.paneTerm.dispose()
  stack.remove()
  return {
    writes,
    warmRenders,
    displayNoneRenders,
    pauseAvailable,
    pausedRenders: pausedRendersWhileHidden,
    resumedRenders,
    resumedRowText,
  }
}

window.runWarmPaneCycleSoak = async () => {
  // Repeated active↔warm cycling with output arriving while warm: the regression
  // class the retained-measurable-warm-pane work exists for. After the final reveal
  // and fit, the grid, the rendered DOM rows, and the tail must all agree.
  host.style.display = 'none'
  const stack = makeStack()
  const paneHost = document.createElement('div')
  paneHost.style.width = '100%'
  paneHost.style.height = '100%'
  stack.append(paneHost)
  const paneTerm = new Terminal({ fontFamily: 'Consolas, monospace', fontSize: 12, scrollback: 500 })
  const paneFit = new FitAddon()
  paneTerm.loadAddon(paneFit)
  paneTerm.open(paneHost)
  paneFit.fit()
  const writeLines = async (label: string) => {
    for (let line = 0; line < 30; line += 1) {
      paneTerm.write(`${label} line ${line.toString().padStart(3, '0')} ${'content '.repeat(8)}\r\n`)
    }
    await new Promise<void>(resolve => paneTerm.write('', resolve))
  }
  await writeLines('seed')
  for (let cycle = 0; cycle < 10; cycle += 1) {
    applyWarmPaneCss(paneHost)
    await frame()
    await writeLines(`warm-${cycle}`)
    clearWarmPaneCss(paneHost)
    // The active pane also changes size while this one is warm; production repairs
    // that with a reveal-time fit, mirrored here.
    stack.style.width = `${560 + (cycle % 4) * 40}px`
    paneFit.fit()
    await frame()
    paneTerm.scrollToBottom()
  }
  await frame()
  const proposed = paneFit.proposeDimensions()!
  const buffer = paneTerm.buffer.active
  const result = {
    cols: paneTerm.cols,
    rows: paneTerm.rows,
    proposedCols: proposed.cols,
    proposedRows: proposed.rows,
    renderedRowElements: paneHost.querySelectorAll('.xterm-rows > div').length,
    baseY: buffer.baseY,
    viewportY: buffer.viewportY,
    scrollbackLines: buffer.length - paneTerm.rows,
  }
  paneTerm.dispose()
  stack.remove()
  return result
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
