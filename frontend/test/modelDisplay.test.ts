import assert from 'node:assert/strict'
import test from 'node:test'
import { displayModelName, MODEL_VENDOR_TOKENS } from '../src/modelDisplay.ts'

test('Claude family model ids use compact display names',()=>{
  assert.equal(displayModelName('claude-opus-5'),'opus-5')
  assert.equal(displayModelName('claude-sonnet-4-6'),'sonnet-4-6')
  assert.equal(displayModelName('claude-haiku-4-5-20251001'),'haiku-4-5-20251001')
})

test('a model family the label mapping never heard of is compacted anyway',()=>{
  // The defect this pins: a hand-maintained per-family table listed opus,
  // sonnet, and haiku, so one new model name put `claude-fable-5` in a list
  // where every sibling row had already dropped the brand.
  assert.equal(displayModelName('claude-fable-5'),'fable-5')
  assert.equal(displayModelName('anthropic/claude-nightingale-2'),'nightingale-2')
})

test('provider-qualified ids use the same compact names',()=>{
  assert.equal(displayModelName('anthropic/claude-opus-4-8'),'opus-4-8')
  assert.equal(displayModelName('anthropic/claude-sonnet-4.6'),'sonnet-4.6')
})

test('OpenAI ids drop the brand and keep the model',()=>{
  assert.equal(displayModelName('openai/gpt-5.6-sol'),'5.6-sol')
  assert.equal(displayModelName('gpt-5.6-sol'),'5.6-sol')
  assert.equal(displayModelName('gpt-5.1-codex-max'),'5.1-codex-max')
})

test('a family token is not branding and survives',()=>{
  assert.equal(displayModelName('openai/codex'),'codex')
  assert.equal(displayModelName('moonshotai/kimi-k2.6'),'kimi-k2.6')
  assert.equal(displayModelName('o3-mini'),'o3-mini')
})

test('an unknown vendor path is still not part of the model name',()=>{
  assert.equal(displayModelName('vendor/model-x'),'model-x')
})

test('already compact ids are preserved exactly',()=>{
  assert.equal(displayModelName('opus-5'),'opus-5')
  assert.equal(displayModelName('sonnet-4-6'),'sonnet-4-6')
})

test('the brand list is externally inspectable and boundary-safe',()=>{
  assert.ok(MODEL_VENDOR_TOKENS.includes('claude'))
  assert.ok(MODEL_VENDOR_TOKENS.includes('gpt'))
  // A brand token only matches a whole leading segment, never a word it starts.
  assert.equal(displayModelName('claudely-5'),'claudely-5')
  assert.equal(displayModelName('gptx-1'),'gptx-1')
  // And a bare brand is the only name the id has, so it survives intact.
  assert.equal(displayModelName('claude'),'claude')
  assert.equal(displayModelName('claude-'),'claude-')
})
