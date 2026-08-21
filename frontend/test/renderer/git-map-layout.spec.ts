import { expect, test } from 'playwright/test'

for(const width of [180,240,360]){
  test(`Git Map identity and dense metrics remain separate at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:500})
    await page.goto('/git-map-harness.html')
    const row=page.locator('.git-map-summary').filter({hasText:'sidebar-session-git-lines-fix'})
    await expect(row).toBeVisible()
    // Scoped to the linked worktree's own row rather than to the first one in the
    // document: the main tree is pinned to the top of Map regardless of its date
    // (`sortWorktreesByActivity`), so "the first summary" is the trunk, whose identity
    // line is the one case with a qualifier under it and therefore not what this
    // measures.
    const geometry=await page.evaluate(()=>{
      const rows=[...document.querySelectorAll<HTMLElement>('.git-map-summary')]
      const summary=rows.find(item=>item.innerText.includes('sidebar-session-git-lines-fix'))!
      const box=(selector:string)=>summary.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON()
      return {
        summary:summary.getBoundingClientRect().toJSON(),rail:box('.git-map-rail'),identity:box('.git-map-identity'),
        chevron:box('.git-map-chevron'),metrics:box('.git-map-metrics'),
        horizontalOverflow:summary.scrollWidth-summary.clientWidth,
        identityText:summary.querySelector<HTMLElement>('.git-map-identity')!.innerText,
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

test('a worktree\'s live sessions are their own target, and lead to the session',async({page})=>{
  await page.setViewportSize({width:360,height:640})
  await page.goto('/git-map-harness.html')
  const live=page.locator('.git-map-live')
  await expect(live).toHaveText('1 live')
  // Outside the expand button: a button inside a button is invalid, and the click that
  // opened this list must not also toggle the row it came from.
  expect(await live.evaluate(element=>!!element.closest('.git-map-summary'))).toBe(false)
  await expect(page.locator('.git-map-detail')).toHaveCount(0)

  await live.click()
  await expect(page.locator('.git-map-detail')).toHaveCount(0)
  const menu=page.locator('.git-session-links')
  // The session is named the way the sidebar names it, not by its spawned id.
  await expect(menu.locator('.git-session-link-label strong')).toHaveText('Fix sidebar Git lines')
  const box=await menu.boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.y+box!.height).toBeLessThanOrEqual(640)

  await menu.getByRole('menuitem').first().click()
  await expect(menu).toHaveCount(0)
  expect(await page.evaluate(()=>(globalThis as unknown as {__followed:string[]}).__followed)).toEqual(['session:session'])
})

test('a commit\'s session links open without expanding it, and an ended session goes to History',async({page})=>{
  await page.setViewportSize({width:360,height:640})
  await page.goto('/git-map-harness.html')
  await page.getByRole('tab',{name:'Log',exact:true}).click()
  const row=page.locator('.git-graph-row').filter({hasText:'Fix sidebar Git lines'})
  const links=row.locator('.git-commit-links')
  await expect(links).toHaveText('2 session links')
  expect(await links.evaluate(element=>!!element.closest('.git-commit-summary'))).toBe(false)

  await links.click()
  await expect(row.locator('.git-commit-detail')).toHaveCount(0)
  const entries=page.locator('.git-session-links .git-session-link-label strong')
  await expect(entries).toHaveText(['Fix sidebar Git lines','Land the migration'])

  // The second session is not in the fleet, so its work is read in History instead.
  await page.locator('.git-session-links').getByRole('menuitem').nth(1).click()
  expect(await page.evaluate(()=>(globalThis as unknown as {__followed:string[]}).__followed)).toEqual(['history:run-ended'])
})

test('a provenance row names the current session and links to it',async({page})=>{
  await page.setViewportSize({width:360,height:640})
  await page.goto('/git-map-harness.html')
  await page.getByRole('tab',{name:'Provenance',exact:true}).click()
  const names=page.locator('.git-provenance .git-session-open')
  await expect(names).toHaveText(['Fix sidebar Git lines','Land the migration'])
  await names.nth(0).click()
  expect(await page.evaluate(()=>(globalThis as unknown as {__followed:string[]}).__followed)).toEqual(['session:session'])
})

test('the Git toolbar keeps its actions together under the shared segmented control',async({page})=>{
  await page.setViewportSize({width:360,height:640})
  await page.goto('/git-map-harness.html')
  // The Map/Log/Provenance switch is the drawer's one segmented control now, drawn above
  // this tab rather than inside its toolbar, so the two must stack without overlapping and
  // the toolbar's own actions must still sit at its trailing edge.
  const geometry=await page.evaluate(()=>{
    const box=(selector:string)=>document.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON()
    return {toolbar:box('.git-toolbar'),segmented:box('.drawer-segmented'),actions:box('.git-toolbar-actions')}
  })
  expect(geometry.segmented.bottom).toBeLessThanOrEqual(geometry.toolbar.top + 0.5)
  expect(geometry.actions.right).toBeLessThanOrEqual(geometry.toolbar.right + 0.5)
  // Glyph only, but still named for a screen reader and for voice control.
  await expect(page.locator('.git-refresh')).toHaveText('↻')
  await expect(page.locator('.git-refresh')).toHaveAttribute('aria-label','Refresh')
})

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
  await page.getByRole('tab',{name:'Log',exact:true}).click()
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
