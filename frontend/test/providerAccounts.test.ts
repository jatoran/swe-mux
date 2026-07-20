import assert from 'node:assert/strict'
import test from 'node:test'
import { accountPopoverStyle, formatResetRemaining, providerWeeklyUsage, quotaSummary, usageBand } from '../src/providerAccountDisplay.ts'

test('rail quota chips report each provider weekly window, never session or fable',()=>{
  const accounts=[
    {provider:'claude',id:'a',quota:{status:'ready',session:{used_percent:12},weekly:{used_percent:81},fable:{used_percent:95}}},
    {provider:'codex',id:'b',quota:{status:'ready',session:{used_percent:44},weekly:{used_percent:20}}},
    // Not the selected Claude account, so its exhausted window is irrelevant.
    {provider:'claude',id:'c',quota:{status:'ready',weekly:{used_percent:99}}},
  ]
  const weekly=providerWeeklyUsage(accounts,{claude:'a',codex:'b'})
  assert.equal(weekly.claude?.used_percent,81)
  assert.equal(weekly.codex?.used_percent,20)
})

test('rail quota chips blank out unreadable or unselected accounts',()=>{
  const accounts=[
    {provider:'claude',id:'a',quota:{status:'error',weekly:{used_percent:99}}},
    {provider:'codex',id:'b',quota:{status:'ready',fable:{used_percent:7}}},
  ]
  const weekly=providerWeeklyUsage(accounts,{claude:'a',codex:'b'})
  assert.equal(weekly.claude,null)
  // A provider reporting only fable has no weekly window to show.
  assert.equal(weekly.codex,null)
  assert.deepEqual(providerWeeklyUsage(accounts,{claude:null,codex:null}),{})
})

test('usage bands escalate at the thresholds every condensed indicator shares',()=>{
  assert.equal(usageBand(0),'ok')
  assert.equal(usageBand(74.9),'ok')
  assert.equal(usageBand(75),'warn')
  assert.equal(usageBand(89.9),'warn')
  assert.equal(usageBand(90),'critical')
  assert.equal(usageBand(undefined),'unknown')
  assert.equal(usageBand(null),'unknown')
})

test('quota summaries include compact 5-hour and weekly reset countdowns',()=>{
  const now=2_000_000
  const account={quota:{status:'ready',session:{used_percent:5,resets_at:now+4*3600+3*60},weekly:{used_percent:63,resets_at:now+3*86400+3600}}}
  assert.equal(formatResetRemaining(now+4*3600+3*60,now),'4h3m')
  assert.equal(formatResetRemaining(now+3*86400+3600,now),'3d1h')
  assert.equal(quotaSummary(account,now),'5% 4h3m - 63% 3d1h')
})

test('account popovers escape a narrow sidebar and remain inside the viewport',()=>{
  const style=accountPopoverStyle({left:0,right:190,top:650,bottom:730},false,{width:1200,height:800})
  assert.equal(style.left,'8px')
  assert.equal(style.width,'340px')
  assert.equal(style.bottom,'154px')
  assert.equal(style.maxHeight,'638px')
})
