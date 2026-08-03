import assert from 'node:assert/strict'
import test from 'node:test'
import { AGENT_NEWLINE } from '../src/terminalKeys.ts'
import { mobileEnterNeedsPinnedSend, mobileEnterPayload, mobileImeDelta, TERMINAL_DELETE } from '../src/mobileTerminalIme.ts'

test('mobile IME composition streams only the newly appended text',()=>{
  assert.equal(mobileImeDelta('','h'),'h')
  assert.equal(mobileImeDelta('h','he'),'e')
  assert.equal(mobileImeDelta('hello','hello '),' ')
})

test('mobile IME replacements rewind the composing suffix before rewriting it',()=>{
  assert.equal(mobileImeDelta('teh','the'),TERMINAL_DELETE.repeat(2)+'he')
  assert.equal(mobileImeDelta('hello','hell'),TERMINAL_DELETE)
  assert.equal(mobileImeDelta('word',''),TERMINAL_DELETE.repeat(4))
})

test('mobile IME deltas count Unicode characters and normalize Enter',()=>{
  assert.equal(mobileImeDelta('👍','👍a'),'a')
  assert.equal(mobileImeDelta('👍',''),TERMINAL_DELETE)
  assert.equal(mobileImeDelta('run','run\n'),'\r')
  assert.equal(mobileImeDelta('run','run\n',AGENT_NEWLINE),AGENT_NEWLINE)
  assert.equal(mobileImeDelta('run','run\r\n',AGENT_NEWLINE),AGENT_NEWLINE)
})

test('mobile Enter inserts newlines in agent composers and submits shells',()=>{
  assert.equal(mobileEnterPayload('claude'),AGENT_NEWLINE)
  assert.equal(mobileEnterPayload('codex'),AGENT_NEWLINE)
  assert.equal(mobileEnterPayload('shell'),'\r')
  assert.equal(mobileEnterNeedsPinnedSend('claude'),true)
  assert.equal(mobileEnterNeedsPinnedSend('codex'),true)
  assert.equal(mobileEnterNeedsPinnedSend('shell'),false)
})
