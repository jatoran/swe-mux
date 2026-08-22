import assert from 'node:assert/strict'
import test from 'node:test'
import {
  BRACKETED_PASTE_END,
  BRACKETED_PASTE_START,
  bracketedPaste,
  composerInsertion,
  composerInsertionParts,
} from '../src/composerInsertion.ts'
import { HARNESS_REGISTRY_SEED } from '../src/harnessRegistrySeed.ts'
import { installHarnessRegistry } from '../src/harnessRegistry.ts'
import { AGENT_NEWLINE } from '../src/terminalKeys.ts'

installHarnessRegistry(HARNESS_REGISTRY_SEED)

// The live "Tree" prompt template, which is what surfaced this. Its leading
// newline made a Codex pane submit whatever the operator had half-typed.
const TREE = '\nWork in a separate worktree. Do not land it and do not redeploy.'
const TREE_BODY = 'Work in a separate worktree. Do not land it and do not redeploy.'

test('a body is wrapped in bracketed paste with CR line breaks', () => {
  const payload = bracketedPaste('one\ntwo')
  assert.equal(payload, `${BRACKETED_PASTE_START}one\rtwo${BRACKETED_PASTE_END}`)
  assert.ok(!payload.includes('\n'))
})

test('CRLF and bare CR normalize to the same single break', () => {
  assert.equal(bracketedPaste('a\r\nb'), bracketedPaste('a\nb'))
  assert.equal(bracketedPaste('a\rb'), bracketedPaste('a\nb'))
})

test('codex lifts a leading newline out of the paste and into key presses', () => {
  const { leading, body } = composerInsertionParts(TREE, 'codex')
  assert.equal(leading, AGENT_NEWLINE)
  assert.equal(body, TREE_BODY)
  assert.equal(composerInsertion(TREE, 'codex'), `${AGENT_NEWLINE}${bracketedPaste(TREE_BODY)}`)
})

test('every leading newline is lifted, not just the first', () => {
  const { leading, body } = composerInsertionParts('\n\n\nx', 'codex')
  assert.equal(leading, AGENT_NEWLINE.repeat(3))
  assert.equal(body, 'x')
})

test('a leading CRLF run is lifted the same way a leading LF run is', () => {
  assert.deepEqual(
    composerInsertionParts('\r\n\r\nx', 'codex'),
    composerInsertionParts('\n\nx', 'codex'),
  )
})

test('interior newlines stay inside the paste, which is where codex reads them as content', () => {
  const { leading, body } = composerInsertionParts('a\nb\nc', 'codex')
  assert.equal(leading, '')
  assert.equal(body, 'a\nb\nc')
})

test('a harness not measured as having the defect is left on the bytes it always got', () => {
  for (const backend of ['claude', 'opencode', 'pi', 'omp', 'shell', undefined]) {
    const parts = composerInsertionParts(TREE, backend)
    assert.equal(parts.leading, '', `${backend} must not gain lifted keys`)
    assert.equal(parts.body, TREE)
    assert.equal(composerInsertion(TREE, backend), bracketedPaste(TREE))
  }
})

test('text that is nothing but newlines produces the keys and no empty paste', () => {
  assert.equal(composerInsertion('\n\n', 'codex'), AGENT_NEWLINE.repeat(2))
  assert.ok(!composerInsertion('\n\n', 'codex').includes(BRACKETED_PASTE_START))
})

test('an insertion never begins with a bare newline for any registered harness', () => {
  // The invariant the whole module exists to hold: the byte after nothing is
  // never CR or LF, because that byte is the one the paste wrapper cannot protect.
  for (const harness of HARNESS_REGISTRY_SEED.harnesses) {
    const payload = composerInsertion(TREE, harness.name)
    const first = payload.slice(0, BRACKETED_PASTE_START.length + 1)
    assert.ok(
      !first.startsWith('\r') && !first.startsWith('\n'),
      `${harness.name} insertion starts with a bare newline`,
    )
    if (harness.paste_leading_newline_submits) {
      assert.ok(
        !payload.startsWith(BRACKETED_PASTE_START),
        `${harness.name} must not open with the paste marker over a leading newline`,
      )
    }
  }
})
