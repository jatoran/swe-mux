import { expect, test } from 'playwright/test'

/** Click the row and wait for the detail view, so a geometry read cannot race the fetch. */
const openConversation=async(page:import('playwright/test').Page)=>{
  await page.getByRole('button',{name:'[claude] Optimize Workout Plan'}).click()
  await page.locator('.transcript-heading').waitFor()
}

for(const viewport of [{name:'compact desktop',width:820,height:500},{name:'mobile',width:390,height:520}]){
  test(`history actions remain reachable with dense metadata on ${viewport.name}`,async({page})=>{
    await page.setViewportSize({width:viewport.width,height:viewport.height})
    await page.goto('/history-harness.html')
    await openConversation(page)

    const resume=page.getByRole('button',{name:'Resume',exact:true})
    await expect(resume).toBeVisible()
    await expect(page.getByRole('button',{name:/^Review with/})).toHaveCount(0)
    const geometry=await page.evaluate(()=>{
      const bounds=(selector:string)=>document.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON()
      return {
        modal:bounds('.history-modal'),resume:bounds('.transcript-actions .primary'),actions:bounds('.transcript-actions'),
        sections:[...document.querySelectorAll<HTMLElement>('.transcript-section')].map(node=>node.getBoundingClientRect().toJSON()),
        messages:bounds('.messages'),
      }
    })
    expect(geometry.resume.left).toBeGreaterThanOrEqual(geometry.modal.left)
    expect(geometry.resume.right).toBeLessThanOrEqual(geometry.modal.right)
    expect(geometry.resume.top).toBeGreaterThanOrEqual(geometry.actions.top)
    expect(geometry.resume.bottom).toBeLessThanOrEqual(geometry.actions.bottom)
    // The bands stack between the actions and the transcript, and none of them overlaps it.
    expect(geometry.sections.length).toBeGreaterThan(1)
    for(const section of geometry.sections){
      expect(section.top).toBeGreaterThanOrEqual(geometry.actions.bottom - 1)
      expect(section.bottom).toBeLessThanOrEqual(geometry.messages.top + 1)
    }
    expect(geometry.messages.height).toBeGreaterThan(20)
  })

  test(`back to results sits inside the detail top bar on ${viewport.name}`,async({page})=>{
    await page.setViewportSize({width:viewport.width,height:viewport.height})
    await page.goto('/history-harness.html')
    await openConversation(page)

    const geometry=await page.evaluate(()=>{
      const bounds=(selector:string)=>document.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON()
      return {back:bounds('.history-back'),heading:bounds('.transcript-heading'),title:bounds('.transcript-heading h3')}
    })
    expect(geometry.back.top).toBeGreaterThanOrEqual(geometry.heading.top - 1)
    expect(geometry.back.bottom).toBeLessThanOrEqual(geometry.heading.bottom + 1)
    // Beside the title, not a full-width row stacked above it.
    expect(geometry.back.right).toBeLessThanOrEqual(geometry.title.left + 1)
    expect(geometry.back.width).toBeLessThan(geometry.heading.width * 0.6)
  })
}

test('the filter block wraps compact controls instead of stretching them full width',async({page})=>{
  await page.setViewportSize({width:1200,height:800})
  await page.goto('/history-harness.html')
  await page.getByRole('button',{name:'Filter history state'}).waitFor()

  const geometry=await page.evaluate(()=>{
    const block=document.querySelector<HTMLElement>('.history-search')!
    const triggers=[...block.querySelectorAll<HTMLElement>('.dropdown-trigger')]
      .filter(node=>!node.closest('.history-query-row'))
    return {
      block:block.getBoundingClientRect().toJSON(),
      triggers:triggers.map(node=>({label:node.getAttribute('aria-label')||'',box:node.getBoundingClientRect().toJSON()})),
    }
  })
  const state=geometry.triggers.find(item=>item.label==='Filter history state')!
  const origin=geometry.triggers.find(item=>item.label==='Filter history origin')!
  // A four-item list does not occupy the sidebar, and it does not occupy half of it either.
  expect(state.box.width).toBeLessThan(geometry.block.width * 0.45)
  // Filters share rows rather than each taking one.
  expect(Math.abs(state.box.top - origin.box.top)).toBeLessThan(2)
  for(const trigger of geometry.triggers){
    expect(trigger.box.right).toBeLessThanOrEqual(geometry.block.right + 1)
  }
})

test('a phone opens a conversation on the transcript with the sections closed',async({page})=>{
  await page.setViewportSize({width:390,height:720})
  await page.goto('/history-harness.html')
  await openConversation(page)

  const geometry=await page.evaluate(()=>({
    open:[...document.querySelectorAll<HTMLDetailsElement>('.transcript-section')].filter(node=>node.open).length,
    sections:document.querySelectorAll('.transcript-section').length,
    messages:document.querySelector<HTMLElement>('.messages')!.getBoundingClientRect().toJSON(),
    main:document.querySelector<HTMLElement>('.history-body>main')!.getBoundingClientRect().toJSON(),
  }))
  expect(geometry.sections).toBeGreaterThan(1)
  expect(geometry.open).toBe(0)
  // The transcript, not a strip at the bottom under five expanded bands.
  expect(geometry.messages.height).toBeGreaterThan(geometry.main.height * 0.5)
})

test('the commits section opens into rows that each expand, not a sideways strip',async({page})=>{
  await page.setViewportSize({width:1200,height:800})
  await page.goto('/history-harness.html')
  await openConversation(page)

  const summary=page.locator('.transcript-section-commits>summary')
  await expect(summary).toContainText('6 commits · latest')
  await summary.click()

  const rows=page.locator('.transcript-section-commits .transcript-row')
  await expect(rows).toHaveCount(6)
  const stacked=await page.evaluate(()=>{
    const boxes=[...document.querySelectorAll<HTMLElement>('.transcript-section-commits .transcript-row')]
      .map(node=>node.getBoundingClientRect().toJSON())
    const body=document.querySelector<HTMLElement>('.transcript-section-commits .transcript-section-body')!
    return {boxes,scrollsSideways:body.scrollWidth>body.clientWidth+1}
  })
  expect(stacked.scrollsSideways).toBe(false)
  for(let index=1;index<stacked.boxes.length;index++){
    expect(stacked.boxes[index].top).toBeGreaterThanOrEqual(stacked.boxes[index-1].bottom - 1)
  }
  // A row carries its detail behind its own disclosure.
  await expect(page.locator('.transcript-section-commits .transcript-row').first()).not.toHaveAttribute('open','')

  // The bands are controlled `<details>`, so a toggle that never reached state would be
  // reverted by the next render somewhere else. Collapse a different band and check.
  await page.locator('.transcript-section-stats>summary').click()
  await expect(page.locator('.transcript-section-stats')).not.toHaveAttribute('open','')
  await expect(page.locator('.transcript-section-commits')).toHaveAttribute('open','')
})

test('the behavioural timeline shows two entries and hides the rest behind a disclosure',async({page})=>{
  await page.setViewportSize({width:1200,height:800})
  await page.goto('/history-harness.html')
  await openConversation(page)

  const section=page.locator('.transcript-section-timeline')
  await expect(section).toHaveAttribute('open','')
  const preview=section.locator('.transcript-section-body>.transcript-row')
  await expect(preview).toHaveCount(2)
  // Newest first, from a payload the daemon returns oldest-first.
  await expect(preview.first()).toContainText('behavioural summary number 4')
  await expect(preview.nth(1)).toContainText('behavioural summary number 3')

  const more=section.locator('.transcript-more')
  await expect(more.locator('>summary')).toHaveText('3 earlier entries')
  await more.locator('>summary').click()
  await expect(more.locator('>.transcript-row')).toHaveCount(3)
})
