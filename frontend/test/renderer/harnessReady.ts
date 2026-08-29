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
