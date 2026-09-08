import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultSessionRowConfig } from '../src/sessionRowConfig.ts'
import {
  SESSION_TOPBAR_MAX_ROWS, SESSION_TOPBAR_VERSION, addSessionTopbarRow, defaultSessionTopbarConfig,
  normalizeSessionTopbarConfig, placeSessionTopbarItem, removeSessionTopbarItem,
  removeSessionTopbarRow, sessionTopbarContextRender, sessionTopbarHasTitle, sessionTopbarItemKey,
  sessionTopbarMetricHasStyle, sessionTopbarMetricStyle, sessionTopbarRowConfig,
  setSessionTopbarMetricStyle, unplacedSessionTopbarItems, type SessionTopbarMetricItem,
} from '../src/sessionTopbarConfig.ts'

test('the default is one row with title and the three existing agent controls',()=>{
  const config=defaultSessionTopbarConfig()
  assert.equal(config.rows.length,1)
  assert.deepEqual(config.rows[0].left.map(sessionTopbarItemKey),['metric:title','metric:cwd'])
  assert.deepEqual(config.rows[0].right.map(sessionTopbarItemKey),[
    'action:approvals','action:drawer:queue','action:drawer:transcript',
  ])
})

test('normalization keeps unique items and no more than three rows, and repairs a version-1 layout\'s title',()=>{
  // No `version`: a layout from before the title could be removed, whose missing title
  // can only be a malformed blob and is put back.
  const config=normalizeSessionTopbarConfig({rows:Array.from({length:5},()=>({
    left:[{kind:'metric',id:'model',mode:'always'}],right:[],separator:'bad',
  }))})
  assert.equal(config.rows.length,SESSION_TOPBAR_MAX_ROWS)
  assert.equal(config.rows.flatMap(row=>row.left).filter(item=>sessionTopbarItemKey(item)==='metric:model').length,1)
  assert.equal(config.rows.flatMap(row=>row.left).filter(item=>sessionTopbarItemKey(item)==='metric:title').length,1)
  assert.equal(config.rows[0].separator,'dot')
  assert.equal(config.version,SESSION_TOPBAR_VERSION,'every write stamps the current version')
  const explicitV1=normalizeSessionTopbarConfig({version:1,rows:[{left:[{kind:'metric',id:'model',mode:'always'}],right:[],separator:'dot'}]})
  assert.ok(sessionTopbarHasTitle(explicitV1))
})

test('a layout written since the title became removable keeps its choice',()=>{
  // The whole reason for the version stamp: without it every load put the title back,
  // and the editor's remove control could never take effect.
  let config=defaultSessionTopbarConfig()
  const title=config.rows[0].left[0]
  assert.deepEqual(title,{kind:'metric',id:'title',mode:'always'})
  config=removeSessionTopbarItem(config,title)
  assert.ok(!sessionTopbarHasTitle(config))
  assert.deepEqual(config.rows[0].left.map(sessionTopbarItemKey),['metric:cwd'])
  const reloaded=normalizeSessionTopbarConfig(JSON.parse(JSON.stringify(config)))
  assert.ok(!sessionTopbarHasTitle(reloaded),'the round trip through storage does not put it back')
  // And it is offered again, so the removal is reversible from the same editor.
  assert.ok(unplacedSessionTopbarItems(reloaded).some(item=>item.key==='metric:title'))
  const restored=placeSessionTopbarItem(reloaded,{kind:'metric',id:'title',mode:'always'},0,'left')
  assert.ok(sessionTopbarHasTitle(restored))
  // A bar with everything removed still normalizes to one empty row rather than to the default.
  let bare=defaultSessionTopbarConfig()
  for(const item of [...bare.rows[0].left,...bare.rows[0].right])bare=removeSessionTopbarItem(bare,item)
  assert.deepEqual(bare.rows,[{left:[],right:[],separator:'dot'}])
})

test('placing moves an item across rows and alignments rather than duplicating it',()=>{
  let config=addSessionTopbarRow(defaultSessionTopbarConfig())
  const item={kind:'action' as const,id:'drawer:queue' as const}
  config=placeSessionTopbarItem(config,item,1,'left')
  assert.deepEqual(config.rows[0].right.map(sessionTopbarItemKey),['action:approvals','action:drawer:transcript'])
  assert.deepEqual(config.rows[1].left.map(sessionTopbarItemKey),['action:drawer:queue'])
})

test('removing a row rehomes its contents',()=>{
  let config=addSessionTopbarRow(defaultSessionTopbarConfig())
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

test('a placed context metric always resolves to an in-row rendering',()=>{
  // The failure this exists for: the sidebar draws context on its indicator by
  // default, the top bar has no indicator, and a placed `context` inherited
  // `arc` and drew nothing at all.
  const item:SessionTopbarMetricItem={kind:'metric',id:'context',mode:'always'}
  const arc=defaultSessionRowConfig()
  assert.equal(arc.context,'arc')
  assert.equal(sessionTopbarContextRender(item,arc),'percent')
  assert.equal(sessionTopbarRowConfig(item,arc).context,'percent')
  assert.equal(sessionTopbarRowConfig(item,{...arc,context:'off'}).context,'percent')
  // A sidebar already drawing in the row is inherited; an explicit style wins.
  assert.equal(sessionTopbarContextRender(item,{...arc,context:'gauge'}),'gauge')
  assert.equal(sessionTopbarContextRender({...item,style:'both'},{...arc,context:'gauge'}),'both')
  // Every other field renders under the sidebar's configuration untouched, and
  // so does context when the sidebar already draws it in the row.
  assert.equal(sessionTopbarRowConfig({kind:'metric',id:'model',mode:'always'},arc),arc)
  const percent={...arc,context:'percent' as const}
  assert.equal(sessionTopbarRowConfig(item,percent),percent)
})

test('the context style is stored per item, survives normalization, and is refused elsewhere',()=>{
  let config=placeSessionTopbarItem(defaultSessionTopbarConfig(),{kind:'metric',id:'context',mode:'always'},0,'right')
  config=setSessionTopbarMetricStyle(config,'context','both')
  const placed=()=>config.rows[0].right.find(item=>item.kind==='metric'&&item.id==='context') as SessionTopbarMetricItem
  assert.equal(placed().style,'both')
  config=normalizeSessionTopbarConfig(JSON.parse(JSON.stringify(config)))
  assert.equal(placed().style,'both')
  // A style this build does not draw is dropped, and so is one on a field
  // that has no rendering choice.
  const stale=normalizeSessionTopbarConfig({rows:[{left:[
    {kind:'metric',id:'context',mode:'always',style:'sparkline'},
    {kind:'metric',id:'model',mode:'always',style:'both'},
  ],right:[],separator:'dot'}]})
  const [,contextItem,modelItem]=stale.rows[0].left as SessionTopbarMetricItem[]
  assert.equal(contextItem.style,undefined)
  assert.equal(modelItem.style,undefined)
  assert.ok(sessionTopbarMetricHasStyle('context'))
  assert.ok(!sessionTopbarMetricHasStyle('model'))
  assert.equal(setSessionTopbarMetricStyle(config,'model','gauge'),config)
  // A style from the other styled field's vocabulary is refused too.
  assert.equal(setSessionTopbarMetricStyle(config,'context','full'),config)
})

test('a placed working directory follows the sidebar unless the item chose its own spelling',()=>{
  const rowConfig=defaultSessionRowConfig()
  const item:SessionTopbarMetricItem={kind:'metric',id:'cwd',mode:'always'}
  assert.ok(sessionTopbarMetricHasStyle('cwd'))
  assert.equal(sessionTopbarMetricStyle(item,rowConfig),'leaf')
  assert.equal(sessionTopbarRowConfig(item,rowConfig),rowConfig,'no override, no copy')
  assert.equal(sessionTopbarMetricStyle(item,{...rowConfig,cwdStyle:'relative'}),'relative')
  let config=defaultSessionTopbarConfig()
  config=setSessionTopbarMetricStyle(config,'cwd','full')
  const placed=config.rows[0].left.find(entry=>entry.kind==='metric'&&entry.id==='cwd') as SessionTopbarMetricItem
  assert.equal(placed.style,'full')
  assert.equal(sessionTopbarRowConfig(placed,rowConfig).cwdStyle,'full')
  assert.equal(sessionTopbarMetricStyle(placed,rowConfig),'full')
  // Round trip through storage keeps it; a context spelling on cwd does not survive.
  assert.equal((normalizeSessionTopbarConfig(JSON.parse(JSON.stringify(config))).rows[0].left[1] as SessionTopbarMetricItem).style,'full')
  const crossed=normalizeSessionTopbarConfig({rows:[{left:[{kind:'metric',id:'cwd',mode:'always',style:'gauge'}],right:[],separator:'dot'}]})
  assert.equal((crossed.rows[0].left[1] as SessionTopbarMetricItem).style,undefined)
})
