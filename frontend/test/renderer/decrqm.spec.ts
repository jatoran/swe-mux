import { expect, test } from 'playwright/test'

test('DECRQM startup queries neither crash the parser nor stop rendering', async ({ page }) => {
  // oh-my-pi probes five DEC private modes at startup — the only integrated
  // harness that sends DECRQM at all. xterm 6.0.0's handler for it was the one
  // sequence the production bundler used to corrupt (a dropped enum declaration
  // turned into a strict-mode ReferenceError), which killed the write loop and
  // rendered every OMP pane permanently black. This pins the whole contract:
  // writes complete, output after the queries still paints, every query gets a
  // DECRPM answer, and no page error escapes the parser.
  const pageErrors: string[] = []
  page.on('pageerror', error => pageErrors.push(error.stack ?? error.message))

  await page.goto('/renderer-harness.html')
  const result = await page.evaluate(() => window.runDecrqmProbe())

  expect(result.writeCompleted, 'write callbacks must survive the DECRQM barrage').toBe(true)
  expect(result.painted).toContain('omp probe survived')
  const reports = result.responses.join('')
  // One DECRPM (`…$y`) answer per query: five DEC-private probes plus one ANSI probe.
  expect(reports.match(/\$y/g)?.length ?? 0, `mode reports in ${JSON.stringify(reports)}`).toBe(6)
  for (const mode of [2026, 2048, 2031, 1010, 1011]) {
    expect(reports, `DECRPM answer for mode ${mode}`).toContain(`\x1b[?${mode};`)
  }
  expect(pageErrors).toEqual([])
})
