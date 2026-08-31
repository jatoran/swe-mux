import assert from 'node:assert/strict'
import test from 'node:test'
import { commandCategoryLabel, shortcutMatches, UNBOUND_CHORD } from '../src/commands.ts'

const command = (id: string, label: string, category: string) => ({ id, label, category })
const palette = command('palette.open', 'Open command palette', 'view')

test('a category reads as a heading rather than as the id it is stored as', () => {
  // Every category in both catalogs is one lowercase word. The daemon's list and the
  // frontend union do not agree on the set, which is why an unknown one still renders.
  for (const category of ['view', 'session', 'pane', 'project', 'terminal', 'input', 'clipboard', 'voice', 'notes'])
    assert.match(commandCategoryLabel(category), /^[A-Z][a-z]+$/, category)
  assert.equal(commandCategoryLabel('view'), 'View')
  assert.equal(commandCategoryLabel('unheard-of'), 'Unheard-of')
  assert.equal(commandCategoryLabel(''), '')
})

test('an empty filter keeps every row', () => {
  assert.equal(shortcutMatches(palette, 'ctrl+k', ''), true)
  assert.equal(shortcutMatches(palette, undefined, '   '), true)
})

test('a row matches on what it shows: label, id, and category', () => {
  assert.equal(shortcutMatches(palette, undefined, 'palette'), true)
  assert.equal(shortcutMatches(palette, undefined, 'palette.open'), true)
  assert.equal(shortcutMatches(palette, undefined, 'view'), true)
  assert.equal(shortcutMatches(palette, undefined, 'scrollback'), false)
})

test('a chord matches in both spellings, so neither typing habit misses it', () => {
  // Stored form is what a person reads off a config; displayed form is what the row
  // shows. Matching only one of them makes "what owns this chord" a trick question.
  assert.equal(shortcutMatches(palette, 'ctrl+shift+p', 'ctrl+shift'), true)
  assert.equal(shortcutMatches(palette, 'ctrl+shift+p', 'ctrl shift'), true)
  assert.equal(shortcutMatches(palette, 'ctrl+shift+p', 'shift p'), true)
  assert.equal(shortcutMatches(palette, 'ctrl+shift+p', 'alt'), false)
})

test('an unbound row answers to what it reads as', () => {
  assert.equal(shortcutMatches(palette, undefined, UNBOUND_CHORD), true)
  assert.equal(shortcutMatches(palette, 'ctrl+k', UNBOUND_CHORD), false)
})

test('every term must match, so extra words narrow rather than widen', () => {
  const kill = command('session.kill', 'Confirm-kill focused session', 'session')
  assert.equal(shortcutMatches(kill, undefined, 'session kill'), true)
  assert.equal(shortcutMatches(kill, undefined, 'session palette'), false)
})

test('filtering is case-insensitive in both directions', () => {
  assert.equal(shortcutMatches(palette, 'CTRL+K', 'ctrl'), true)
  assert.equal(shortcutMatches(palette, 'ctrl+k', 'OPEN COMMAND'), true)
})
