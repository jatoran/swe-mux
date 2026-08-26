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
const voicePages = (page:Page) => page.locator('.settings-subtabs').filter({has:page.getByText('Talk & dictation',{exact:true})})

const open = async (page: Page, subpage = 'Read aloud') => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  await page.locator('.settings-tabs button').first().waitFor({ state: 'attached' })
  const row=page.locator('.settings-tab-row',{has:page.locator('[role="tab"]',{hasText:/^Voice$/})})
  await row.locator('[role="tab"]').click()
  await expect(page.locator('.settings-tab-row>[role="tab"].active')).toHaveText('Voice')
  if(await row.locator('.settings-tab-expand[aria-expanded="false"]').count())await row.locator('.settings-tab-expand').click()
  await voicePages(page).locator('button',{hasText:new RegExp(`^${subpage}$`)}).click()
}

const PAGES=['Read aloud','Talk & dictation','Voice commands','Mux assistant','Diagnostics']

test('Voice exposes capability pages and renders only the selected page', async ({ page }) => {
  await open(page)
  await expect(voicePages(page).locator('button')).toHaveText(PAGES)
  await expect(page.locator('.settings-subpage-heading strong')).toHaveText('Read aloud')
  await expect(page.locator('.settings-content > section:visible > h3')).toHaveText(['Read aloud','TTS provider','Spoken summary','Clip storage'])
  await voicePages(page).locator('button',{hasText:'Talk & dictation'}).click()
  await expect(page.locator('.settings-content > section:visible > h3')).toHaveText(['Talk & dictation'])
  await voicePages(page).locator('button',{hasText:'Voice commands'}).click()
  await expect(page.locator('.settings-content > section:visible > h3')).toHaveText(['Voice commands','Command reference'])
  await voicePages(page).locator('button',{hasText:'Mux assistant'}).click()
  await expect(page.locator('.settings-content > section:visible > h3')).toHaveText(['Mux assistant'])
  await voicePages(page).locator('button',{hasText:'Diagnostics'}).click()
  await expect(page.locator('.settings-content > section:visible > h3')).toHaveText(['Testing and latency','Mobile voice'])
})

test('the read-aloud policy stays one numbered block under the first heading', async ({ page }) => {
  await open(page)

  const first = page.locator('.settings-content > section:visible').first()
  await expect(first.locator('h3')).toHaveText('Read aloud')

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

test('the pronunciation section exists only for Kokoro', async ({ page }) => {
  await open(page)

  await expect(page.locator('.settings-content > section', { has: page.locator('h3', { hasText: 'Pronunciation' }) })).toHaveCount(0)
  const engine = page.locator('[data-setting="tts_engine"]')

  // Selecting Kokoro adds one provider-owned section and puts the editor there only.
  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'kokoro')
  const pronunciation = page.locator('.settings-content > section', { has: page.locator('h3', { hasText: 'Pronunciation' }) })
  await expect(pronunciation.locator('.tts-lexicon')).toHaveCount(1)
  await expect(page.locator('.settings-content .tts-lexicon')).toHaveCount(1)
  await expect(page.locator('.settings-content > section:visible > h3')).toHaveText([
    'Read aloud','TTS provider','Pronunciation','Spoken summary','Clip storage',
  ])

  // Edge owns no local G2P. Hiding Kokoro's controls does not clear their draft values.
  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'edge')
  await expect(page.locator('.settings-content .tts-lexicon')).toHaveCount(0)
  await expect(page.locator('.kokoro-model-panel')).toHaveCount(0)
  await expect(page.locator('.kokoro-voice-disclosure')).toHaveCount(0)
  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'kokoro')
  await expect(page.locator('.settings-content .tts-lexicon')).toHaveCount(1)
})

test('Talk owns commands, while Mux Assistant remains independent', async ({ page }) => {
  await open(page,'Voice commands')
  await expect(page.locator('.settings-capability-flag')).toContainText('Talk is off')
  await expect(page.locator('.voice-commands')).toHaveCount(0)
  await expect(page.locator('.settings-muted-reference')).toBeVisible()

  await page.getByRole('button',{name:'Enable Talk & dictation'}).click()
  await expect(page.locator('.voice-commands')).toBeVisible()
  await expect(page.locator('.settings-content > section:visible details.settings-disclosure')).toHaveCount(1)

  await voicePages(page).locator('button',{hasText:'Mux assistant'}).click()
  await expect(page.locator('[data-setting="assistant_enabled"]')).toBeVisible()
  await expect(page.locator('.settings-capability-status')).toHaveCount(0)
  await page.locator('[data-setting="assistant_enabled"] input').check()
  await voicePages(page).locator('button',{hasText:'Talk & dictation'}).click()
  await page.locator('[data-setting="stt_enabled"] input').uncheck()
  await voicePages(page).locator('button',{hasText:'Mux assistant'}).click()
  await expect(page.locator('.settings-capability-status')).toContainText('Text chat available')
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

test('provider-specific draft settings survive switching in both directions', async ({ page }) => {
  await open(page)
  const engine = page.locator('[data-setting="tts_engine"]')

  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'kokoro')
  const speed = page.getByLabel('Speed (0.5–2.0)')
  await speed.fill('1.35')

  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'edge')
  await expect(page.locator('.edge-tts-settings')).toHaveCount(1)
  await page.getByLabel('Edge voice').selectOption('en-GB-SoniaNeural')
  await page.getByLabel('Rate (%)').fill('22')
  await page.getByLabel('Pitch (Hz)').fill('-8')
  await page.getByLabel('I understand the service, privacy, reliability, and commercial-use uncertainty').check()
  await expect(page.locator('.kokoro-model-panel')).toHaveCount(0)

  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'kokoro')
  await expect(speed).toHaveValue('1.35')
  await expect(page.locator('.edge-tts-settings')).toHaveCount(0)

  await chooseDropdown(page, engine.locator('.dropdown-trigger'), 'edge')
  await expect(page.getByLabel('Edge voice')).toHaveValue('en-GB-SoniaNeural')
  await expect(page.getByLabel('Rate (%)')).toHaveValue('22')
  await expect(page.getByLabel('Pitch (Hz)')).toHaveValue('-8')
  await expect(page.getByLabel('I understand the service, privacy, reliability, and commercial-use uncertainty')).toBeChecked()

  await page.getByRole('button',{name:'Save changes'}).click()
  const submitted=await page.evaluate(()=>window.settingsCalls.filter(call=>call.path==='/api/settings/apply').at(-1)?.body as {config?:Record<string,unknown>})
  expect(submitted.config).toMatchObject({
    tts_engine:'edge',tts_kokoro_speed:1.35,tts_edge_voice:'en-GB-SoniaNeural',
    tts_edge_rate_percent:22,tts_edge_pitch_hz:-8,tts_edge_risk_ack_version:1,
  })
})

test('managed Edge installation is explicit and carries the user-gesture proof', async ({ page }) => {
  await open(page)
  const engine=page.locator('[data-setting="tts_engine"]')
  await chooseDropdown(page,engine.locator('.dropdown-trigger'),'edge')
  const install=page.getByRole('button',{name:'Install Edge TTS integration'})
  await expect(install).toBeVisible()
  expect(await page.evaluate(()=>window.settingsCalls.filter(call=>call.path==='/api/voice/providers/edge/install').length)).toBe(0)
  await install.click()
  const call=await page.evaluate(()=>window.settingsCalls.filter(item=>item.path==='/api/voice/providers/edge/install').at(-1))
  expect(call?.method).toBe('POST')
  expect(call?.gesture).toBe('edge-tts-install')
  await expect(page.getByRole('button',{name:'Installing…'})).toBeVisible()
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
