import { type Page } from 'playwright/test'

/**
 * Wait until a harness has installed the global a spec is about to call.
 *
 * `page.goto` resolves on the `load` event, but a harness page installs its hooks
 * (`Object.assign(globalThis, {...})`) when its **module script** executes, and under
 * Vite that can land after `load`. So `goto` then `page.evaluate(() => globalThis.__hook())`
 * is a race: it wins on an idle machine and loses on a busy one, and the failure reads as
 * `TypeError: globalThis.__hook is not a function` rather than as a timing problem.
 *
 * It lost on CI on 2026-08-29 (`git-map-select.spec.ts`, `__refuse`, one failure in 384),
 * on a commit that touched no frontend code at all. Three other call sites had the same
 * shape and had simply not lost yet: `decrqm.spec.ts`, and `jump-latest.spec.ts` twice.
 *
 * Most specs never hit this because they assert on the rendered UI before touching a hook,
 * and that `expect` waits long enough for the module to have run. The ones that bite are the
 * ones that reach for a hook *first*, with nothing in between.
 *
 * This is the same rule the Python suite already follows: wait for the condition rather than
 * assume the window (root `CLAUDE.md`, on fixed sleeps before a positive assertion). It also
 * returns as soon as the hook exists, so it costs nothing on an idle machine.
 */
export async function harnessReady(page: Page, ...globals: string[]): Promise<void> {
  await page.waitForFunction(
    names => names.every(name => typeof (globalThis as Record<string, unknown>)[name] === 'function'),
    globals,
  )
}

/**
 * Let the animation frames that publish measured layout run before reading their output.
 *
 * Some values are not in the markup and are not set on mount: they are measured and written
 * to the document by a callback scheduled on `requestAnimationFrame`. `--rail-clearance` is
 * the one this was written for - `registerRailClearance` measures every rail that reaches the
 * bottom of the viewport and publishes the tallest, because rail height is a row count times a
 * density variable and cannot be a constant.
 *
 * A spec that asserts on such a value right after `expect(...).toBeVisible()` is racing the
 * frame that produces it: the element is visible one frame before the measurement lands. That
 * read returns the CSS default (`:root{--rail-clearance:0px}`), which looks exactly like the
 * feature being broken. It failed that way on this host while passing on CI.
 *
 * Two frames rather than one, because the `ResizeObserver` path costs a frame to fire and
 * another for the coalesced `measure` it schedules.
 *
 * **Wait for the frame, not for the value.** `waitForFunction(() => value !== default)` would
 * also go green, and would assert nothing - the test's whole subject is that the published
 * value is not the default.
 */
export async function measuredLayoutSettled(page: Page): Promise<void> {
  await page.evaluate(
    () =>
      new Promise<void>(resolve => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      }),
  )
}
