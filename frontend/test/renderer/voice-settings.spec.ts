import { expect, test, type Page } from 'playwright/test'
import { chooseDropdown } from './dropdown'

// The shape of Settings → Voice, which is the largest tab in the panel and the one that
// most easily reverts to a single scroll of everything.
//
// It was exactly that: one `<section>` carrying eight headings, with the read-aloud policy,
// the engine, the pronunciation lexicon (an `<h4>` inside the Kokoro branch of the engine
// block), summary budgets, the microphone, seventeen phrase rows, the whole spoken-command
// catalog, a latency readout, a tester, and mobile setup in one column. These assertions are
// the structure, not the styling: one section per concern so the section rail can index it,
// the three-layer read-aloud policy kept as one unit, the lexicon owning a section of its own,
// reference bodies folded away, and every deep-linkable control still on screen when the tab
// lands.

const DESKTOP = { width: 1280, height: 900 }

const open = async (page: Page) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  await page.locator('.settings-tabs button').first().waitFor({ state: 'attached' })
  await page.locator('.settings-tabs button', { hasText: /^Voice$/ }).click()
  await expect(page.locator('.settings-tabs button.active')).toHaveText('Voice')
}

// The order the tab reads in: what speaks, how it sounds, how it says a word, what it says
// and costs, then the whole capture half, then reference.
const SECTIONS = [
  'Read aloud (TTS)',
  'Voice and engine',
  'Pronunciation',
  'Spoken summary',
  'Microphone and wake words',
  'Command phrases',
  'Command reference',
  'Mux assistant',
  'Testing and latency',
  'Mobile voice',
]

test('the tab is one section per concern, and the rail indexes all of them', async ({ page }) => {
  await open(page)

  // The headings themselves are the source of truth; the rail is derived from them
  // (`settingsTabs.ts`), so asserting both is what catches a heading that renders but never
  // reaches the rail — the failure mode of a heading nested somewhere the reader is not.
  await expect(page.locator('.settings-content > section > h3')).toHaveText(SECTIONS)
  await expect(page.locator('.settings-section-rail button')).toHaveText(SECTIONS)

  // One `<section>` per heading, like Automation and Remote. A tab that renders one section
  // with ten headings inside it passes the heading check above and still reads as a sprawl,
  // because the borders between concerns come from the section boxes.
  await expect(page.locator('.settings-content > section')).toHaveCount(SECTIONS.length)
})

test('the read-aloud policy stays one numbered block under the first heading', async ({ page }) => {
  await open(page)

  const first = page.locator('.settings-content > section').first()
  await expect(first.locator('h3')).toHaveText('Read aloud (TTS)')

  // Three layers, in order, in one box. They answer "why is it talking / why is it silent"
  // and are only useful read together, so splitting them across sections would undo the
  // thing this block exists to fix (`features/voice.md`, one policy in three layers).
  const stack = first.locator('.policy-stack[data-policy="read-aloud"]')
  await expect(stack).toHaveCount(1)
  await expect(stack.locator('.policy-step')).toHaveText(['1', '2', '3'])
  await expect(stack.locator('[data-setting="tts_enabled"]')).toHaveCount(1)
  await expect(stack.locator('[data-setting="tts_default_mode"]')).toHaveCount(1)

  // The master is the only switch in the block that is a config write; the device layer is
  // browser-local and deliberately carries no `data-setting`, since there is nothing
  // install-wide for a deep link to land on.
  await expect(page.locator('.settings-content [data-setting="tts_enabled"]')).toHaveCount(1)
})

test('the pronunciation lexicon is its own section, not a heading inside the engine block', async ({ page }) => {
  await open(page)

  const pronunciation = page.locator('.settings-content > section', { has: page.locator('h3', { hasText: 'Pronunciation' }) })
  await expect(pronunciation).toHaveCount(1)

  // The fixture ships the OS voice, where the lexicon does not apply. That is the case the
  // section has to survive without going quiet: it says why, and offers the control that
  // changes it rather than naming a setting and leaving the reader to find it.
  await expect(pronunciation.locator('.tts-lexicon')).toHaveCount(0)
  await pronunciation.locator('button', { hasText: 'Go to the engine setting' }).click()
  const engine = page.locator('[data-setting="tts_engine"]')
  await expect(engine).toHaveClass(/setting-flash/)

  // Selecting Kokoro puts the editor in the Pronunciation section — and nowhere else.
  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'kokoro')
  await expect(pronunciation.locator('.tts-lexicon')).toHaveCount(1)
  await expect(page.locator('.settings-content .tts-lexicon')).toHaveCount(1)
  await expect(page.locator('.settings-content > section > h3')).toHaveText(SECTIONS)
})

test('reference sections fold away, and no deep-linkable control folds with them', async ({ page }) => {
  await open(page)

  // Three sections are reference rather than daily controls: the command catalog, the two
  // measuring instruments, and the one-time mobile setup. Each keeps its heading — so the
  // rail still lists it and the reader still knows it exists — and folds only its body.
  const folds = page.locator('.settings-content details.settings-disclosure')
  await expect(folds).toHaveCount(3)
  expect(await folds.evaluateAll(nodes => nodes.map(node => (node as HTMLDetailsElement).open)))
    .toEqual([false, false, false])

  // `revealSetting` does open a disclosure above its target (`setting-reveal.spec.ts`), so
  // this is a convention rather than a hard requirement — but a switch a gated surface just
  // promised should be on screen when the panel lands, not behind one more state change.
  const folded = await page.locator('.settings-content [data-setting]').evaluateAll(nodes =>
    nodes.filter(node => node.closest('details.settings-disclosure:not([open])'))
      .map(node => node.getAttribute('data-setting')))
  expect(folded).toEqual([])

  // And the marks a deep link and the models index actually aim at are all still here
  // (`settingTargets.ts`, `modelRouting.ts`).
  for (const setting of ['tts_enabled', 'stt_enabled', 'tts_summary_model', 'assistant_model', 'assistant_enabled']) {
    await expect(page.locator(`[data-setting="${setting}"]`)).toBeVisible()
  }
})


test('Kokoro\'s long bodies fold, and folding one never hides a deep link', async ({ page }) => {
  await open(page)
  // Two controls in this tab are long rather than rarely read: the fifty-odd Kokoro
  // voice chips and the pronunciation editor with its spelled-word history. Both are
  // only drawn under Kokoro, and both buried the controls beneath them - the speed
  // field, the cache limit, the whole rest of the tab - behind a wall of chips.
  const engine = page.locator('[data-setting="tts_engine"]')
  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'kokoro')

  const voices = page.locator('details.kokoro-voice-disclosure')
  await expect(voices).toHaveCount(1)
  // Closed, it still answers which voice is selected: a fold that hides the current
  // value makes the reader open it to learn nothing they wanted to change.
  await expect(voices.locator('summary')).toContainText('Kokoro voice')
  // The picker is INSIDE the fold, and the fold is shut. Counting DOM nodes would
  // prove nothing: a closed `<details>` in current Chromium hides its body with
  // `content-visibility`, so the chips are still in the tree and still report client
  // rects (the same engine detail `settingReveal.ts` is written around).
  await expect(voices.locator('.kokoro-voice-picker')).toHaveCount(1)
  expect(await voices.evaluate(node => (node as HTMLDetailsElement).open)).toBe(false)
  await voices.locator('summary').click()
  expect(await voices.evaluate(node => (node as HTMLDetailsElement).open)).toBe(true)
  await expect(page.locator('.kokoro-voice-chip').first()).toBeVisible()

  const pronunciation = page.locator('.settings-content > section', { has: page.locator('h3', { hasText: 'Pronunciation' }) })
  const lexicon = pronunciation.locator('details.settings-disclosure')
  await expect(lexicon).toHaveCount(1)
  expect(await lexicon.evaluate(node => (node as HTMLDetailsElement).open)).toBe(false)
  await expect(lexicon.locator('.tts-lexicon')).toHaveCount(1)
  // The section itself does not fold - it keeps its heading and therefore its rail
  // entry, exactly as the three reference sections do.
  await expect(pronunciation.locator('h3')).toHaveText('Pronunciation')

  // And the rule that matters more than either: nothing a deep link aims at ends up
  // inside a closed disclosure because of this.
  const folded = await page.locator('.settings-content [data-setting]').evaluateAll(nodes =>
    nodes.filter(node => node.closest('details.settings-disclosure:not([open])'))
      .map(node => node.getAttribute('data-setting')))
  expect(folded).toEqual([])
})

test('the budget control is a row of chips on a phone, not a stack of form rows', async ({ page }) => {
  await open(page)
  await page.setViewportSize({ width: 390, height: 780 })
  const budget = page.locator('.budget-control[data-setting="tts_daily_budget"]')
  await expect(budget).toBeVisible()

  // The reported defect: `.settings-content label:not(.check)` is (0,2,1) and re-grids
  // every label in the panel into a two-column form row, out-specifying both
  // `.budget-axis` and the `.budget-control .budget-mode` scoping that was meant to
  // protect these. So the three mode radios rendered as tall two-column rows and each
  // axis put its one word in a 38%-wide label column beside a stranded input.
  const layout = await budget.evaluate(node => {
    const mode = node.querySelector<HTMLElement>('.budget-mode')!
    const axis = node.querySelector<HTMLElement>('.budget-axis')!
    return {
      modeDisplay: getComputedStyle(mode).display,
      modeHeight: Math.round(mode.getBoundingClientRect().height),
      axisColumns: getComputedStyle(axis).gridTemplateColumns.split(' ').length,
      // The mode chips share one row's baseline; a re-gridded label breaks that first.
      modeTops: [...node.querySelectorAll<HTMLElement>('.budget-mode')]
        .map(item => Math.round(item.getBoundingClientRect().top)),
    }
  })
  // `flex`, not `grid`: `.budget-modes` is a flex container, so a flex item's
  // `inline-flex` is blockified to `flex` - which is the fix landing, not missing.
  expect(layout.modeDisplay).toBe('flex')
  expect(layout.modeHeight).toBeLessThanOrEqual(40)
  expect(layout.axisColumns).toBe(1)
  expect(new Set(layout.modeTops).size).toBe(1)

  // And the axis label sits above its input rather than beside it, so the pair reads as
  // one field at this width instead of two misaligned columns.
  const stacked = await budget.evaluate(node => {
    const axis = node.querySelector<HTMLElement>('.budget-axis')!
    const input = axis.querySelector<HTMLElement>('input')!
    return input.getBoundingClientRect().top > axis.getBoundingClientRect().top + 4
  })
  expect(stacked).toBe(true)
})

test('the mode radio is a dot, and its word fits in the chip', async ({ page }) => {
  await open(page)
  await page.setViewportSize({ width: 390, height: 780 })
  const budget = page.locator('.budget-control[data-setting="tts_daily_budget"]')
  await expect(budget).toBeVisible()

  // The assertions above passed while this was broken, which is why they are not enough:
  // `.settings-panel input:not([type=checkbox])` and its `.settings-content` twin excluded
  // the checkbox and not the radio, so every dot took `width:100%` and a 31px field height
  // and rendered 77x31. The chip stayed one row and under 40px tall - it simply had no room
  // left for its own word, which spilled past the border ("F...", "T...", "Dollars" adrift).
  // Both numbers below are what the shared `--check-size` promises, measured rather than
  // asserted as CSS text: 14px on this fine-pointer viewport, 18px on a coarse one.
  const chips = await budget.evaluate(node => {
    const size = getComputedStyle(document.documentElement).getPropertyValue('--check-size').trim()
    return [...node.querySelectorAll<HTMLElement>('.budget-mode')].map(chip => {
      const dot = chip.querySelector<HTMLInputElement>('input')!.getBoundingClientRect()
      return {
        checkSize: parseFloat(size),
        dot: { w: Math.round(dot.width), h: Math.round(dot.height) },
        // A chip that has to scroll to show its label is a chip with a clipped label.
        clipped: chip.scrollWidth > Math.ceil(chip.getBoundingClientRect().width),
      }
    })
  })
  for (const chip of chips) {
    expect(chip.dot).toEqual({ w: chip.checkSize, h: chip.checkSize })
    expect(chip.clipped).toBe(false)
  }
})

test('the two axes stay side by side on a phone', async ({ page }) => {
  await open(page)
  await page.setViewportSize({ width: 390, height: 780 })
  const budget = page.locator('.budget-control[data-setting="tts_daily_budget"]')
  await expect(budget).toBeVisible()

  // Tokens and dollars are one field counted two ways, and the narrow layout used to stack
  // them: ~50px of a phone's screen spent putting the dollar figure under the fold, directly
  // below the chips that were choosing between the two. They share a row, they line up at the
  // top (the unenforced axis carries a note the other does not), and the fieldset stays short.
  const pair = await budget.evaluate(node => {
    const axes = [...node.querySelectorAll<HTMLElement>('.budget-axis')]
    const rects = axes.map(axis => axis.getBoundingClientRect())
    const inputs = axes.map(axis => axis.querySelector<HTMLElement>('input')!.getBoundingClientRect())
    return {
      columns: getComputedStyle(node.querySelector<HTMLElement>('.budget-axes')!)
        .gridTemplateColumns.split(' ').length,
      tops: rects.map(rect => Math.round(rect.top)),
      inputTops: inputs.map(rect => Math.round(rect.top)),
      height: Math.round(node.getBoundingClientRect().height),
    }
  })
  expect(pair.columns).toBe(2)
  expect(new Set(pair.tops).size).toBe(1)
  expect(new Set(pair.inputTops).size).toBe(1)
  expect(pair.height).toBeLessThan(215)
})
