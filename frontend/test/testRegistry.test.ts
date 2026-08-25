import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync, readdirSync } from 'node:fs'

/**
 * Every `*.test.ts` in this directory is imported by `all.ts`.
 *
 * `npm test` runs one entry point that imports its suites by hand, so a new test file is
 * not run until somebody remembers to add a line - and nothing anywhere says when they
 * forgot. The failure is silent in the worst direction: the file passes when run on its
 * own, the gate goes green, and the suite it was written to protect is not protected. It
 * had already happened four times (`grants.test.ts` carries its own note in `all.ts`, and
 * `railDensity`, `railGlassContrast` and `usagePots` were all found unrun by this test the
 * day it was written - `railGlassContrast` being the one `ui.md` credits with holding the
 * rail overlays' contrast floors).
 *
 * Read off the directory rather than a list kept here, because a list would be the same
 * bug one level up.
 */

/**
 * Files this list may legitimately not carry, each with the reason.
 *
 * An exemption is the *only* thing that keeps the guard honest, so it costs a sentence
 * saying why - and the reason has to be that the file cannot run at all here, never that
 * it fails.
 */
const NOT_REGISTERED: Readonly<Record<string, string>> = {
  // Reaches `usageAnalytics` -> `ProviderAccounts.tsx` through extensionless specifiers,
  // which the type-stripping runner cannot resolve. Registering it means changing import
  // specifiers along a chain of production components written for the bundler, which is a
  // change of its own rather than a line in this list.
  'usagePots.test.ts': 'unresolvable under node --experimental-strip-types',
  'testRegistry.test.ts': 'this file',
}

test('every test file is registered in all.ts', () => {
  const here = new URL('.', import.meta.url)
  const registry = readFileSync(new URL('all.ts', here), 'utf8')
  const imported = new Set(
    [...registry.matchAll(/from\s+'\.\/(.+?)'|import\s+'\.\/(.+?)'/g)]
      .map(match => match[1] ?? match[2]),
  )
  const missing = readdirSync(here)
    .filter(name => name.endsWith('.test.ts') && !(name in NOT_REGISTERED))
    .filter(name => !imported.has(name))
  assert.deepEqual(missing, [], `add these to test/all.ts: ${missing.join(', ')}`)
})

/**
 * Every `*.test.ts` puts its assertions inside a `node:test` `test()` body.
 *
 * A file that asserts at module scope instead is the worst failure this suite has: when
 * such an assertion throws, the runner reports it as "a resource generated asynchronous
 * activity after the test ended", the import chain in `all.ts` stops there, and every
 * suite after it is never registered - while the summary still reads `# fail 0`. Measured
 * 2026-08-24 by breaking one module-scope assertion in `voiceComms.test.ts`: 2042 tests
 * became 1047, all reported passing. The exit code was 1, so CI would have caught it, but
 * anything reading the summary - a human, or a `| grep fail` - reads it as green.
 *
 * Twelve files were written that way; all twelve were converted, and this keeps the
 * thirteenth from being written.
 */
test('every test file registers its assertions with node:test', () => {
  const here = new URL('.', import.meta.url)
  const offenders = readdirSync(here)
    .filter(name => name.endsWith('.test.ts'))
    .filter(name => {
      const source = readFileSync(new URL(name, here), 'utf8')
      return !/^import .*from 'node:test'$/m.test(source) || !/(^|[^.\w])test\(/m.test(source)
    })
  assert.deepEqual(offenders, [], `these assert at module scope - move them into test() bodies: ${offenders.join(', ')}`)
})

test('nothing is exempted that is actually registered', () => {
  // An exemption left behind after the file was fixed would quietly stop guarding it.
  const registry = readFileSync(new URL('all.ts', new URL('.', import.meta.url)), 'utf8')
  for (const name of Object.keys(NOT_REGISTERED)) {
    if (name === 'testRegistry.test.ts') continue
    assert.equal(registry.includes(`'./${name}'`), false, `${name} is registered; drop its exemption`)
  }
})
