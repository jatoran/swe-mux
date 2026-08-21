import assert from 'node:assert/strict'
import test from 'node:test'
import {
  formatContextLength, formatModelPricing, modelMetaLabel, modelMetaTitle, perMillionTokens,
} from '../src/modelPricing.ts'

test('a per-token price is stated per million tokens', () => {
  // OpenRouter quotes USD per token, which is the whole reason this conversion is
  // not left to the call site: 0.00000008 is not a figure anyone compares models on.
  assert.equal(perMillionTokens(0.00000008), '$0.08')
  assert.equal(perMillionTokens(0.0000003), '$0.30')
  assert.equal(perMillionTokens(0.000015), '$15.00')
  assert.equal(perMillionTokens(0.000000015), '$0.015')
})

test('unknown and free are not the same answer and never render as each other', () => {
  assert.equal(perMillionTokens(null), null)
  assert.equal(perMillionTokens(undefined), null)
  assert.equal(perMillionTokens(Number.NaN), null)
  assert.equal(perMillionTokens(0), '$0.00')
  assert.equal(formatModelPricing({}), null)
  assert.equal(formatModelPricing({prompt_price: 0, completion_price: 0}), 'free')
})

test('a price decided at request time says so instead of showing a credit', () => {
  assert.equal(perMillionTokens(-1), 'variable')
  assert.equal(
    formatModelPricing({prompt_price: -1, completion_price: -1}),
    'variable / variable per M',
  )
})

test('a price too small to print does not round down into looking free', () => {
  assert.equal(perMillionTokens(0.0000000001), '<$0.001')
  assert.equal(perMillionTokens(0.0000000012), '$0.001')
})

test('a half-known pair keeps the half that answers the question', () => {
  assert.equal(formatModelPricing({prompt_price: 0.00000008}), '$0.08 / ? per M')
  assert.equal(formatModelPricing({completion_price: 0.0000003}), '? / $0.30 per M')
})

test('free on one side only stays a pair, because it is not a free model', () => {
  assert.equal(
    formatModelPricing({prompt_price: 0, completion_price: 0.0000003}),
    '$0.00 / $0.30 per M',
  )
})

test('context length reads the way a context window is quoted', () => {
  assert.equal(formatContextLength(128_000), '128K')
  assert.equal(formatContextLength(131_072), '131K')
  assert.equal(formatContextLength(1_048_576), '1M')
  assert.equal(formatContextLength(2_000_000), '2M')
  assert.equal(formatContextLength(512), '512')
  assert.equal(formatContextLength(0), null)
  assert.equal(formatContextLength(null), null)
})

test('the row cell states capacity then price, and omits what it does not know', () => {
  assert.equal(
    modelMetaLabel({context_length: 128_000, prompt_price: 0.00000008, completion_price: 0.0000003}),
    '128K · $0.08 / $0.30 per M',
  )
  assert.equal(modelMetaLabel({context_length: 128_000}), '128K')
  assert.equal(modelMetaLabel({prompt_price: 0.00000008, completion_price: 0.0000003}), '$0.08 / $0.30 per M')
  // A catalog entry with neither renders nothing rather than an empty separator.
  assert.equal(modelMetaLabel({}), null)
})

test('the tooltip names which figure is which, because the row cannot', () => {
  assert.equal(
    modelMetaTitle({context_length: 128_000, prompt_price: 0.00000008, completion_price: 0.0000003}),
    'Input $0.08 per million tokens · Output $0.30 per million tokens · 128K context',
  )
  assert.equal(modelMetaTitle({}), null)
})
