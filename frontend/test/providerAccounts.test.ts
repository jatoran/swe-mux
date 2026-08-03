import assert from 'node:assert/strict'
import test from 'node:test'
import { accountPopoverStyle, chipUsageBand, formatResetRemaining, providerQuotaWindows, quotaChipSegments, quotaSummary, shownUsageBand, usageBand } from '../src/providerAccountDisplay.ts'

test('quota windows come from the selected account of each provider, never another slot',()=>{
  const accounts=[
    {provider:'claude',id:'a',quota:{status:'ready',session:{used_percent:12},weekly:{used_percent:81},fable:{used_percent:95}}},
    {provider:'codex',id:'b',quota:{status:'ready',session:{used_percent:44},weekly:{used_percent:20}}},
    // Not the selected Claude account, so its exhausted window is irrelevant.
    {provider:'claude',id:'c',quota:{status:'ready',weekly:{used_percent:99}}},
  ]
  const quotas=providerQuotaWindows(accounts,{claude:'a',codex:'b'})
  assert.equal(quotas.claude?.session?.used_percent,12)
  assert.equal(quotas.claude?.weekly?.used_percent,81)
  assert.equal(quotas.claude?.fable?.used_percent,95)
  assert.equal(quotas.codex?.weekly?.used_percent,20)
  // The rail narrows this to weekly alone; nothing else on it may leak in.
  assert.equal(quotas.codex?.fable,null)
})

test('quota chips blank out unreadable or unselected accounts',()=>{
  const accounts=[
    {provider:'claude',id:'a',quota:{status:'error',weekly:{used_percent:99}}},
    {provider:'codex',id:'b',quota:{status:'ready',fable:{used_percent:7}}},
  ]
  const quotas=providerQuotaWindows(accounts,{claude:'a',codex:'b'})
  // One failed poll invalidates the whole account, not just the window that errored.
  assert.equal(quotas.claude?.weekly,null)
  // A provider reporting only fable has no weekly window to show.
  assert.equal(quotas.codex?.weekly,null)
  assert.equal(quotas.codex?.fable?.used_percent,7)
  assert.deepEqual(providerQuotaWindows(accounts,{claude:null,codex:null}),{})
})

test('the toolbar chip reads 5h/weekly/fable, keeping the slot of a window the provider omits',()=>{
  const accounts=[
    {provider:'claude',id:'a',quota:{status:'ready',session:{used_percent:89.6},weekly:{used_percent:80},fable:{used_percent:74}}},
    // Codex reports no 5-hour window today, so that slot has to survive as a dash.
    {provider:'codex',id:'b',quota:{status:'ready',weekly:{used_percent:73.5}}},
  ]
  const quotas=providerQuotaWindows(accounts,{claude:'a',codex:'b'})
  assert.deepEqual(quotaChipSegments(quotas.claude).map(segment=>segment.text),['90','80','74'])
  assert.deepEqual(quotaChipSegments(quotas.codex).map(segment=>segment.text),['—','74'])
  // No fable slot at all for a plan without one, rather than a third dash.
  assert.deepEqual(quotaChipSegments(quotas.codex).map(segment=>segment.key),['session','weekly'])
  assert.deepEqual(quotaChipSegments(quotas.claude).map(segment=>segment.band),['critical','warn','ok'])
})

test('the toolbar chip blanks every window when the account poll errored',()=>{
  const accounts=[{provider:'claude',id:'a',quota:{status:'error',session:{used_percent:12},weekly:{used_percent:99}}}]
  const quotas=providerQuotaWindows(accounts,{claude:'a'})
  assert.deepEqual(quotaChipSegments(quotas.claude).map(segment=>segment.text),['—','—'])
  assert.equal(chipUsageBand(quotas.claude),'unknown')
  // An unselected or missing provider has no entry, and the chip degrades to the same reading.
  assert.deepEqual(quotaChipSegments(quotas.codex).map(segment=>segment.text),['—','—'])
})

test('a chip bands on its hottest window, not on weekly alone',()=>{
  const accounts=[{provider:'claude',id:'a',quota:{status:'ready',session:{used_percent:93},weekly:{used_percent:20}}}]
  const quotas=providerQuotaWindows(accounts,{claude:'a'})
  assert.equal(chipUsageBand(quotas.claude),'critical')
  // Fable counts too: it is the window a Claude plan runs out of first.
  assert.equal(chipUsageBand({session:{used_percent:1},weekly:{used_percent:2},fable:{used_percent:80}}),'warn')
})

test('a band follows the digits the chip prints, not the value behind them',()=>{
  // 89.6 shows as `90`, so it must colour as 90 does. Banding the raw value put a critical
  // number in the warn colour at exactly the reading a user starts watching for.
  assert.equal(usageBand(89.6),'warn')
  assert.equal(shownUsageBand(89.6),'critical')
  assert.equal(shownUsageBand(74.4),'ok')
  assert.equal(shownUsageBand(74.5),'warn')
  assert.equal(shownUsageBand(undefined),'unknown')
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
