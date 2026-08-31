import { expect, test } from 'playwright/test'

// The declared navigation model, so the orphan check below compares the rendered
// DOM against the registry itself rather than against a second list written here
// that would drift the moment a page is added.
import { settingsSubpages, settingsTabs, type SettingsTab } from '../../src/settingsTabs'

const tabId = (label:string):SettingsTab => {
  const found = settingsTabs.find(tab => tab.label === label)
  if (!found) throw new Error(`no Settings tab is labelled ${label}`)
  return found.id
}

/**
 * Settings' two layouts, measured rather than described.
 *
 * `settingsTabs.test.ts` proves the navigation model; none of it can see where anything
 * lands. What is asserted here is the part that is CSS and only CSS:
 *
 *  - narrow, the header is one row — the search box sits between the title and the close
 *    button instead of wrapping onto a line of its own, which is the vertical space this
 *    layout was changed to recover;
 *  - narrow, the section list is a drawer: genuinely off screen and out of the focus
 *    order when closed, over the content and inside the panel when open;
 *  - wide, it is still the docked column, and the drawer chrome is absent.
 *
 * The groups are asserted in both, because "see the categories" is the reason the phone
 * gets the desktop's list instead of the flat rail it used to have.
 */

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1280, height: 900 }

type Page = import('playwright/test').Page

test('Notes settings expose the global Scratchpad visibility toggle',async({page})=>{
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  await page.getByRole('tab',{name:'Notes',exact:true}).click()
  const toggle=page.locator('[data-setting="note_scratchpad_enabled"]')
  await expect(toggle).toContainText('Global Scratchpad')
  await expect(toggle.locator('input[type="checkbox"]')).toBeChecked()
})

async function chrome(page: Page) {
  return page.evaluate(() => {
    const box = (selector: string) => {
      const element = document.querySelector(selector)
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { x: rect.x, right: rect.right, y: rect.y, bottom: rect.bottom, width: rect.width, height: rect.height }
    }
    const nav = document.querySelector('.settings-tabs') as HTMLElement | null
    const scrim = document.querySelector('.settings-nav-scrim') as HTMLElement | null
    const content = document.querySelector('.settings-content') as HTMLElement | null
    return {
      header: box('.settings-panel>header'),
      heading: box('.settings-heading'),
      search: box('.settings-panel>header>div.settings-search'),
      close: box('.settings-panel>header>.settings-close'),
      trigger: box('.settings-panel>header>.settings-nav-trigger'),
      body: box('.settings-body'),
      content: box('.settings-content'),
      nav: box('.settings-tabs'),
      navVisibility: nav ? getComputedStyle(nav).visibility : null,
      isDrawer: !!document.querySelector('.settings-tabs-drawer'),
      scrims: document.querySelectorAll('.settings-nav-scrim').length,
      scrimBackground: scrim ? getComputedStyle(scrim).backgroundColor : null,
      contentOpacity: content ? getComputedStyle(content).opacity : null,
      groups: [...document.querySelectorAll('.settings-tab-group>span')]
        .filter(element => getComputedStyle(element).display !== 'none')
        .map(element => element.textContent),
      headingText: document.querySelector('.settings-heading')?.textContent || '',
      // Asked of the browser rather than inferred: a `visibility:hidden` element keeps its
      // offsetParent and still answers a bounding-box query, and the only thing that
      // settles whether a closed drawer is out of the way is whether focus takes.
      focusableInNav: nav
        ? [...nav.querySelectorAll('button')].filter(button => {
            button.focus()
            return document.activeElement === button
          }).length
        : 0,
    }
  })
}

test('narrow: the header is one row with the search inline, and the section list is a closed drawer', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })
  const g = await chrome(page)

  // One row: every header child shares the header's vertical band. A wrapped search box
  // is what this asserts against — it used to sit on its own line below the title.
  expect(g.trigger).not.toBeNull()
  for (const part of [g.trigger!, g.heading!, g.search!, g.close!]) {
    expect(part.y).toBeGreaterThanOrEqual(g.header!.y - 0.5)
    expect(part.bottom).toBeLessThanOrEqual(g.header!.bottom + 0.5)
  }
  // ...and in that order, left to right.
  expect(g.trigger!.x).toBeLessThan(g.heading!.x)
  expect(g.heading!.x).toBeLessThan(g.search!.x)
  expect(g.search!.right).toBeLessThanOrEqual(g.close!.x + 0.5)
  // The whole header is one row of a two-line title, not three stacked controls.
  expect(g.header!.height).toBeLessThan(64)

  // The title names where you are, not what the panel is.
  expect(g.headingText).toContain('SETTINGS')
  expect(g.headingText).toContain('General')

  // Closed drawer: off to the left of the panel, invisible, and unreachable by Tab.
  expect(g.isDrawer).toBe(true)
  expect(g.navVisibility).toBe('hidden')
  expect(g.nav!.right).toBeLessThanOrEqual(g.body!.x + 0.5)
  expect(g.focusableInNav).toBe(0)
  expect(g.scrims).toBe(0)
  // The content has the full width, because the drawer is out of flow.
  expect(g.content!.width).toBeGreaterThan(PHONE.width - 2)
})

test('narrow: the hamburger and the title both open the drawer over the content', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })

  for (const opener of ['.settings-nav-trigger', '.settings-heading-trigger']) {
    await page.click(opener)
    await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(1)
    // The transform has to finish before the geometry means anything.
    await page.waitForTimeout(250)
    const open = await chrome(page)
    expect(open.navVisibility).toBe('visible')
    expect(open.nav!.x).toBeGreaterThanOrEqual(open.body!.x - 0.5)
    // Over the content, not beside it: the content keeps its full width underneath.
    expect(open.content!.width).toBeGreaterThan(PHONE.width - 2)
    expect(open.nav!.right).toBeLessThan(PHONE.width)
    expect(open.scrims).toBe(1)
    expect(open.scrimBackground).toBe('rgba(0, 0, 0, 0)')
    expect(open.contentOpacity).toBe('1')
    expect(open.focusableInNav).toBeGreaterThan(10)
    // The categories are the point of borrowing the desktop list.
    expect(open.groups).toEqual(['Workspace', 'Agents', 'Interface', 'System'])

    // The scrim closes it again, which is how the workspace sidebar behaves.
    // To the right of the drawer: the scrim spans the whole body and the drawer is
    // painted over its left end, exactly as the workspace sidebar sits over its own.
    await page.click('.settings-nav-scrim', { position: { x: PHONE.width - 30, y: 400 } })
    await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(0)
    await page.waitForTimeout(250)
    expect((await chrome(page)).navVisibility).toBe('hidden')
  }
})

test('narrow: picking a section closes the drawer and retitles the header', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })

  await page.click('.settings-nav-trigger')
  await page.click('.settings-tabs button:text-is("Appearance")')
  await expect(page.locator('.settings-tabs-drawer.open')).toHaveCount(0)
  await page.waitForTimeout(250)

  const g = await chrome(page)
  expect(g.headingText).toContain('Appearance')
  expect(g.navVisibility).toBe('hidden')
  expect(g.focusableInNav).toBe(0)
  await expect(page.locator('.settings-content h3').first()).toHaveText('Theme')
})

test('narrow: the soft keyboard shortens the panel instead of hiding its footer', async ({ page }) => {
  // The bug this pins: on a phone the panel is sized to `100dvh`, and `100dvh` is the
  // *layout* viewport, which `interactive-widget=resizes-visual` deliberately keeps at full
  // height while the keyboard is up (see `updateAppHeight` in `App.tsx`). So the footer -
  // where Save lives - sat under the keyboard, unreachable by scrolling, because scrolling
  // moves the middle row and not the fixed one below it. It is a fixed panel like the drawer
  // and the sidebar overlay, and it takes the same treatment they do.
  //
  // The keyboard is simulated the way the app publishes it, because that is the whole
  // contract: `--keyboard-inset` and `--visual-offset` on the root, from which the stylesheet
  // derives `--keyboard-cover`. Playwright cannot raise a real one. The rules are ungated by
  // `.soft-keyboard-open`, so the lengths alone are what has to be enough.
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  await page.waitForSelector('.settings-panel>footer button', { state: 'attached' })

  const INSET = 320
  const before = await page.evaluate(() => document.querySelector('.settings-panel>footer')!.getBoundingClientRect().bottom)
  expect(before).toBeGreaterThan(PHONE.height - 4)

  const after = await page.evaluate(inset => {
    document.documentElement.style.setProperty('--keyboard-inset', `${inset}px`)
    document.documentElement.style.setProperty('--visual-offset', '0px')
    const footer = document.querySelector<HTMLElement>('.settings-panel>footer')!
    const save = [...footer.querySelectorAll('button')].pop()!
    return {
      footer: footer.getBoundingClientRect().bottom,
      save: save.getBoundingClientRect().bottom,
      // The scroller is what gives up the height, which is what keeps every control
      // reachable rather than merely keeping the buttons on screen.
      content: document.querySelector('.settings-body')!.getBoundingClientRect().height,
    }
  }, INSET)

  const visible = PHONE.height - INSET
  expect(after.footer).toBeLessThanOrEqual(visible + 1)
  expect(after.save).toBeLessThanOrEqual(visible + 1)
  expect(after.content).toBeGreaterThan(0)
})

test('wide: the section list is the docked column it always was', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  // Attached, not visible: a closed drawer is `visibility:hidden`, which is the point.
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })
  const g = await chrome(page)

  expect(g.isDrawer).toBe(false)
  expect(g.trigger).toBeNull()
  expect(g.navVisibility).toBe('visible')
  expect(g.scrims).toBe(0)
  // Docked: the column and the content are side by side and do not overlap.
  expect(g.nav!.right).toBeLessThanOrEqual(g.content!.x + 0.5)
  expect(g.groups).toEqual(['Workspace', 'Agents', 'Interface', 'System'])
  expect(g.headingText).toContain('CONFIG::V6')
  expect(g.headingText).toContain('Settings')

  // Wide, the search sits above the section list rather than in the header: it drives
  // that list, and the header spends the width on nothing else.
  const search = await page.evaluate(() => {
    const inCol = document.querySelector('.settings-nav-col>.settings-search input')
    const inHeader = document.querySelector('.settings-panel>header .settings-search')
    const nav = document.querySelector('.settings-tabs')!.getBoundingClientRect()
    const box = inCol?.getBoundingClientRect()
    return { inCol: !!inCol, inHeader: !!inHeader, above: box ? box.bottom <= nav.top + 0.5 : false }
  })
  expect(search.inCol).toBe(true)
  expect(search.inHeader).toBe(false)
  expect(search.above).toBe(true)
})

test('the active Settings tab adds no marker or indentation', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  const general=page.locator('.settings-tabs [role="tab"]',{hasText:'General'}).first()
  const projects=page.locator('.settings-tabs [role="tab"]',{hasText:'Projects'}).first()
  await expect(general).toHaveAttribute('aria-selected','true')

  const measure=async()=>page.locator('.settings-tabs [role="tab"]').evaluateAll(buttons=>buttons.slice(0,2).map(button=>{
    const element=button as HTMLElement
    const text=element.firstChild
    const range=document.createRange()
    if(text)range.selectNodeContents(text)
    return {
      marker:getComputedStyle(element,'::before').content,
      textLeft:Math.round(range.getBoundingClientRect().left),
    }
  }))

  const before=await measure()
  expect(before[0].marker).toBe('none')
  expect(before[1].marker).toBe('none')
  expect(before[0].textLeft).toBe(before[1].textLeft)
  await projects.click()
  await expect(projects).toHaveAttribute('aria-selected','true')
  const after=await measure()
  expect(after[0].marker).toBe('none')
  expect(after[1].marker).toBe('none')
  expect(after[0].textLeft).toBe(after[1].textLeft)
})

test('Actions owns rail policy and the embedded layout editor',async({page})=>{
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  await page.locator('.settings-tabs [role="tab"]',{hasText:/^Actions$/}).click()
  await expect(page.locator('.settings-content h3',{hasText:/^Action rail$/})).toBeVisible()
  await expect(page.locator('.settings-content h3',{hasText:/^Action layout$/})).toBeVisible()
  await expect(page.locator('.settings-content .commandrail-settings')).toBeVisible()
  await expect(page.locator('.action-editor-modal,.action-editor-layer')).toHaveCount(0)

  await page.locator('.settings-tabs [role="tab"]',{hasText:/^Appearance$/}).click()
  await expect(page.locator('.settings-content h3',{hasText:/^Action rail$/})).toHaveCount(0)
})

test('every paged Settings tab exposes working page links in the sidebar', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  // Only genuinely long tabs are paged; everything else is one scrolling column whose
  // sections the sidebar lists as anchors while the tab is active.
  const pagedTabs=['Accounts','Prompt queue','Input','Voice']

  for(const tab of pagedTabs){
    const row=page.locator('.settings-tab-row',{has:page.locator('[role="tab"]',{hasText:new RegExp(`^${tab}$`)})})
    await row.locator('[role="tab"]').click()
    // Selecting the tab expands its pages without touching the chevron.
    const nav=row.locator('xpath=following-sibling::div[contains(@class,"settings-subtabs")][1]')
    await expect(nav).toBeVisible()
    const pages=await nav.locator('button').allTextContents()
    expect(pages.length,`${tab} must expose more than one page`).toBeGreaterThan(1)
    for(const label of pages){
      await nav.getByRole('button',{name:label,exact:true}).click()
      await expect(nav.locator('button.active')).toHaveText(label)
      const visible=page.locator('.settings-content section[data-settings-subpage]:visible')
      expect(await visible.count(),`${tab} > ${label} rendered no page`).toBeGreaterThan(0)
      expect((await visible.first().innerText()).trim(),`${tab} > ${label} rendered blank`).not.toBe('')
    }

    // Every rendered section has to belong to a page this tab declares.
    //
    // The loop above cannot catch what this does, and both escapes shipped:
    // switching page sets `hidden` on every section that is not the selected
    // one, so a section whose id matches no declared page is hidden on *every*
    // page and its content is unreachable, while the link that should have
    // opened it is treated as invalid and silently falls back to page one. The
    // visible-and-non-blank assertions above stay true throughout, because a
    // fallback page is visible and non-blank.
    //
    // Measured when this assertion was added: Input's shortcuts section was
    // filed under `what-this-browser-gives-the-app` (its last heading rather
    // than its first), so the Keyboard shortcuts page drew nothing at all; and
    // Accounts rendered an `openrouter` section - the API key field - that no
    // page could reach. `groupedHeadings` is where a section with several
    // headings declares which page owns it.
    const declared=new Set((settingsSubpages[tabId(tab)]||[]).map(entry=>entry.id))
    const rendered=await page.locator('.settings-content section[data-settings-subpage]')
      .evaluateAll(nodes=>nodes.map(node=>({
        id:node.getAttribute('data-settings-subpage')||'',
        heading:node.querySelector('h3')?.textContent?.trim()||'(no heading)',
      })))
    const orphans=rendered.filter(section=>!declared.has(section.id))
    expect(orphans,
      `${tab}: these sections belong to no declared page, so nothing can reach them: `
      + `${JSON.stringify(orphans)}. Declare the page, or map the heading onto an `
      + 'existing one in `groupedHeadings` (settingsTabs.ts).',
    ).toEqual([])
  }
})

for (const [device, viewport] of [['desktop', DESKTOP], ['mobile', PHONE]] as const) {
  test(`Session rows owns a sticky, expandable live preview on ${device}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.goto('/settings-harness.html?section=Appearance&setting=session_rows')
    const heading=page.locator('.settings-content h3',{hasText:/^Session rows$/})
    await expect(heading).toBeVisible()
    await expect(page.locator('.settings-content h3',{hasText:/^Theme$/})).toBeHidden()

    const preview=page.locator('.session-row-preview-sticky')
    const rows=preview.locator('.session-row')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toHaveClass(/working/)
    expect(await preview.evaluate(node=>getComputedStyle(node).position)).toBe('sticky')

    await preview.getByRole('button',{name:'Show examples'}).click()
    await expect(rows).toHaveCount(4)
    await preview.getByRole('button',{name:'Show one row'}).click()
    await expect(rows).toHaveCount(1)

    await page.locator('.settings-content').evaluate(node=>{node.scrollTop=600})
    await expect.poll(async()=>preview.evaluate(node=>{
      const box=node.getBoundingClientRect()
      const scroller=node.closest('.settings-content')!.getBoundingClientRect()
      return Math.abs(box.top-scroller.top)
    })).toBeLessThan(1)
  })
}

test('the sticky session-row preview updates before its settings write finishes', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html?section=Appearance&setting=session_rows')
  const row=page.locator('.session-row-preview .session-row').first()
  await expect(row).toContainText('5-codex')
  await page.getByRole('button',{name:'Minimal',exact:true}).click()
  await expect(row).not.toContainText('5-codex')
})

for(const [device,viewport] of [['desktop',DESKTOP],['mobile',PHONE]] as const){
  test(`Session top bars owns a sticky realtime preview on ${device}`,async({page})=>{
    await page.setViewportSize(viewport)
    await page.goto('/settings-harness.html?section=Appearance&setting=session_topbar')
    await expect(page.locator('.settings-content h3',{hasText:/^Session top bars$/})).toBeVisible()
    await expect(page.locator('.settings-content h3',{hasText:/^Theme$/})).toBeHidden()
    const preview=page.locator('.session-topbar-preview-sticky')
    expect(await preview.evaluate(node=>getComputedStyle(node).position)).toBe('sticky')
    await expect(preview.locator('.session-topbar-row')).toHaveCount(1)
    await expect(preview.locator('.approval-chip')).toContainText('appr:wait')
    await expect(preview.locator('.queue-chip')).toContainText('queue:2')
    await expect(preview.locator('.transcript-chip')).toContainText('transcript')
    await expect(preview.getByRole('button',{name:'More actions'})).toBeVisible()
    const addRow=page.getByRole('button',{name:'Add row',exact:true})
    await addRow.click();await addRow.click()
    await page.locator('.settings-content').evaluate(node=>{node.scrollTop=600})
    await expect.poll(async()=>preview.evaluate(node=>Math.abs(node.getBoundingClientRect().top-node.closest('.settings-content')!.getBoundingClientRect().top))).toBeLessThan(1)
  })
}

test('top-bar row count and shortcuts update the preview immediately',async({page})=>{
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html?section=Appearance&setting=session_topbar')
  const preview=page.locator('.session-topbar-preview')
  const addRow=page.getByRole('button',{name:'Add row',exact:true})
  await addRow.click();await expect(preview.locator('.session-topbar-row')).toHaveCount(2)
  await addRow.click();await expect(preview.locator('.session-topbar-row')).toHaveCount(3)
  await expect(addRow).toBeDisabled()

  const transcriptSlot=page.locator('.topbar-slot').filter({hasText:'Transcript'}).first()
  await transcriptSlot.getByTitle('Remove').click()
  await expect(preview.locator('.transcript-chip')).toHaveCount(0)
  const add=page.locator('.topbar-add-items').first()
  await add.locator('summary').click()
  await add.getByRole('button',{name:'Processes',exact:true}).click()
  await expect(preview.getByRole('button',{name:'processes',exact:true})).toBeVisible()
})

test('an unpaged tab lists its rendered sections in the sidebar, and the chevron collapses them', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  const row=page.locator('.settings-tab-row',{has:page.locator('[role="tab"]',{hasText:/^Remote$/})})
  await row.locator('[role="tab"]').click()

  const nav=row.locator('xpath=following-sibling::div[contains(@class,"settings-subtabs")][1]')
  await expect(nav).toBeVisible()
  const sections=await nav.locator('button').allTextContents()
  expect(sections).toContain('Tailnet listener')
  expect(sections.length).toBeGreaterThan(2)

  // The content pane carries no second copy of this navigation.
  await expect(page.locator('.settings-subpage-nav')).toHaveCount(0)
  await expect(page.locator('.settings-section-rail')).toHaveCount(0)

  // Explicit collapse is the only collapse: the chevron folds the tree and it stays
  // folded; selecting the tab again re-expands it.
  await row.locator('.settings-tab-expand').click()
  await expect(nav).toHaveCount(0)
  await page.locator('.settings-tabs [role="tab"]',{hasText:/^General$/}).click()
  await expect(nav).toHaveCount(0)
  await row.locator('[role="tab"]').click()
  await expect(nav).toBeVisible()
})

test('a tab discloses its sections before it has ever been on screen', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  // Only the opening tab has a DOM, so a chevron on any other tab can only have come
  // from that tab's vnodes. Reading the DOM alone is what used to make the disclosure
  // appear on a tab's second visit and not its first.
  const row=page.locator('.settings-tab-row',{has:page.locator('[role="tab"]',{hasText:/^Remote$/})})
  await expect(row.locator('[role="tab"]')).toHaveAttribute('aria-selected','false')
  await row.locator('.settings-tab-expand').click()

  const nav=row.locator('xpath=following-sibling::div[contains(@class,"settings-subtabs")][1]')
  await expect(nav).toBeVisible()
  expect(await nav.locator('button').allTextContents()).toContain('Firewall')

  // And the link works from there: it selects the tab it belongs to first, because the
  // heading it names is not in the document until then.
  await nav.getByRole('button',{name:'Firewall',exact:true}).click()
  await expect(row.locator('[role="tab"]')).toHaveAttribute('aria-selected','true')
  await expect(page.locator('.settings-content h3',{hasText:/^Firewall$/})).toBeInViewport()
})

test('sidebar pages and sections cue their destination heading and section border', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')

  const remote=page.locator('.settings-tab-row',{has:page.locator('[role="tab"]',{hasText:/^Remote$/})})
  await remote.locator('.settings-tab-expand').click()
  await remote.locator('xpath=following-sibling::div[contains(@class,"settings-subtabs")][1]').getByRole('button',{name:'Firewall',exact:true}).click()
  const firewall=page.locator('.settings-content h3',{hasText:/^Firewall$/})
  await expect(firewall).toHaveClass(/setting-section-heading-flash/)
  await expect(firewall.locator('xpath=ancestor::section[1]')).toHaveClass(/setting-section-flash/)

  const input=page.locator('.settings-tab-row',{has:page.locator('[role="tab"]',{hasText:/^Input$/})})
  await input.locator('[role="tab"]').click()
  const pages=input.locator('xpath=following-sibling::div[contains(@class,"settings-subtabs")][1]')
  await pages.getByRole('button',{name:'Clipboard history',exact:true}).click()
  const clipboard=page.locator('.settings-content h3',{hasText:/^Clipboard history$/})
  await expect(clipboard).toHaveClass(/setting-section-heading-flash/)
  const clipboardSection=clipboard.locator('xpath=ancestor::section[1]')
  await expect(clipboardSection).toHaveClass(/setting-section-flash/)
  await expect(clipboard).not.toHaveClass(/setting-section-heading-flash/,{timeout:4000})
  await expect(clipboardSection).not.toHaveClass(/setting-section-flash/)
})

test('Settings search flashes the exact result and its section context', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  const search=page.getByRole('combobox',{name:'Search settings'})
  await search.fill('scrollback bytes')
  await page.locator('.settings-search-results').getByRole('option',{name:/Scrollback bytes/}).click()

  const target=page.locator('.settings-content label').filter({hasText:'Scrollback bytes'}).first()
  const section=target.locator('xpath=ancestor::section[1]')
  const heading=section.locator('h3').first()
  await expect(target).toHaveClass(/setting-flash/)
  await expect(heading).toHaveClass(/setting-section-heading-flash/)
  await expect(section).toHaveClass(/setting-section-flash/)
})

test('switching tabs never files one tab sections under another', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  const groups=async()=>page.evaluate(()=>[...document.querySelectorAll('.settings-subtabs')].map(group=>({
    label:group.getAttribute('aria-label'),
    buttons:[...group.querySelectorAll('button')].map(button=>button.textContent),
  })))
  await expect(page.locator('.settings-subtabs')).toHaveCount(1)
  const [general]=await groups()
  expect(general.label).toBe('General pages')

  // The heading read is driven by a MutationObserver, whose callback is a microtask
  // while the effect cleanup that would retire it is not - so the outgoing tab can be
  // scheduled to read the incoming tab's DOM. The sidebar now keeps that answer, so the
  // misattribution outlives the render instead of correcting itself on the next one.
  for(const tab of ['Voice','Remote','Terminals','General']){
    await page.locator('.settings-tabs [role="tab"]',{hasText:new RegExp(`^${tab}$`)}).click()
    await expect(page.locator('.settings-tab-row>[role="tab"].active')).toHaveText(tab)
    const after=(await groups()).find(group=>group.label==='General pages')
    expect(after?.buttons,`General was rewritten while opening ${tab}`).toEqual(general.buttons)
  }
})

test('a tab with one section is not given a disclosure', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  // `SECTION_RAIL_MIN`: listing a lone section is a row spent saying what one glance
  // already shows. The rule is the section count, not whether the tab has been opened.
  for(const tab of ['Git','Automation','Alerts']){
    const row=page.locator('.settings-tab-row',{has:page.locator('[role="tab"]',{hasText:new RegExp(`^${tab}$`)})})
    await expect(row.locator('.settings-tab-expand'),`${tab} has one section`).toHaveCount(0)
    await row.locator('[role="tab"]').click()
    await expect(row.locator('.settings-tab-expand'),`${tab} gained one by being opened`).toHaveCount(0)
  }
})

test('every tab renders, and every marked control is really in its DOM', async ({ page }) => {
  // `settingsCoverage.test.ts` walks from `config.py` to a control, and it reads *source*:
  // it cannot tell a control that renders from one sitting behind a condition that is never
  // true, or from a tab that throws before it gets there. This is the half only a browser
  // can answer, and it is worth one pass because the coverage pass that added thirty
  // controls added them across seven tabs at once.
  const errors: string[] = []
  let visiting = ''
  // Attributed to the tab being opened, because "something threw" is unactionable across
  // eighteen of them and the stack from a bundled build names no section.
  page.on('pageerror', error => errors.push(`${visiting}: ${error}`))
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  await page.waitForSelector('.settings-tabs button', { state: 'attached' })

  // 18 since the Plugins tab (2026-08-31). The count is asserted so a tab that
  // fails to register vanishes loudly rather than being silently skipped by the
  // walk below.
  const tabs = await page.locator('.settings-tabs [role="tab"]').allTextContents()
  expect(tabs.length).toBe(settingsTabs.length)

  const marked = new Set<string>()
  for (const label of tabs) {
    visiting = label
    await page.locator('.settings-tabs [role="tab"]', { hasText: label }).first().click()
    // Every tab renders at least one section heading; a tab that threw renders none.
    await expect(page.locator('.settings-content h3').first()).toBeVisible()
    for (const value of await page.locator('.settings-content [data-setting]').evaluateAll(
      nodes => nodes.map(node => node.getAttribute('data-setting') || ''),
    )) marked.add(value)
  }
  expect(errors).toEqual([])

  // A sample from each tab this pass touched, so a control that stops rendering fails here
  // rather than only when someone follows a link to it.
  for (const setting of [
    'attach_replay_bytes', 'session_recovery_checkpoint_bytes', 'ghost_window_sweep_enabled',
    'status_timeline_retention_days', 'agent_interject_enabled', 'agent_message_max_chars',
    'request_spawn_enabled', 'session_watch_max_minutes', 'prompt_queue_retention_days',
    'openrouter_request_timeout_seconds',
    'assistant_stream_replies', 'log_level',
  ]) expect(marked, `${setting} is marked in the source but never rendered`).toContain(setting)
})
