import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SESSION_TOPBAR_MAX_ROWS, addSessionTopbarRow, defaultSessionTopbarConfig,
  normalizeSessionTopbarConfig, placeSessionTopbarItem, removeSessionTopbarItem,
  removeSessionTopbarRow, sessionTopbarItemKey, unplacedSessionTopbarItems,
} from '../src/sessionTopbarConfig.ts'

test('the default is one row with title and the three existing agent controls',()=>{
  const config=defaultSessionTopbarConfig()
  assert.equal(config.rows.length,1)
  assert.deepEqual(config.rows[0].left.map(sessionTopbarItemKey),['metric:title','metric:cwd'])
  assert.deepEqual(config.rows[0].right.map(sessionTopbarItemKey),[
    'action:approvals','action:drawer:queue','action:drawer:transcript',
  ])
})

test('normalization keeps one title, unique items, and no more than three rows',()=>{
  const config=normalizeSessionTopbarConfig({rows:Array.from({length:5},()=>({
    left:[{kind:'metric',id:'model',mode:'always'}],right:[],separator:'bad',
  }))})
  assert.equal(config.rows.length,SESSION_TOPBAR_MAX_ROWS)
  assert.equal(config.rows.flatMap(row=>row.left).filter(item=>sessionTopbarItemKey(item)==='metric:model').length,1)
  assert.equal(config.rows.flatMap(row=>row.left).filter(item=>sessionTopbarItemKey(item)==='metric:title').length,1)
  assert.equal(config.rows[0].separator,'dot')
})

test('placing moves an item across rows and alignments rather than duplicating it',()=>{
  let config=addSessionTopbarRow(defaultSessionTopbarConfig())
  const item={kind:'action' as const,id:'drawer:queue' as const}
  config=placeSessionTopbarItem(config,item,1,'left')
  assert.deepEqual(config.rows[0].right.map(sessionTopbarItemKey),['action:approvals','action:drawer:transcript'])
  assert.deepEqual(config.rows[1].left.map(sessionTopbarItemKey),['action:drawer:queue'])
})

test('title cannot be removed and removing a row rehomes its contents',()=>{
  let config=addSessionTopbarRow(defaultSessionTopbarConfig())
  const title=config.rows[0].left[0]
  assert.deepEqual(removeSessionTopbarItem(config,title),config)
  config=placeSessionTopbarItem(config,{kind:'metric',id:'model',mode:'always'},1,'right')
  config=removeSessionTopbarRow(config,1)
  assert.equal(config.rows.length,1)
  assert.ok(config.rows[0].right.some(item=>sessionTopbarItemKey(item)==='metric:model'))
})

test('every catalog item is reachable once and row creation stops at three',()=>{
  let config=defaultSessionTopbarConfig()
  assert.ok(unplacedSessionTopbarItems(config).some(item=>item.key==='action:drawer:processes'))
  for(let index=0;index<8;index++)config=addSessionTopbarRow(config)
  assert.equal(config.rows.length,SESSION_TOPBAR_MAX_ROWS)
})
