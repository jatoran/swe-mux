import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  railFitCount,
  railFocusTarget,
  railOverflowState,
  railPageTarget,
  railPopoverClosingCommand,
  railPopoverStyle,
  RAIL_POPOVER_MAX_WIDTH_PX,
} from '../src/railOverflow.ts'

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
  assert.match(styles, /\.terminal-action-scroller\.overflow-rail-touch-drag\{touch-action:none\}/)
})

test('a swipe may begin on a repeating arrow key, which sends on click like its neighbours', () => {
  const railSource = readFileSync(new URL('../src/RailScroller.tsx', import.meta.url), 'utf8')
  const button = readFileSync(new URL('../src/RailRepeatKey.tsx', import.meta.url), 'utf8')
  const pointerDown = button.slice(button.indexOf('onPointerDown='), button.indexOf('onContextMenu='))

  // The pan refusing a touch that landed on an arrow is what made the arrows the one part
  // of the rail you could not push off of: the flick fired the key instead of scrolling.
  assert.doesNotMatch(railSource, /closest\('\.rail-key-repeat'\)/)
  // Both halves of the fix have to hold together. A press that sent on its own would be
  // unrecoverable however permissive the pan is, and a `preventDefault` on the pointer
  // would leave the tap-carrying click to each browser's compatibility rules.
  assert.doesNotMatch(pointerDown, /preventDefault|repeat\.send/)
  assert.match(button, /onClick=\{\(\)=>\{if\(!repeat\.repeater\.consumeHeldClick\(\)\)repeat\.send\(sequence\)\}\}/)
  assert.match(button, /onMouseDown=\{event=>event\.preventDefault\(\)\}/)
})

test('command rail terminal writes preserve rather than request mobile keyboard focus', () => {
  const source = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')
  const pasteHelper = source.slice(source.indexOf('async function pasteBrowserClipboard'), source.indexOf('function TerminalPaneImpl'))
  const paste = source.slice(source.indexOf('const paste = async'), source.indexOf('const copyLastReply'))
  const sendKey = source.slice(source.indexOf('const sendKey='), source.indexOf('const railKeyRepeat='))
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

test('a row that fits keeps every chip and reserves nothing for an overflow chip', () => {
  // Six 60px chips with 5px gaps is 385. The `+N` chip's width is passed but must not be
  // spent: a rail that fits has to look exactly like a rail from before the split existed.
  const widths = [60, 60, 60, 60, 60, 60]
  assert.equal(railFitCount({ widths, gap: 5, available: 385, overflowWidth: 42 }), 6)
  assert.equal(railFitCount({ widths, gap: 5, available: 400, overflowWidth: 42 }), 6)
})

test('subpixel measurement does not push the last chip of a full row into the popover', () => {
  // Real widths come from getBoundingClientRect at fractional device pixel ratios, so an
  // exactly-fitting row routinely measures a few hundredths over its container.
  const widths = [60.2, 60.2, 60.2]
  assert.equal(railFitCount({ widths, gap: 5, available: 190.4, overflowWidth: 42 }), 3)
})

test('once anything overflows the `+N` chip is paid for before the first chip is placed', () => {
  const widths = [60, 60, 60, 60, 60, 60]
  // At 300 the row has 253 left once the chip and its gap are taken; four chips need 255,
  // so the fourth goes to the popover even though it would have fitted the bare 300.
  assert.equal(railFitCount({ widths, gap: 5, available: 300, overflowWidth: 42 }), 3)
  assert.equal(railFitCount({ widths, gap: 5, available: 320, overflowWidth: 42 }), 4)
})

test('a rail narrower than its first chip pins nothing and shows only the overflow chip', () => {
  assert.equal(railFitCount({ widths: [120, 120], gap: 5, available: 90, overflowWidth: 42 }), 0)
  assert.equal(railFitCount({ widths: [], gap: 5, available: 400, overflowWidth: 42 }), 0)
})

test('the overflow popover opens upward, right-aligned to its chip, inside the viewport', () => {
  const style = railPopoverStyle({ left: 1100, right: 1150, top: 860 }, { width: 1400, height: 900 })
  assert.equal(style.width, `${RAIL_POPOVER_MAX_WIDTH_PX}px`)
  // Right edge on the chip's right edge: 1150 - 520.
  assert.equal(style.left, '630px')
  // Anchored above the chip rather than below it, which is what "grows upward" means.
  assert.equal(style.bottom, `${900 - 860 + 4}px`)
  assert.ok(Number.parseInt(style.maxHeight, 10) <= 450)
})

test('a phone clamps the popover to the viewport instead of hanging off the rail edge', () => {
  const style = railPopoverStyle({ left: 300, right: 360, top: 700 }, { width: 390, height: 760 })
  assert.equal(style.width, `${390 - 16}px`)
  assert.equal(style.left, '8px')
  // Half the viewport at most, so a phone's popover never blankets the composer above it.
  assert.ok(Number.parseInt(style.maxHeight, 10) <= 380)
})

test('a selection that opens a drawer or the library collapses the popover; a rail key does not', () => {
  // The whole point of the panel is that using it does not close it, so this list is the
  // exception rather than the rule and every entry has to be a departure from the rail.
  assert.ok(railPopoverClosingCommand('drawer.peekActions'))
  assert.ok(railPopoverClosingCommand('drawer.actions.skills'))
  assert.ok(railPopoverClosingCommand('clipboard.open'))
  assert.ok(railPopoverClosingCommand('prompts.new'))
  // Two-click End session and the repeat-tap arrows have to survive their own command.
  assert.ok(!railPopoverClosingCommand('session.kill'))
  assert.ok(!railPopoverClosingCommand('session.relaunch'))
  assert.ok(!railPopoverClosingCommand('terminal.copy'))
})

/** One CSS rule's declarations, or a failure naming the selector that went missing. */
function declarations(styles: string, pattern: RegExp): string {
  const rule = pattern.exec(styles)
  if (!rule) throw new Error(`style.css no longer has a rule matching ${pattern}`)
  return rule[1]
}

test('the overflow chip is fixed-width, so the control that absorbs overflow cannot overflow', () => {
  const styles = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
  const rule = declarations(styles, /\.terminal-action-rail button\.rail-more\{([^}]*)\}/)
  assert.match(rule, /min-width:var\(--rail-more-width\)/)
  assert.match(rule, /width:var\(--rail-more-width\)/)
  // The split reserves the chip's width before the chip exists, and reads the same
  // variable to do it (RailStrip.tsx). A literal there would be a second answer.
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  assert.match(strip, /getPropertyValue\('--rail-more-width'\)/)
})

test('the measured copy of a row is unpaintable, untouchable, and outside the scroller', () => {
  const styles = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
  const rule = declarations(styles, /\.rail-row-measure\{([^}]*)\}/)
  assert.match(rule, /visibility:hidden/)
  assert.match(rule, /pointer-events:none/)
  // Without this an absolutely-positioned flex container shrink-fits, and the widths it
  // reports are the squeezed ones rather than the ones the chips would really render at.
  assert.match(rule, /width:max-content/)
  // A measure row *inside* the strip would count toward its scrollWidth and report
  // permanent overflow to the component drawing the edge chevrons.
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  assert.match(strip, /class="terminal-action-scroll rail-row-measure"[\s\S]*?<OverflowRail/)
})

test('the trailing gear is reserved out of the fit budget and the status readout is not', () => {
  const pane = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  assert.match(pane, /class="rail-config" data-rail-fixed/)
  assert.match(strip, /querySelectorAll<HTMLElement>\('\[data-rail-fixed\]'\)/)
  // The readout's text changes under the row (a transient "Copied", a selection count).
  // Reserving it would move every chip beside it each time it appeared.
  assert.doesNotMatch(pane, /aria-live="polite" data-rail-fixed/)
})

test('the terminal rail keeps the scroller that owns its touch-drag click suppression', () => {
  // The split means a row no longer overflows, but the pan's suppression is what settles
  // whether a swipe that began on a chip activated it - including the `+N` chip, whose tap
  // opens a panel over whatever the finger is above.
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  assert.match(strip, /<OverflowRail className="terminal-action-scroll" itemLabel="commands" wrapperClassName="terminal-action-scroller" touchDrag touchDragGain=\{1\.75\} preserveSoftKeyboard>/)
})
