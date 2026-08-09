import { expect, test } from 'playwright/test'

// Two failure modes with no symptom that points at their cause: a VAD that never
// loads degrades silently to the energy detector, and a worklet the audio graph
// never renders simply produces no utterances at all. Both are only observable in a
// real browser, so they are pinned here rather than in the unit suite.

test('Silero loads in the browser and separates speech from silence', async ({ page }) => {
  await page.goto('/voice-capture-harness.html')
  const result = await page.evaluate(() => window.muxVoiceHarness.sileroProbabilities())
  expect(result.silence).toBeLessThan(0.2)
  expect(result.speech).toBeGreaterThan(result.silence)
})

test('the capture worklet is pulled by the audio graph', async ({ page }) => {
  await page.goto('/voice-capture-harness.html')
  const samples = await page.evaluate(() => window.muxVoiceHarness.workletDelivers())
  // Half a second at 48 kHz is ~24000 samples; anything above a few thousand proves
  // the node is being rendered rather than sitting idle with nothing downstream.
  expect(samples).toBeGreaterThan(5_000)
})
