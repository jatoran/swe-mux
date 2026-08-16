import assert from 'node:assert/strict'
import test from 'node:test'
import {
  accountDisplayLabel,
  modelPeriodRows,
  quotaPointValue,
  quotaSeriesPath,
  type UsageSource,
} from '../src/usageAnalytics.ts'

const base = {
  input_tokens:0,
  output_tokens:0,
  cache_creation_tokens:0,
  cache_read_tokens:0,
  total_tokens:0,
  cost_usd:0,
}

test('model history stays separated by source, model, and day',()=>{
  const sources:UsageSource[]=[
    {
      source_id:'codex',source_label:'Codex',collector_id:'ccusage',daily:[],monthly:[],sessions:[],models:[],totals:base,
      model_daily:[
        {...base,date:'2026-08-01',model:'gpt-5',total_tokens:100,cost_usd:1,cost_method:'proportional'},
        {...base,date:'2026-08-02',model:'gpt-5',total_tokens:200,cost_usd:2,cost_method:'proportional'},
        {...base,date:'2026-08-02',model:'gpt-5-mini',total_tokens:50,cost_usd:.2,cost_method:'proportional'},
      ],
    },
    {
      source_id:'claude',source_label:'Claude Code',collector_id:'ccusage',daily:[],monthly:[],sessions:[],models:[],totals:base,
      model_daily:[
        {...base,date:'2026-08-02',model:'opus',total_tokens:300,cost_usd:3,cost_method:'source_estimate'},
      ],
    },
  ]
  const rows=modelPeriodRows(sources,new Set(['2026-08-01','2026-08-02']),'daily')
  assert.deepEqual(rows.map(row=>[row.period,row.source_id,row.model,row.total_tokens]),[
    ['2026-08-02','claude','opus',300],
    ['2026-08-02','codex','gpt-5',200],
    ['2026-08-02','codex','gpt-5-mini',50],
    ['2026-08-01','codex','gpt-5',100],
  ])
  assert.equal(rows[1].cost_method,'proportional')
})

test('monthly model history sums days without merging sources or models',()=>{
  const source:UsageSource={
    source_id:'codex',source_label:'Codex',collector_id:'ccusage',daily:[],monthly:[],sessions:[],models:[],totals:base,
    model_daily:[
      {...base,date:'2026-08-01',model:'gpt-5',total_tokens:100,cost_usd:1,cost_method:'proportional'},
      {...base,date:'2026-08-02',model:'gpt-5',total_tokens:200,cost_usd:2,cost_method:'proportional'},
    ],
  }
  const rows=modelPeriodRows([source],new Set(['2026-08-01','2026-08-02']),'monthly')
  assert.equal(rows.length,1)
  assert.equal(rows[0].period,'2026-08')
  assert.equal(rows[0].total_tokens,300)
  assert.equal(rows[0].cost_usd,3)
})

test('quota query carries server-side provider, account, range, and resolution filters',()=>{
  const path=quotaSeriesPath({
    provider:'codex',account:'account-a',range:'7',resolution:'raw',now:2_000_000,
  })
  const url=new URL(path,'http://localhost')
  assert.equal(url.pathname,'/api/telemetry/quota-series')
  assert.equal(url.searchParams.get('provider'),'codex')
  assert.equal(url.searchParams.get('account'),'account-a')
  assert.equal(url.searchParams.get('resolution'),'raw')
  assert.equal(url.searchParams.get('since'),String(2_000_000-7*86400))
  assert.equal(url.searchParams.get('until'),'2000000')
})

test('quota point readers handle raw samples and daily rollups',()=>{
  assert.equal(quotaPointValue({
    id:1,provider:'codex',account_id:'a',sampled_at:1,status:'ready',freshness:'fresh',
    raw_precision:1,active:true,session:{used_percent:42},weekly:null,
  },'session'),42)
  assert.equal(quotaPointValue({
    provider:'codex',account_id:'a',day:'2026-08-01',samples:2,errors:0,
    session_last:55,weekly_last:75,
  },'weekly'),75)
})

test('account labels use the friendly label and disambiguating email',()=>{
  assert.equal(accountDisplayLabel({label:'Work',email:'dev@example.com',provider:'codex'}),'Work · dev@example.com · codex')
  assert.equal(accountDisplayLabel({label:'Personal',email:null,provider:'claude'}),'Personal · claude')
})
