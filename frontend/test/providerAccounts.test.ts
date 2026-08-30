import assert from 'node:assert/strict'
import test from 'node:test'
import { accountAbbreviation, accountPopoverStyle, formatResetRemaining, hasFableWindow, loginCommand, loginOf, loginRunning, providerQuotaWindows, quotaGridSegments, quotaRowCells, quotaSummary, shownUsageBand, signInTitle, usageBand, QUOTA_COLUMN_HEADINGS } from '../src/providerAccountDisplay.ts'

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

test('the quota grid reads reset/percentage columns and omits windows the provider does not report',()=>{
  const now=2_000_000
  const accounts=[
    {provider:'claude',id:'a',quota:{status:'ready',session:{used_percent:89.6,resets_at:now+75*60},weekly:{used_percent:80,resets_at:now+5*86400+13*3600},fable:{used_percent:74}}},
    // Codex reports no 5-hour window today, so only its weekly value should render.
    {provider:'codex',id:'b',quota:{status:'ready',weekly:{used_percent:73.5}}},
  ]
  const quotas=providerQuotaWindows(accounts,{claude:'a',codex:'b'})
  assert.deepEqual(quotaGridSegments(quotas.claude,now).map(segment=>segment.heading),['1h15m','5d13h','Fable'])
  assert.deepEqual(quotaGridSegments(quotas.claude,now).map(segment=>segment.text),['90%','80%','74%'])
  assert.deepEqual(quotaGridSegments(quotas.codex,now).map(segment=>segment.text),['74%'])
  assert.deepEqual(quotaGridSegments(quotas.codex,now).map(segment=>segment.key),['weekly'])
  assert.deepEqual(quotaGridSegments(quotas.claude,now).map(segment=>segment.band),['critical','warn','ok'])
})

test('the expanded chip omits every window when the account poll errored',()=>{
  const accounts=[{provider:'claude',id:'a',quota:{status:'error',session:{used_percent:12},weekly:{used_percent:99}}}]
  const quotas=providerQuotaWindows(accounts,{claude:'a'})
  assert.deepEqual(quotaGridSegments(quotas.claude),[])
  assert.equal(shownUsageBand(quotas.claude?.weekly?.used_percent),'unknown')
  // An unselected or missing provider has no entry, and the chip degrades to the same reading.
  assert.deepEqual(quotaGridSegments(quotas.codex),[])
})

test('account abbreviations are trimmed, capped at four characters, and visible without a label',()=>{
  assert.equal(accountAbbreviation(' Personal '),'PERS')
  assert.equal(accountAbbreviation('work'),'WORK')
  assert.equal(accountAbbreviation(''),'—')
})

test('a condensed square bands on the weekly window it prints, not on a hotter one it hides',()=>{
  const accounts=[{provider:'claude',id:'a',quota:{status:'ready',session:{used_percent:93},weekly:{used_percent:20}}}]
  const quotas=providerQuotaWindows(accounts,{claude:'a'})
  assert.equal(shownUsageBand(quotas.claude?.weekly?.used_percent),'ok')
  // Every window still bands individually where all of them are drawn.
  assert.deepEqual(quotaGridSegments(quotas.claude).map(segment=>segment.band),['critical','ok'])
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
  const account={quota:{status:'ready',session:{used_percent:5,resets_at:now+4*3600+3*60},weekly:{used_percent:63,resets_at:now+3*86400+3600},fable:{used_percent:30}}}
  assert.equal(formatResetRemaining(now+4*3600+3*60,now),'4h3m')
  assert.equal(formatResetRemaining(now+3*86400+3600,now),'3d1h')
  assert.equal(quotaSummary(account,now),'5% 4h3m - 63% 3d1h · 30% Fable')
})

test('every account of a provider emits the same quota cells, so the switcher reads as columns',()=>{
  const now=2_000_000
  const first={quota:{status:'ready',session:{used_percent:5,resets_at:now+4*3600+3*60},weekly:{used_percent:63,resets_at:now+3*86400+3600},fable:{used_percent:30}}}
  // Different lengths in every figure - which is the whole defect when these are joined
  // into a sentence and stacked.
  const second={quota:{status:'ready',session:{used_percent:100,resets_at:now+22*60},weekly:{used_percent:7,resets_at:now+6*86400+23*3600}}}
  assert.deepEqual(quotaRowCells(first,true,now),[
    {key:'session',percent:'5%',reset:'4h3m'},
    {key:'weekly',percent:'63%',reset:'3d1h'},
    // Fable reports no reset instant of its own; the weekly window beside it is what rolls over.
    {key:'fable',percent:'30%',reset:''},
  ])
  // The second account has no Fable reading at all, and still carries the column: a row
  // that omitted it would shift every column after it, which is the defect rather than a
  // smaller version of it.
  assert.deepEqual(quotaRowCells(second,true,now),[
    {key:'session',percent:'100%',reset:'22m'},
    {key:'weekly',percent:'7%',reset:'6d23h'},
    {key:'fable',percent:'—',reset:''},
  ])
  // A provider that never reports Fable gets two columns, not an empty third on every row.
  assert.deepEqual(quotaRowCells(second,false,now).map(cell=>cell.key),['session','weekly'])
})

test('a section carries the fable column when any of its accounts reports one',()=>{
  const withFable={quota:{status:'ready',weekly:{used_percent:9},fable:{used_percent:30}}}
  const without={quota:{status:'ready',weekly:{used_percent:9}}}
  assert.equal(hasFableWindow([without,withFable]),true)
  assert.equal(hasFableWindow([without]),false)
  assert.equal(hasFableWindow([]),false)
  // An errored poll invalidates the whole account, so it cannot be the reason a column exists.
  assert.equal(hasFableWindow([{quota:{status:'error',fable:{used_percent:30}}}]),false)
})

test('an errored poll empties every quota cell rather than showing a stale mix',()=>{
  const account={quota:{status:'error',error:'claude usage endpoint returned 500',session:{used_percent:12},weekly:{used_percent:99},fable:{used_percent:4}}}
  assert.deepEqual(quotaRowCells(account,true).map(cell=>cell.percent),['—','—','—'])
  assert.deepEqual(quotaRowCells(undefined,false).map(cell=>cell.percent),['—','—'])
})

test('the quota column headings use the same window names the tooltips do',()=>{
  assert.deepEqual(QUOTA_COLUMN_HEADINGS,{session:'5h',weekly:'weekly',fable:'fable'})
})

test('only a running sign-in tightens the poll; a finished one is a result to read',()=>{
  const running={provider:'claude',state:'running',started_at:1} as const
  const failed={provider:'codex',state:'failed',started_at:1,error:'codex login timed out'} as const
  assert.equal(loginRunning({claude:running,codex:null}),true)
  // A `succeeded`/`failed` entry is an outcome sitting on screen, not something to keep
  // asking the daemon about - polling it at 3s would be a tighter cadence for less reason.
  assert.equal(loginRunning({claude:null,codex:failed}),false)
  assert.equal(loginRunning({}),false)
  assert.equal(loginRunning(undefined),false)
  assert.equal(loginRunning(null),false)
})

test('a provider with no sign-in reported reads as none rather than undefined',()=>{
  const running={provider:'claude',state:'running',started_at:1} as const
  assert.equal(loginOf({claude:running,codex:null},'claude'),running)
  assert.equal(loginOf({claude:running,codex:null},'codex'),null)
  // The map is absent entirely against a daemon older than this field.
  assert.equal(loginOf(undefined,'claude'),null)
  assert.equal(loginOf({},'pi'),null)
})

test('the sign-in tooltip names the command the daemon reported, never one compiled in here',()=>{
  // The daemon builds these from the *configured* executable, so an install that pointed
  // `harness_exe` elsewhere is described accurately rather than by the shipped default.
  const commands={claude:'D:\\tools\\claude.cmd auth login --claudeai',codex:'codex login'}
  assert.equal(loginCommand(commands,'claude'),'D:\\tools\\claude.cmd auth login --claudeai')
  assert.equal(
    signInTitle(commands,'codex'),
    'Runs codex login on the daemon host and saves the account it produces.',
  )
  // A daemon older than this field reports none. The control still says what it does; it
  // just does not name a command it cannot vouch for.
  assert.equal(loginCommand(undefined,'claude'),'')
  assert.equal(
    signInTitle(undefined,'claude'),
    "Runs claude's login on the daemon host and saves the account it produces.",
  )
})

test('account popovers escape a narrow sidebar and remain inside the viewport',()=>{
  const style=accountPopoverStyle({left:0,right:190,top:650,bottom:730},false,{width:1200,height:800})
  assert.equal(style.left,'8px')
  assert.equal(style.width,'340px')
  assert.equal(style.bottom,'154px')
  assert.equal(style.maxHeight,'638px')
})
