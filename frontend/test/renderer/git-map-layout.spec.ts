import { expect, test } from 'playwright/test'

for(const width of [180,240,360]){
  test(`Git Map identity and dense metrics remain separate at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:500})
    await page.goto('/git-map-harness.html')
    const row=page.locator('.git-map-summary')
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
