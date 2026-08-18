import assert from 'node:assert/strict'
import test from 'node:test'

import type { Command } from '../src/commands.ts'
import { planUiCommand } from '../src/uiCommand.ts'

const command = (id: string, label: string, phrases: string[], available = true): Command => ({
  id, label, category: 'voice', available, run: () => {}, voice: { phrases },
})

const REGISTRY: Command[] = [
  command('project.focus:1', 'Focus project workout-plan', ['go to workout plan']),
  command('drawer.show:notes', 'Open Notes tab', ['open notes', 'notes']),
  command('voice.fleetStatus', 'Speak fleet status', ['fleet status', 'status report']),
  // The deterministic-lookup catch-all: matches any text via its bare slot.
  command('voice.query', 'Ask a deterministic voice lookup', ['{text}']),
]

test('a registry alias resolves as a command, never as the catch-all', () => {
  const plan = planUiCommand(REGISTRY, 'open notes')
  assert.equal(plan.kind, 'command')
  assert.equal(plan.kind === 'command' && plan.command.id, 'drawer.show:notes')
})

test('navigation phrasing lands on the closed query grammar', () => {
  const plan = planUiCommand(REGISTRY, 'open project workout plan')
  assert.equal(plan.kind, 'query')
  assert.equal(plan.kind === 'query' && plan.query.kind, 'open')
})

test('the {text} catch-all can never swallow a dispatched command', () => {
  // "move to cmr capture manager project" reproduced live as the voice lookup
  // running instead of a project focus; the plan must be the query grammar's
  // open (which resolves the project reference) or an honest miss — never the
  // catch-all command.
  const plan = planUiCommand(REGISTRY, 'move to cmr capture manager project')
  assert.notEqual(plan.kind === 'command' && plan.command.id, 'voice.query')
})

test('a fuzzy near-miss still lands, and an unmatched phrase reports candidates', () => {
  const fuzzy = planUiCommand(REGISTRY, 'fleet stats')
  assert.equal(fuzzy.kind === 'command' && fuzzy.command.id, 'voice.fleetStatus')
  const miss = planUiCommand(REGISTRY, 'defragment the chronotron')
  assert.equal(miss.kind, 'none')
  assert.ok(miss.kind === 'none' && Array.isArray(miss.candidates))
})

test('an exact label match works when no alias exists', () => {
  const registry = [...REGISTRY, command('special.thing', 'Toggle the special thing', [])]
  const plan = planUiCommand(registry, 'Toggle the special thing')
  assert.equal(plan.kind === 'command' && plan.command.id, 'special.thing')
})
