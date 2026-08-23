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

test('nothing is exempted that is actually registered', () => {
  // An exemption left behind after the file was fixed would quietly stop guarding it.
  const registry = readFileSync(new URL('all.ts', new URL('.', import.meta.url)), 'utf8')
  for (const name of Object.keys(NOT_REGISTERED)) {
    if (name === 'testRegistry.test.ts') continue
    assert.equal(registry.includes(`'./${name}'`), false, `${name} is registered; drop its exemption`)
  }
})
