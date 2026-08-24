import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  commitsSummary, countLabel, defaultHistorySections, historyKeyStats, HISTORY_SECTION_KEYS,
  HISTORY_TIMELINE_PREVIEW, sectionKeysVisible, speakerLabel, splitRecentScans,
} from '../src/historyDetail.ts'

const source=readFileSync(join(import.meta.dirname,'..','src','HistoryBrowser.tsx'),'utf8')
const css=readFileSync(join(import.meta.dirname,'..','src','style.css'),'utf8')

test('history detail actions stay ahead of the collapsible run sections',()=>{
  const actions=source.indexOf('<div class="transcript-actions">')
  const stats=source.indexOf("section('stats','Session'")
  assert.ok(actions>0)
  assert.ok(stats>actions)
  assert.match(css,/\.transcript-actions \{[^}]*flex:none;[^}]*flex-wrap:wrap/)
  // Both states of a band are flex:none, so only the transcript grows, and the open body
  // is bounded and scrolls rather than pushing the conversation off the view.
  assert.match(css,/\.transcript-section \{[^}]*flex:none/)
  assert.match(css,/\.transcript-section-body \{[^}]*max-height:[^;}]+;[^}]*overflow-y:auto/)
  assert.match(css,/\.transcript-section-body small \{[^}]*overflow-wrap:anywhere/)
  // The transcript's own band is a sibling of `.messages`, never a `<details>` around it:
  // a `<details>` interposes a UA-owned content box that becomes the flex item, and the
  // conversation inside one sizes to its text instead of taking the leftover height.
  assert.match(css,/\.transcript-section-toggle \{[^}]*flex:none/)
  assert.match(source,/<button type="button" class="transcript-section-toggle" aria-expanded=\{sections\.transcript\} aria-controls="history-transcript-body"/)
  assert.match(source,/\{sections\.transcript&&<div class="messages" id="history-transcript-body"/)
  assert.ok(!/<details[^>]*>\s*<summary>[^]{0,400}?class="messages"/.test(source))
})

test('the detail view drops cross-vendor review and renames the two resume actions',()=>{
  assert.ok(!source.includes('Review with'))
  assert.ok(!source.includes('onSecondOpinion'))
  assert.ok(!source.includes('Resume as new'))
  assert.ok(!source.includes('Resume later'))
  assert.match(source,/>Resume<\/button>/)
  assert.match(source,/>Schedule Resume<\/button>/)
  // Back sits in the detail view's own top bar rather than in a full-width row above it.
  const heading=source.indexOf('<div class="transcript-heading">')
  assert.ok(heading>0&&source.indexOf('class="history-back"')>heading)
  assert.ok(source.indexOf('class="history-back"')<source.indexOf('<div><h3>[{transcript.entry.backend}]'))
  assert.doesNotMatch(css,/\.history-back\{display:none/)
  assert.doesNotMatch(css,/\.history-back \{ display:block;width:100%/)
})

test('the filter block wraps content-width controls instead of stretching them',()=>{
  assert.match(css,/\.history-search \{ display:flex;flex-wrap:wrap/)
  assert.doesNotMatch(css,/\.history-search \{ display:grid;grid-template-columns:repeat\(2/)
  // A floor so a short list is not a stub, capped at the container so a narrow sidebar
  // wraps rather than overflowing.
  assert.match(css,/\.history-search .dropdown-trigger\{ flex:0 1 auto;min-width:min\(7rem,100%\);max-width:100%/)
  // The full-width members say so in flex terms now that the block is not a grid.
  for(const selector of ['.history-query-row','.history-backfill-control','.history-backfill-status']){
    assert.match(css,new RegExp(`\\${selector} \\{ flex:1 1 100%`),selector)
  }
})

test('a conversation opens on its transcript and on nothing else',()=>{
  const opened=defaultHistorySections()
  assert.deepEqual(Object.keys(opened).sort(),[...HISTORY_SECTION_KEYS].sort())
  // The transcript is a band now, and it is the only one that starts open. Everything else
  // is metadata about the run, and each band that opened by itself was height taken from
  // the one thing the reader came for.
  assert.equal(opened.transcript,true)
  assert.deepEqual(Object.entries(opened).filter(([,open])=>open).map(([key])=>key),['transcript'])
  // No device split: the view a reader learns on a phone is the view they get on a desktop.
  assert.deepEqual(defaultHistorySections(),opened)
})

test('a closed band keeps its count and drops a whole commit subject',()=>{
  // A count is one short line and stays visible while closed; the commits line carries a
  // commit subject and wraps to a taller block than the band it labels.
  for(const key of HISTORY_SECTION_KEYS)assert.equal(sectionKeysVisible(key,true),true,key)
  assert.equal(sectionKeysVisible('commits',false),false)
  for(const key of HISTORY_SECTION_KEYS.filter(item=>item!=='commits')){
    assert.equal(sectionKeysVisible(key,false),true,key)
  }
})

test('the behavioural timeline previews the two newest entries and hides the rest',()=>{
  assert.equal(HISTORY_TIMELINE_PREVIEW,2)
  // Deliberately oldest-first, which is the order the transcript route returns.
  const records=[{id:'a',t0:10},{id:'b',t0:20},{id:'c',t0:30},{id:'d',t0:40}]
  const {recent,earlier}=splitRecentScans(records)
  assert.deepEqual(recent.map(item=>item.id),['d','c'])
  assert.deepEqual(earlier.map(item=>item.id),['b','a'])
})

test('equal timeline timestamps keep the order the daemon returned',()=>{
  const records=[{id:'a',t0:5},{id:'b',t0:5},{id:'c',t0:5}]
  assert.deepEqual(splitRecentScans(records,3).recent.map(item=>item.id),['a','b','c'])
})

test('a missing timeline timestamp sorts last rather than throwing',()=>{
  const {recent,earlier}=splitRecentScans([{id:'a'},{id:'b',t0:9}],1)
  assert.deepEqual(recent.map(item=>item.id),['b'])
  assert.deepEqual(earlier.map(item=>item.id),['a'])
})

test('a closed commits section names the count and the newest commit',()=>{
  const short=(oid:string)=>oid.slice(0,7)
  assert.equal(
    commitsSummary([
      {commitOid:'aaaaaaaaaaaa',subject:'older',observedAt:100},
      {commitOid:'bbbbbbbbbbbb',subject:'newest thing',observedAt:900},
    ],short),
    '2 commits · latest bbbbbbb newest thing',
  )
  // Newest by observation rather than by position, because the provenance read is not
  // promised to come back chronological.
  assert.match(commitsSummary([{commitOid:'cccccccccccc',observedAt:5},{commitOid:'dddddddddddd',observedAt:4}],short),/latest ccccccc$/)
  assert.equal(commitsSummary([{commitOid:'eeeeeeeeeeee',subject:'  ',observedAt:1}],short),'1 commit · latest eeeeeee')
  assert.equal(commitsSummary([],short),'no commits')
})

test('a count reads as a count, singular included',()=>{
  assert.equal(countLabel(1,'commit'),'1 commit')
  assert.equal(countLabel(0,'commit'),'0 commits')
  assert.equal(countLabel(1,'entry','entries'),'1 entry')
  assert.equal(countLabel(4,'entry','entries'),'4 entries')
})

test('the closed stats line carries what decides whether to open a conversation',()=>{
  const stats=historyKeyStats(
    {exit_reason:'killed',model:'claude-fable-5',last_message_at:1786118091,last_message_role:'assistant',cost_usd:1.5},
    {timestamp:value=>`ts:${value}`,money:value=>`$${value}`},
  )
  assert.deepEqual(stats.map(stat=>stat.label),['state','model','last agent','cost'])
  assert.deepEqual(stats.map(stat=>stat.value),['killed','claude-fable-5','ts:1786118091','$1.5'])
  // The model is handed on raw so `ModelName` keeps owning how a model is displayed.
  assert.equal(stats[1].model,true)
  // A run that never exited still says something, and an unattributed final message is
  // not attributed to either party.
  const unknown=historyKeyStats({final_state:'idle'},{timestamp:()=>'—',money:value=>`$${value}`})
  assert.equal(unknown[0].value,'idle')
  assert.equal(unknown[2].label,'last message')
  assert.equal(historyKeyStats({},{timestamp:()=>'—',money:value=>`$${value}`})[0].value,'indexed')
})

test('a speaker is named the way the rest of the browser names one',()=>{
  assert.equal(speakerLabel('assistant'),'agent')
  assert.equal(speakerLabel('user'),'you')
  assert.equal(speakerLabel(null),'message')
  assert.equal(speakerLabel(undefined),'message')
})
