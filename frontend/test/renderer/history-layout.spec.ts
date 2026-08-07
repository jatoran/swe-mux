import { expect, test } from 'playwright/test'

for(const viewport of [{name:'compact desktop',width:820,height:500},{name:'mobile',width:390,height:520}]){
  test(`history actions remain reachable with dense metadata on ${viewport.name}`,async({page})=>{
    await page.setViewportSize({width:viewport.width,height:viewport.height})
    await page.goto('/history-harness.html')
    await page.getByRole('button',{name:'[claude] Optimize Workout Plan'}).click()

    const resume=page.getByRole('button',{name:'Resume as new'})
    await expect(resume).toBeVisible()
    const geometry=await page.evaluate(()=>{
      const bounds=(selector:string)=>document.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON()
      const metadata=document.querySelector<HTMLElement>('.transcript-metadata')!
      return {
        modal:bounds('.history-modal'),resume:bounds('.transcript-actions .primary'),actions:bounds('.transcript-actions'),
        metadata:bounds('.transcript-metadata'),messages:bounds('.messages'),metadataScrollHeight:metadata.scrollHeight,
      }
    })
    expect(geometry.resume.left).toBeGreaterThanOrEqual(geometry.modal.left)
    expect(geometry.resume.right).toBeLessThanOrEqual(geometry.modal.right)
    expect(geometry.resume.top).toBeGreaterThanOrEqual(geometry.actions.top)
    expect(geometry.resume.bottom).toBeLessThanOrEqual(geometry.actions.bottom)
    expect(geometry.actions.bottom).toBeLessThanOrEqual(geometry.metadata.top)
    expect(geometry.metadata.bottom).toBeLessThanOrEqual(geometry.messages.top)
    expect(geometry.metadataScrollHeight).toBeGreaterThan(geometry.metadata.height)
    expect(geometry.messages.height).toBeGreaterThan(20)
  })
}
