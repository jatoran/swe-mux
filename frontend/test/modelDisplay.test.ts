import assert from 'node:assert/strict'
import test from 'node:test'
import { displayModelName, MODEL_DISPLAY_PREFIXES } from '../src/modelDisplay.ts'

test('Claude family model ids use compact display names',()=>{
  assert.equal(displayModelName('claude-opus-5'),'opus-5')
  assert.equal(displayModelName('claude-sonnet-4-6'),'sonnet-4-6')
  assert.equal(displayModelName('claude-haiku-4-5-20251001'),'haiku-4-5-20251001')
})

test('provider-qualified Claude ids use the same compact names',()=>{
  assert.equal(displayModelName('anthropic/claude-opus-4-8'),'opus-4-8')
  assert.equal(displayModelName('anthropic/claude-sonnet-4.6'),'sonnet-4.6')
})

test('other mapped provider families remove redundant namespaces',()=>{
  assert.equal(displayModelName('openai/gpt-5.6-sol'),'gpt-5.6-sol')
  assert.equal(displayModelName('openai/codex'),'codex')
  assert.equal(displayModelName('moonshotai/kimi-k2.6'),'kimi-k2.6')
})

test('unknown and already compact model ids are preserved exactly',()=>{
  assert.equal(displayModelName('opus-5'),'opus-5')
  assert.equal(displayModelName('gpt-5.6-sol'),'gpt-5.6-sol')
  assert.equal(displayModelName('vendor/model-x'),'vendor/model-x')
  assert.equal(displayModelName('claude-fable-5'),'claude-fable-5')
})

test('the declarative prefix mapping is externally inspectable and boundary-safe',()=>{
  assert.ok(MODEL_DISPLAY_PREFIXES.some(mapping=>mapping.raw==='claude-opus'&&mapping.display==='opus'))
  assert.equal(displayModelName('claude-opusplus-5'),'claude-opusplus-5')
})
