import assert from 'node:assert/strict'
import test from 'node:test'
import { accountPopoverStyle, formatResetRemaining, quotaSummary } from '../src/providerAccountDisplay.ts'

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
