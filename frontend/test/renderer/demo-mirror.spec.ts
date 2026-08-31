import { expect, test, type BrowserContext, type Page } from 'playwright/test'

/**
 * The demo's cross-frame view mirror, driven the way the landing page drives it: two
 * same-origin frames of the *same* browsing context, one laid out as a desktop and one as
 * a phone, talking over a BroadcastChannel.
 *
 * The unit suite cannot see any of this - the mirror reads the DOM the app renders and
 * converges through the app's own command bus, so both halves only exist in a browser with
 * two live copies of the app in it.
 *
 * What is under test is the one field whose meaning depends on the layout. On a desktop the
 * navigation sidebar is a column in the flow, and its state is a standing layout choice. On
 * a phone it is a modal overlay over the whole workspace, and the app closes it again the
 * moment it has been used. Mirrored across the two, each layout's constraint became the
 * other's instruction: the phone's overlay closing after a navigation collapsed the
 * desktop's fleet column, and the desktop opening its column threw a full-screen overlay
 * over the phone's terminal.
 */

const READY_TIMEOUT = 30_000
/** A converge pass is one correction per 140ms tick, bounded at twelve, and a heartbeat
 *  publishes every 900ms. Long enough that "nothing happened" means it, not that the pass
 *  had not started. */
const MIRROR_SETTLE = 3_000

type SidebarView = { narrow: boolean; overlayOpen: boolean; collapsed: boolean }

/** Both presentations of the sidebar at once, so one reading covers either layout. */
const sidebar = (page: Page): Promise<SidebarView> => page.evaluate(() => {
  const workspace = document.querySelector('.workspace')
  return {
    narrow: window.matchMedia('(max-width: 760px)').matches,
    overlayOpen: Boolean(document.querySelector('.sidebar.open')),
    collapsed: Boolean(workspace?.classList.contains('sidebar-collapsed')),
  }
})

/** Drive a frame through the app's own command bus, exactly as the mirror does. */
const command = (page: Page, id: string): Promise<void> =>
  page.evaluate(name => { window.dispatchEvent(new CustomEvent('mux:command', { detail: name })) }, id)

const openFrame = async (context: BrowserContext, width: number, height: number): Promise<Page> => {
  const page = await context.newPage()
  await page.setViewportSize({ width, height })
  await page.goto('/demo.html?deterministic=1')
  await page.waitForSelector('.workspace', { timeout: READY_TIMEOUT })
  return page
}

/**
 * A pair of frames that have found each other.
 *
 * The walkthrough is marked seen before the first navigation: a director run would drive
 * controls on its own, and one of the two frames wins the lead election, so the frame under
 * test would be being operated by something other than this spec.
 */
const pair = async (
  context: BrowserContext,
  layouts: readonly [[number, number], [number, number]],
): Promise<[Page, Page]> => {
  await context.addInitScript(() => { localStorage.setItem('swemux-demo-coach-v1', 'done') })
  const first = await openFrame(context, layouts[0][0], layouts[0][1])
  const second = await openFrame(context, layouts[1][0], layouts[1][1])
  // The mirror publishes on a heartbeat until a peer has spoken, so give both frames a
  // chance to hear each other before anything is asserted about what they do with it.
  await first.waitForTimeout(MIRROR_SETTLE)
  return [first, second]
}

const DESKTOP: [number, number] = [1280, 800]
const PHONE: [number, number] = [380, 780]

test('a phone closing its sidebar overlay leaves the desktop column alone', async ({ browser }) => {
  const context = await browser.newContext()
  const [desktop, phone] = await pair(context, [DESKTOP, PHONE])

  // Opening it is the only way to see the fleet on a phone, and the app shuts it again on
  // the next navigation - so this close is a consequence of the layout, not a choice.
  await command(phone, 'sidebar.open')
  await phone.waitForTimeout(MIRROR_SETTLE)
  expect(await sidebar(phone)).toMatchObject({ narrow: true, overlayOpen: true })
  expect(await sidebar(desktop)).toMatchObject({ collapsed: false })

  await command(phone, 'sidebar.close')
  await phone.waitForTimeout(MIRROR_SETTLE)
  expect(await sidebar(phone)).toMatchObject({ overlayOpen: false })
  // The bug: the desktop was told "the sidebar is shut" and collapsed its fleet column.
  expect(await sidebar(desktop)).toMatchObject({ collapsed: false })

  await context.close()
})

test('a desktop opening its column does not throw an overlay over the phone', async ({ browser }) => {
  const context = await browser.newContext()
  const [desktop, phone] = await pair(context, [DESKTOP, PHONE])

  await command(desktop, 'sidebar.close')
  await desktop.waitForTimeout(MIRROR_SETTLE)
  expect(await sidebar(desktop)).toMatchObject({ collapsed: true })
  expect(await sidebar(phone)).toMatchObject({ overlayOpen: false })

  await command(desktop, 'sidebar.open')
  await desktop.waitForTimeout(MIRROR_SETTLE)
  expect(await sidebar(desktop)).toMatchObject({ collapsed: false })
  // The other half: a full-screen overlay the phone's visitor never asked for, over the
  // terminal they were reading.
  expect(await sidebar(phone)).toMatchObject({ overlayOpen: false })

  await context.close()
})

test('two frames of one layout still mirror the sidebar', async ({ browser }) => {
  // The gate is layout parity, not "never mirror the sidebar" - and this is the assertion
  // that tells those two apart.
  const context = await browser.newContext()
  const [led, follower] = await pair(context, [DESKTOP, DESKTOP])

  await command(led, 'sidebar.close')
  await expect.poll(async () => (await sidebar(follower)).collapsed, { timeout: 10_000 }).toBe(true)

  await command(led, 'sidebar.open')
  await expect.poll(async () => (await sidebar(follower)).collapsed, { timeout: 10_000 }).toBe(false)

  await context.close()
})

test('everything that is not layout-local still mirrors across layouts', async ({ browser }) => {
  // The demo's headline behaviour - act on the desktop, watch the phone follow - has to
  // survive the sidebar being carved out of it.
  const context = await browser.newContext()
  const [desktop, phone] = await pair(context, [DESKTOP, PHONE])

  await command(desktop, 'resources.open')
  await expect.poll(
    () => phone.evaluate(() => document.querySelector('[role="dialog"][aria-modal="true"]')?.getAttribute('aria-label') || ''),
    { timeout: 10_000 },
  ).toBe('Resources')

  await command(desktop, 'drawer.show:git')
  await expect.poll(
    () => phone.evaluate(() => document.querySelector('[data-drawer-tab-id][aria-selected="true"]')?.getAttribute('data-drawer-tab-id') || ''),
    { timeout: 15_000 },
  ).toBe('git')

  await context.close()
})
