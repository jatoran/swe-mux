import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { TUTORIAL_CHROME_CLAIMS } from '../src/tutorial.ts'
import { settingsTabs, tabForSection } from '../src/settingsTabs.ts'

/**
 * The guided tour describes only chrome that exists.
 *
 * This is the Phase 16 "stale guidance" item, written as a mechanism rather than as a
 * correction. The tour told every new user for months that the app menu had a `Utilities`
 * group holding the viewers, long after that group was unfolded into eight plain rows -
 * and nothing failed, because the claim was a string inside a JSX body and no test in the
 * suite reads those. `ui.md` was wrong about the side-panel tab count in four places for
 * the same reason.
 *
 * The shape is `settingTargets.test.ts`'s: read the source that makes the promise, read the
 * source that must keep it, and fail when they disagree. Three guards, and the split
 * between them is deliberate.
 *
 *  - **Anchors are derived, not declared.** Every `[data-tutorial="…"]` in the step list is
 *    read straight out of the tour and checked against the components, so a spotlight on a
 *    mark nobody renders fails with nothing to maintain.
 *  - **Named chrome is declared**, because there is no way to derive "this sentence names a
 *    menu row" from prose. `TUTORIAL_CHROME_CLAIMS` is that declaration, and it is checked
 *    in *both* directions so it cannot rot into a list of names the copy stopped using.
 *  - **Settings paths are closed**: every `Settings →` the copy contains must be declared,
 *    which is what stops a fourth one being added and never checked.
 */

const src = join(import.meta.dirname, '..', 'src')
const read = (name: string) => readFileSync(join(src, name), 'utf8')

const tour = read('GuidedTutorial.tsx')
const app = read('App.tsx')

/** Every `src` file except the tour itself: the components that must carry its anchors. */
const componentSources = readdirSync(src)
  .filter(name => (name.endsWith('.tsx') || name.endsWith('.ts')) && name !== 'GuidedTutorial.tsx')
  .map(name => read(name))
  .join('\n')

test('every anchor the tour spotlights is rendered by some component', () => {
  const marks = [...new Set([...tour.matchAll(/\[data-tutorial="([^"]+)"\]/g)].map(match => match[1]))]
  // Vacuity guard: an empty match set would make every assertion below pass.
  assert.ok(marks.length >= 8, `expected the tour to spotlight several anchors, found ${marks.length}`)
  for (const mark of marks) {
    // Two spellings, because a mark on a component's own element is an attribute while a
    // mark passed through a wrapper's props (the tab strip's `stripProps`) is an object
    // key. Both put the same attribute in the DOM.
    const rendered = componentSources.includes(`data-tutorial="${mark}"`)
      || componentSources.includes(`'data-tutorial':'${mark}'`)
      || componentSources.includes(`data-tutorial': '${mark}'`)
      // The conditional form, where one element carries the mark only for one item:
      // `data-tutorial={tab.id==='notes'?'project-notes':undefined}`.
      || new RegExp(`data-tutorial=\\{[^}]*'${mark}'`).test(componentSources)
    assert.ok(rendered, `the tour spotlights [data-tutorial="${mark}"], which no component renders`)
  }
})

test('every class selector the tour spotlights exists in a component', () => {
  const classes = [...new Set([...tour.matchAll(/selectors:\[[^\]]*'\.([a-z][a-z0-9-]*)'/g)].map(match => match[1]))]
  assert.ok(classes.length >= 1, 'expected the tour to spotlight at least one control by class')
  for (const name of classes) {
    assert.ok(componentSources.includes(name), `the tour spotlights .${name}, which no component renders`)
  }
})

test('every Settings path the tour names resolves to a real tab', () => {
  for (const path of TUTORIAL_CHROME_CLAIMS.settingsPaths) {
    const section = path.replace(/^Settings\s*→\s*/, '')
    const tab = tabForSection(section)
    assert.ok(settingsTabs.some(entry => entry.id === tab), `${path} resolves to no tab`)
    // `tabForSection` answers General for anything it does not recognise, so a path that
    // lands there without asking for General has stopped resolving.
    if (section.toLowerCase() !== 'general') {
      assert.notEqual(tab, 'general', `${path} names a Settings tab that no longer exists`)
    }
  }
})

test('the declared Settings paths are all of them, and each is really in the copy', () => {
  for (const path of TUTORIAL_CHROME_CLAIMS.settingsPaths) {
    assert.ok(tour.includes(path), `${path} is declared but the tour no longer says it`)
  }
  // Closed in the other direction: a fourth path added to the copy and not declared here
  // would otherwise be checked by nothing at all.
  const mentioned = (tour.match(/Settings\s*→/g) || []).length
  const declared = TUTORIAL_CHROME_CLAIMS.settingsPaths
    .reduce((total, path) => total + (tour.split(path).length - 1), 0)
  assert.equal(mentioned, declared, 'the tour names a Settings path that TUTORIAL_CHROME_CLAIMS does not declare')
})

test('every app-menu row the tour names is a row the app menu renders', () => {
  for (const label of TUTORIAL_CHROME_CLAIMS.menuRows) {
    // JSX escapes the ampersand, so compare against what the source really contains.
    const rendered = label.replace(/&/g, '&amp;')
    assert.ok(
      app.includes(`class="menu-row-label">${rendered}`),
      `the tour names the menu row "${label}", which the app menu does not render`,
    )
    assert.ok(
      tour.includes(`<strong>${rendered}</strong>`),
      `"${label}" is declared as a menu row the tour names, but the tour does not name it`,
    )
  }
})

test('every app-menu group the tour names still exists', () => {
  const groups = [...app.matchAll(/<MenuGroup[^>]*label="([^"]+)"/g)].map(match => match[1])
  assert.ok(groups.length >= 1, 'expected the app menu to have at least one collapsible group')
  for (const label of TUTORIAL_CHROME_CLAIMS.menuGroups) {
    assert.ok(groups.includes(label), `the tour names the menu group "${label}", which no longer exists`)
    assert.ok(tour.includes(`<strong>${label}</strong>`), `"${label}" is declared but the tour does not name it`)
  }
})

test('the retired Utilities menu group is named by neither the menu nor the tour', () => {
  // The specific regression this whole file was written for. Kept as its own case because
  // the guards above would only catch it while `Utilities` was still *declared*, and the
  // failure mode was a name nobody had thought about for months.
  const groups = [...app.matchAll(/<MenuGroup[^>]*label="([^"]+)"/g)].map(match => match[1])
  assert.ok(!groups.includes('Utilities'))
  assert.ok(!/<strong>Utilities<\/strong>/.test(tour), 'the tour still describes the removed Utilities menu group')
})
