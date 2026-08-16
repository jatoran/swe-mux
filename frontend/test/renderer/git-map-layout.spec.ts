import { expect, test } from 'playwright/test'

for(const width of [180,240,360]){
  test(`Git Map identity and dense metrics remain separate at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:500})
    await page.goto('/git-map-harness.html')
    const row=page.locator('.git-map-summary').filter({hasText:'sidebar-session-git-lines-fix'})
    await expect(row).toBeVisible()
    const geometry=await page.evaluate(()=>{
      const box=(selector:string)=>document.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON()
      const summary=document.querySelector<HTMLElement>('.git-map-summary')!
      return {
        summary:box('.git-map-summary'),rail:box('.git-map-rail'),identity:box('.git-map-identity'),
        chevron:box('.git-map-chevron'),metrics:box('.git-map-metrics'),
        horizontalOverflow:summary.scrollWidth-summary.clientWidth,
        identityText:document.querySelector<HTMLElement>('.git-map-identity')!.innerText,
      }
    })
    expect(geometry.identity.bottom).toBeLessThanOrEqual(geometry.metrics.top+0.5)
    expect(geometry.rail.right).toBeLessThanOrEqual(geometry.identity.left)
    expect(geometry.chevron.right).toBeLessThanOrEqual(geometry.metrics.left)
    expect(geometry.rail.top).toBeLessThan(geometry.identity.bottom)
    expect(geometry.rail.bottom).toBeGreaterThan(geometry.identity.top)
    expect(geometry.chevron.top).toBeLessThan(geometry.metrics.bottom)
    expect(geometry.chevron.bottom).toBeGreaterThan(geometry.metrics.top)
    expect(geometry.identity.right).toBeLessThanOrEqual(geometry.summary.right)
    expect(geometry.metrics.right).toBeLessThanOrEqual(geometry.summary.right)
    expect(geometry.horizontalOverflow).toBeLessThanOrEqual(0.5)
    expect(geometry.identityText).toBe('sidebar-session-git-lines-fix')
  })
}

test('Git Map gives ahead its own cool emphasis',async({page})=>{
  await page.setViewportSize({width:240,height:500})
  await page.goto('/git-map-harness.html')
  const ahead=page.locator('.git-map-metrics .ahead')
  await expect(ahead).toHaveText('12345 ahead')
  expect(await ahead.evaluate(element=>getComputedStyle(element).color)).toBe('rgb(197, 138, 249)')
})

test('Git Log labels a linear worktree tip separately from main without inventing a fork',async({page})=>{
  await page.setViewportSize({width:280,height:600})
  await page.goto('/git-map-harness.html')
  await page.getByRole('button',{name:'Log',exact:true}).click()
  const context=page.locator('.git-graph-context')
  await expect(context).toContainText('MAIN TREEmain@ 9299950a')
  await expect(context).toContainText('COMPAREorigin/main')
  await expect(context).toContainText('WORKTREES1 linked')
  await expect(context).toContainText('SCOPEall refs')

  const worktreeRow=page.locator('.git-graph-row').filter({hasText:'Fix sidebar Git lines'})
  await expect(worktreeRow.locator('.git-commit-refs')).toContainText('sidebar-session-git-lines-fix')
  await expect(worktreeRow.locator('.git-commit-refs')).toContainText('WT sidebar-session-git-lines-fix')
  await expect(worktreeRow.locator('.git-graph-glyph .node')).toHaveText('●')

  const mainRow=page.locator('.git-graph-row').filter({hasText:'Main branch baseline'})
  await expect(mainRow.locator('.git-commit-refs')).toContainText('HEAD')
  await expect(mainRow.locator('.git-commit-refs')).toContainText('origin/main')
  await expect(mainRow.locator('.git-commit-refs')).toContainText('MAIN TREE')
})
