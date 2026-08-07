import assert from 'node:assert/strict'
import test from 'node:test'
import { filterModelOptions, includeSelectedModel } from '../src/modelFilter.ts'

const models=[
  {id:'openai/gpt-5.6-luna',name:'GPT-5.6 Luna'},
  {id:'openai/gpt-5.6-sol',name:'GPT-5.6 Sol'},
  {id:'anthropic/claude-sonnet-4.6',name:'Claude Sonnet 4.6'},
]

test('model filtering searches ids and names live with strongest matches first',()=>{
  assert.deepEqual(
    filterModelOptions(models,'luna').map(model=>model.id),
    ['openai/gpt-5.6-luna'],
  )
  assert.deepEqual(
    filterModelOptions(models,'openai/gpt-5.6-sol').map(model=>model.id),
    ['openai/gpt-5.6-sol'],
  )
  assert.deepEqual(
    filterModelOptions(models,'Claude Sonnet').map(model=>model.id),
    ['anthropic/claude-sonnet-4.6'],
  )
})

test('model filtering is bounded and preserves catalog order for an empty query',()=>{
  assert.deepEqual(filterModelOptions(models,'',2),models.slice(0,2))
})

test('a configured model remains selectable while the catalog is stale',()=>{
  const result=includeSelectedModel(models,'vendor/model-no-longer-cached')
  assert.equal(result[0].id,'vendor/model-no-longer-cached')
  assert.equal(result[0].name,'Configured model (catalog unavailable)')
  assert.equal(includeSelectedModel(models,models[0].id).length,models.length)
})
