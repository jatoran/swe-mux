import { expect, test } from 'playwright/test'

test('react-diff-view runs through Preact with layouts, gutters, widgets, and selection',async({page})=>{
  await page.goto('/git-diff-harness.html')
  await expect(page.locator('.diff')).toBeVisible()
  await expect(page.locator('#toggle')).toHaveText('unified')
  await expect(page.locator('.test-widget')).toContainText('Saved widget')
  await page.locator('.git-diff-gutter-button').last().click()
  await expect(page.locator('#clicked')).not.toHaveText('')
  await expect(page.locator('.test-widget').last()).toContainText('composer')
  await page.locator('#toggle').click()
  await expect(page.locator('#toggle')).toHaveText('split')
  const selected=await page.evaluate(()=>{
    const code=document.querySelector('.diff-code')!
    const range=document.createRange();range.selectNodeContents(code)
    const selection=getSelection()!;selection.removeAllRanges();selection.addRange(range)
    return selection.toString()
  })
  expect(selected.length).toBeGreaterThan(0)
})

test('review modal adapts, preserves manual split, traps focus, and fills mobile',async({page})=>{
  await page.setViewportSize({width:1400,height:900})
  await page.goto('/git-diff-harness.html')
  await page.locator('#open-modal').click()
  await expect(page.locator('.git-review-modal')).toBeVisible()
  await expect(page.locator('.git-review-content')).toHaveClass(/split/)
  await expect(page.locator('.git-review-modal .diff')).toBeVisible()

  await page.setViewportSize({width:700,height:800})
  await expect(page.locator('.git-review-content')).toHaveClass(/unified/)
  const mobileBox=await page.locator('.git-review-modal').boundingBox()
  expect(mobileBox?.width).toBe(700)
  expect(mobileBox?.height).toBe(800)

  await page.getByRole('button',{name:'Split'}).click()
  await expect(page.locator('.git-review-content')).toHaveClass(/split/)
  await expect(page.locator('.git-review-content')).toHaveCSS('overflow-x','auto')
  await page.keyboard.press('Escape')
  await expect(page.locator('.git-review-modal')).toHaveCount(0)
  await expect(page.locator('#open-modal')).toBeFocused()
})
