import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  railFitCount,
  railFocusTarget,
  railOverflowState,
  railOverlayBox,
  railPageTarget,
  railPopoverClosingCommand,
  RAIL_DROPUP_MAX_WIDTH_PX,
  RAIL_POPOVER_MAX_WIDTH_PX,
} from '../src/railOverflow.ts'

/** The whole window is visible: no soft keyboard, no pinch-zoom. */
const wholeWindow = (width: number, height: number) => ({ left: 0, top: 0, width, height })

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

test('the overflow popover opens upward, on the rail\'s trailing edge, inside the viewport', () => {
  // The rect is the trailing *cluster*, not the `+N` chip inside it, so the panel lands on
  // the rail's edge whatever that row is carrying.
  const box = railOverlayBox({ left: 1040, right: 1150, top: 860 }, wholeWindow(1400, 900), RAIL_POPOVER_MAX_WIDTH_PX)
  assert.equal(box.width, RAIL_POPOVER_MAX_WIDTH_PX)
  // Right edge on the cluster's right edge: 1150 - 520.
  assert.equal(box.left, 630)
  // The bottom *edge* sits just above the anchor, which is what "grows upward" means.
  assert.equal(box.bottom, 856)
  assert.ok(box.maxHeight <= 450)
})

test('a drop-up gets a list width where the popover gets a grid width', () => {
  const view = wholeWindow(1400, 900)
  const dropup = railOverlayBox({ left: 300, right: 360, top: 860 }, view, RAIL_DROPUP_MAX_WIDTH_PX)
  assert.equal(dropup.width, RAIL_DROPUP_MAX_WIDTH_PX)
  // Same rule, one number apart: a list of one-line rows at grid width reads as a stretched
  // menu, and a wrap grid of chips at list width is a column of one chip per row.
  assert.ok(RAIL_DROPUP_MAX_WIDTH_PX < RAIL_POPOVER_MAX_WIDTH_PX)
})

test('a phone gives a rail overlay half its screen, on the screen\'s trailing edge', () => {
  const view = wholeWindow(390, 760)
  const box = railOverlayBox({ left: 300, right: 360, top: 700 }, view, RAIL_POPOVER_MAX_WIDTH_PX)
  // Half the screen, so the terminal it is opened over stays readable beside it.
  assert.equal(box.width, 195)
  assert.equal(box.left, 390 - 195 - 8)
  // Half the visible height at most, so it never blankets the composer above it.
  assert.ok(box.maxHeight <= 380)

  // And the *screen's* edge rather than the trigger's: a drop-up opened from a chip in the
  // middle of the rail must land where the overflow popover lands, not in the middle.
  const middle = railOverlayBox({ left: 120, right: 180, top: 700 }, view, RAIL_DROPUP_MAX_WIDTH_PX)
  assert.equal(middle.left, box.left)
})

test('a desktop keeps a rail overlay on its own trigger, where there is room to say so', () => {
  const view = wholeWindow(1400, 900)
  const left = railOverlayBox({ left: 300, right: 360, top: 860 }, view, RAIL_DROPUP_MAX_WIDTH_PX)
  const right = railOverlayBox({ left: 1040, right: 1150, top: 860 }, view, RAIL_DROPUP_MAX_WIDTH_PX)
  assert.equal(left.left, 360 - RAIL_DROPUP_MAX_WIDTH_PX)
  assert.equal(right.left, 1150 - RAIL_DROPUP_MAX_WIDTH_PX)
})

test('the half-screen rule follows the device class, not the anchor', () => {
  const anchor = { left: 700, right: 760, top: 700 }
  // Exactly at the breakpoint is still the phone's layout, so still the phone's budget.
  assert.equal(railOverlayBox(anchor, wholeWindow(760, 800), RAIL_POPOVER_MAX_WIDTH_PX).width, 380)
  assert.equal(railOverlayBox(anchor, wholeWindow(761, 800), RAIL_POPOVER_MAX_WIDTH_PX).width, RAIL_POPOVER_MAX_WIDTH_PX)
})

test('an open soft keyboard shrinks what the overlay is allowed to fill', () => {
  // The layout viewport stays 760 tall under `interactive-widget=resizes-visual`; only the
  // visual one shrinks. The rail rides up with it, so the anchor moves too.
  const keyboard = { left: 0, top: 0, width: 390, height: 420 }
  const box = railOverlayBox({ left: 300, right: 360, top: 380 }, keyboard, RAIL_POPOVER_MAX_WIDTH_PX)
  // Half of what is *visible*, not half of a viewport that runs behind the keyboard.
  assert.ok(box.maxHeight <= 210, `maxHeight ${box.maxHeight} is measured against the layout viewport`)
  // And the panel's top stays on screen rather than being pushed above it.
  assert.ok(box.bottom - box.maxHeight >= 0)
  assert.ok(box.bottom <= keyboard.height)
})

test('a rail too high in a short view stops hugging rather than opening off the top', () => {
  // The minimum height is a floor, so an anchor with less room above it than that floor
  // would otherwise be handed a panel whose first row is above the window.
  const box = railOverlayBox({ left: 100, right: 200, top: 60 }, wholeWindow(390, 400), RAIL_POPOVER_MAX_WIDTH_PX)
  assert.ok(box.bottom - box.maxHeight >= 0, 'the panel opened off the top of the view')
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
  // No reservation in the split any more: the chip lives outside the scroller, so the
  // scroller's own clientWidth already excludes it and a reserved width would count it
  // twice (RailStrip.tsx passes overflowWidth: 0).
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  assert.match(strip, /overflowWidth: 0/)
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

test('the strip carries no gear: Configure lives in the popover, not on a rail row', () => {
  // Operator decision 2026-08-22: a standing gear chip on the strip spent rail width on
  // chrome and read as one more action. The in-place editor is reached from the overflow
  // popover's header (and the full editor from Actions -> Configure); the strip renders
  // only chips, the readout, and the `+N` chip.
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(strip, /class="rail-config"/)
  // The popover still receives the handler, or the gear would be gone everywhere.
  assert.match(strip, /onConfigure=\{onConfigure\}/)
})

test('the row scrolls end to end while the trailing cluster stays pinned outside it', () => {
  // Operator decision 2026-08-22: scroll OR overlay, reader's choice. Every chip renders
  // inside the scroller, and the cluster (readout + `+N`) is the row's fixed furniture
  // beside it - never inside it, where a pan would carry it away.
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  const scroller = strip.slice(strip.indexOf('<OverflowRail'), strip.indexOf('</OverflowRail>'))
  assert.match(scroller, /\{chips\}/)
  assert.ok(!scroller.includes('rail-row-trailing'), 'the cluster must sit outside the scroller')
  const cluster = strip.slice(strip.indexOf('class="rail-row-trailing"'))
  // Order inside the cluster is load-bearing: the readout is the only shrinking thing, so
  // it goes first and the `+N` chip holds the trailing edge.
  assert.ok(cluster.indexOf('aria-live="polite"') < cluster.indexOf('ref={moreRef}'))
  const styles = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
  const rule = declarations(styles, /\.rail-row>\.rail-row-trailing\{([^}]*)\}/)
  // Fixed furniture beside a flex:1 scroller: the cluster must never grow into the space
  // the chips scroll through.
  assert.match(rule, /flex:0 0 auto/)
  assert.match(rule, /justify-content:flex-end/)
})

test('the panel is placed against the trailing cluster, so it lands on the rail\'s edge', () => {
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  // Anchoring to the `+N` chip instead would leave the panel a gear-width short of the edge
  // on the one row that carries a gear, which is two placements for one control.
  assert.match(strip, /anchor=\{trailingRef\.current\}/)
})

test('the popover offers the rail editor, and closes as it hands over', () => {
  const popover = readFileSync(new URL('../src/RailOverflowPopover.tsx', import.meta.url), 'utf8')
  assert.match(popover, /class="rail-overflow-configure"/)
  // The editor replaces the whole rail area, so a panel left floating over it would be
  // pointing at a surface that no longer exists.
  assert.match(popover, /onClick=\{\(\) => \{ onClose\(\); onConfigure\(\) \}\}/)
  // Chrome in the header, beside the close control - not a chip in the grid, where it would
  // read as one more thing to press into the terminal.
  const grid = popover.slice(popover.indexOf('rail-overflow-grid'))
  assert.doesNotMatch(grid, /rail-overflow-configure/)
})

test('the terminal rail keeps the scroller that owns its touch-drag click suppression', () => {
  // The split means a row no longer overflows, but the pan's suppression is what settles
  // whether a swipe that began on a chip activated it - including the `+N` chip, whose tap
  // opens a panel over whatever the finger is above.
  const strip = readFileSync(new URL('../src/RailStrip.tsx', import.meta.url), 'utf8')
  assert.match(strip, /<OverflowRail className="terminal-action-scroll" itemLabel="commands" wrapperClassName="terminal-action-scroller" touchDrag touchDragGain=\{1\.75\} preserveSoftKeyboard>/)
})
