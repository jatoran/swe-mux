import assert from 'node:assert/strict'
import test from 'node:test'
import { mobileImeDelta, TERMINAL_DELETE } from '../src/mobileTerminalIme.ts'

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
})
