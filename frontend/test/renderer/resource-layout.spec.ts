import { expect, test } from 'playwright/test'

test('a long nested Markdown path cannot widen the mobile resource editor', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 844 })
  await page.goto('/resource-layout-harness.html')

  const widths = await page.evaluate(() => {
    const width = (selector: string) => {
      const element = document.querySelector<HTMLElement>(selector)!
      return element.clientWidth
    }
    return {
      resource: width('.project-resource'),
      header: width('.project-resource > header'),
      editor: width('continuity-editor'),
    }
  })

  expect(widths.resource).toBe(393)
  expect(widths.header).toBeLessThanOrEqual(widths.resource)
  expect(widths.editor).toBeLessThanOrEqual(widths.resource)
})
