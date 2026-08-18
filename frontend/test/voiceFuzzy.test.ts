import assert from 'node:assert/strict'
import test from 'node:test'

import type { Command } from '../src/commands.ts'
import { editDistance, phraseSimilarity, resolveVoiceFuzzy } from '../src/voiceFuzzy.ts'

const command = (id: string, label: string, phrases: string[], available = true): Command => ({
  id, label, category: 'voice', available, run: () => {}, voice: { phrases },
})

const REGISTRY: Command[] = [
  command('project.focus:2', 'Focus pixel lab', ['pixel lab', 'go to pixel lab']),
  command('voice.fleetStatus', 'Speak fleet status', ['fleet status', 'status report']),
  command('voice.standby', 'Standby', ['standby', 'stand by']),
  command('voice.query', 'Ask a lookup', ['{text}']),
  command('drawer.git', 'Open Git tab', ['open git'], false),
]

test('edit distance counts substitutions, gaps, and transpositions', () => {
  assert.equal(editDistance('send', 'send'), 0)
  assert.equal(editDistance('send', 'sned'), 1) // transposition
  assert.equal(editDistance('lab', 'lap'), 1)
  assert.equal(editDistance('mux', 'mucks'), 3)
})

test('positional token alignment keeps word order significant', () => {
  assert.ok(phraseSimilarity('pixel lap', 'pixel lab') > 0.8)
  assert.ok(phraseSimilarity('lab pixel', 'pixel lab') < 0.5)
  assert.equal(phraseSimilarity('go to pixel lab now please', 'pixel lab'), 0)
})

test('one misheard syllable still lands on the right command', () => {
  assert.equal(resolveVoiceFuzzy(REGISTRY, 'pixel lap')?.command.id, 'project.focus:2')
  assert.equal(resolveVoiceFuzzy(REGISTRY, 'fleet stats')?.command.id, 'voice.fleetStatus')
})

test('exact matches are tier 1 and never fuzzy-fire here', () => {
  assert.equal(resolveVoiceFuzzy(REGISTRY, 'pixel lab'), null)
})

test('an utterance equidistant between two commands falls through', () => {
  const registry = [
    ...REGISTRY,
    command('project.focus:3', 'Focus mixel lab', ['mixel lab']),
  ]
  // "nixel lab" is one edit from both "pixel lab" and "mixel lab": guessing
  // between two different commands is worse than handing it to the assistant.
  assert.equal(resolveVoiceFuzzy(registry, 'nixel lab'), null)
})

test('weak, long, unavailable, and slot phrases all fall through', () => {
  assert.equal(resolveVoiceFuzzy(REGISTRY, 'give me a rundown of everything in flight right now please'), null)
  assert.equal(resolveVoiceFuzzy(REGISTRY, 'completely unrelated words'), null)
  assert.equal(resolveVoiceFuzzy(REGISTRY, 'open get'), null) // disabled command
})
