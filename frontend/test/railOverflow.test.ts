import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { railFocusTarget, railOverflowState, railPageTarget } from '../src/railOverflow.ts'

test('rail edge controls appear only in directions with hidden content', () => {
  assert.deepEqual(railOverflowState({ scrollLeft: 0, scrollWidth: 300, clientWidth: 300 }), { left: false, right: false })
  assert.deepEqual(railOverflowState({ scrollLeft: 0, scrollWidth: 900, clientWidth: 300 }), { left: false, right: true })
  assert.deepEqual(railOverflowState({ scrollLeft: 240, scrollWidth: 900, clientWidth: 300 }), { left: true, right: true })
  assert.deepEqual(railOverflowState({ scrollLeft: 600, scrollWidth: 900, clientWidth: 300 }), { left: true, right: false })
})

test('rail edge tolerance prevents controls flickering at endpoints', () => {
  assert.deepEqual(railOverflowState({ scrollLeft: 0.75, scrollWidth: 900, clientWidth: 300 }), { left: false, right: true })
  assert.deepEqual(railOverflowState({ scrollLeft: 599.5, scrollWidth: 900, clientWidth: 300 }), { left: true, right: false })
  assert.deepEqual(railOverflowState({ scrollLeft: -8, scrollWidth: 900, clientWidth: 300 }), { left: false, right: true })
})

test('rail paging preserves context and settles on item boundaries', () => {
  const offsets = [0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600]
  assert.equal(railPageTarget({ scrollLeft: 0, scrollWidth: 900, clientWidth: 300 }, offsets, 1), 300)
  assert.equal(railPageTarget({ scrollLeft: 300, scrollWidth: 900, clientWidth: 300 }, offsets, 1), 600)
  assert.equal(railPageTarget({ scrollLeft: 600, scrollWidth: 900, clientWidth: 300 }, offsets, -1), 300)
  assert.equal(railPageTarget({ scrollLeft: 300, scrollWidth: 900, clientWidth: 300 }, offsets, -1), 0)
})

test('rail paging settles on uneven tab boundaries', () => {
  const offsets = [0, 90, 235, 410, 585, 760]
  assert.equal(railPageTarget({ scrollLeft: 0, scrollWidth: 940, clientWidth: 300 }, offsets, 1), 410)
  assert.equal(railPageTarget({ scrollLeft: 410, scrollWidth: 940, clientWidth: 300 }, offsets, -1), 90)
})

test('focused items are moved clear of both overlay controls', () => {
  const metrics = { scrollLeft: 200, scrollWidth: 900, clientWidth: 300 }
  assert.equal(railFocusTarget(metrics, 210, 260), 182)
  assert.equal(railFocusTarget(metrics, 460, 520), 248)
  assert.equal(railFocusTarget(metrics, 300, 360), 200)
})

test('workspace rails retrigger selected-tab reveal when their pane receives focus', () => {
  const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  const railSource = readFileSync(new URL('../src/RailScroller.tsx', import.meta.url), 'utf8')
  assert.match(appSource, /activeKey=\{activeChild\.id\} focusKey=\{focusedPane\?activeChild\.id:undefined\}/)
  assert.match(railSource, /if \(focusKey === undefined\) return\s+return scheduleSelectedReveal\(\)/)
})

test('command rail owns the first touch drag without dropping the soft keyboard', () => {
  const railSource = readFileSync(new URL('../src/RailScroller.tsx', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
  const commandRail = railSource.slice(railSource.indexOf('export function RailScroller'))

  assert.match(commandRail, /touchDrag touchDragGain=\{1\.75\} preserveSoftKeyboard/)
  assert.match(railSource, /event\.target instanceof Element \? event\.target\.closest\('\.rail-key-repeat'\)/)
  assert.match(styles, /\.terminal-action-scroller\.overflow-rail-touch-drag\{touch-action:none\}/)
})

test('command rail terminal writes preserve rather than request mobile keyboard focus', () => {
  const source = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')
  const pasteHelper = source.slice(source.indexOf('async function pasteBrowserClipboard'), source.indexOf('function TerminalPaneImpl'))
  const paste = source.slice(source.indexOf('const paste = async'), source.indexOf('const copyLastReply'))
  const sendKey = source.slice(source.indexOf('const sendKey='), source.indexOf('const railKeySendRef='))
  const injectText = source.slice(source.indexOf('const injectText='), source.indexOf('const insertMobileDraft='))

  assert.doesNotMatch(pasteHelper, /term\.focus\(\)/)
  for (const handler of [paste, sendKey, injectText]) {
    assert.match(handler, /focusAfterTerminalActionRef\.current\(\)/)
    assert.doesNotMatch(handler, /focusTerminalInputRef\.current\(\)/)
  }
  assert.match(source, /if\(typingIntent&&!mobileCursorInitialized\)\{term\.focus\(\)/)
  assert.match(source, /closest\('\.terminal-action-rail'\)\)holdSoftKeyboard\(event\)/)
})

test('jump-to-latest does not request terminal input focus', () => {
  const source = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')
  const handler = source.slice(source.indexOf('const jumpToLatest='), source.indexOf('const setMobileInputMode='))
  assert.ok(handler.length > 0)
  assert.doesNotMatch(handler, /focusTerminalInputRef/)

  const rendererHarness = readFileSync(new URL('./renderer/jumpLatest.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(rendererHarness, /chip-then-keyboard/)
})
