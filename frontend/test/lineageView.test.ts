import assert from 'node:assert/strict'
import test from 'node:test'

import {
  lineageCounterpart,
  lineageCutLabel,
  lineageDirection,
  lineageEndpointLabel,
  lineageVerb,
  orderedLineage,
  type LineageEdge,
} from '../src/lineageView.ts'

const HERE = 'run-here'

const edge = (overrides: Partial<LineageEdge> = {}): LineageEdge => ({
  id: 'edge-1',
  parent_run_id: 'run-parent',
  child_run_id: HERE,
  relation: 'branch',
  created_at: 1_000,
  parent: { name: 'Update ABC', live: false, known: true },
  child: { name: 'B1-Update ABC', live: true, known: true },
  ...overrides,
})

test('an edge is read in the direction the reader is standing', () => {
  // The section sits on one conversation, so "A → B" is the one thing it must not say.
  assert.equal(lineageDirection(edge(), HERE), 'from')
  assert.equal(lineageDirection(edge({ parent_run_id: HERE, child_run_id: 'run-other' }), HERE), 'to')
  assert.equal(lineageVerb('branch', 'from'), 'Branched from')
  assert.equal(lineageVerb('branch', 'to'), 'Branched into')
  assert.equal(lineageVerb('resume', 'from'), 'Resumed from')
  assert.equal(lineageVerb('review', 'to'), 'Reviewed by')
})

test('a relation this build has never heard of still reads as itself', () => {
  // The set is the daemon's and can grow; a missing entry must not blank the row.
  assert.equal(lineageVerb('rebased', 'from'), 'rebased from')
  assert.equal(lineageVerb('rebased', 'to'), 'rebased to')
})

test('the counterpart is the end the reader is not on', () => {
  const from = lineageCounterpart(edge(), HERE)
  assert.equal(from.runId, 'run-parent')
  assert.equal(from.endpoint.name, 'Update ABC')
  const to = lineageCounterpart(edge({ parent_run_id: HERE, child_run_id: 'run-other' }), HERE)
  assert.equal(to.runId, 'run-other')
})

test('a deleted conversation is named as removed, not left blank', () => {
  // The edge still records that the fork happened. Dropping it would silently reshape
  // the lineage; an empty name would read as a rendering bug.
  const gone = { name: '', live: false, known: false }
  assert.equal(lineageEndpointLabel(gone, 'run-x'), 'conversation removed')
  // A row that exists but somehow carries no name falls back to its id rather than
  // rendering an empty button.
  assert.equal(lineageEndpointLabel({ name: '', live: false, known: true }, 'run-x'), 'run-x')
  assert.equal(lineageEndpointLabel({ name: 'Update ABC', live: true, known: true }, 'run-x'), 'Update ABC')
})

test('a branch says where it was cut, in the words of the turn', () => {
  assert.equal(
    lineageCutLabel({ mode: 'before', from_message_role: 'user', from_message_text: 'second prompt' }),
    'before your message “second prompt”',
  )
  assert.equal(
    lineageCutLabel({ mode: 'after', from_message_role: 'assistant', from_message_text: 'first answer' }),
    'after the reply “first answer”',
  )
})

test('a branch mux did not cut claims no position', () => {
  // Branches made before the transcript-fork rewrite were the CLI's: mux never saw a
  // message id, so the honest rendering is the relation alone.
  assert.equal(lineageCutLabel(undefined), '')
  assert.equal(lineageCutLabel({}), '')
  assert.equal(lineageCutLabel({ backend: 'claude', strategy: 'resume_child_thread' }), '')
  // A cut whose excerpt was lost still says which side of the turn it was on.
  assert.equal(lineageCutLabel({ mode: 'before', from_message_role: 'user' }), 'before your message')
})

test('the most recent relative is listed first', () => {
  const ordered = orderedLineage([
    edge({ id: 'old', created_at: 1 }),
    edge({ id: 'new', created_at: 3 }),
    edge({ id: 'mid', created_at: 2 }),
  ])
  assert.deepEqual(ordered.map(item => item.id), ['new', 'mid', 'old'])
})
