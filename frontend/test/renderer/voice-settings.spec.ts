import { expect, test, type Page } from 'playwright/test'

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
  await engine.locator('select').selectOption('kokoro')
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
