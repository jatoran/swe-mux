// The ranked half of the Alerts tab: which of N sessions actually needs you.
//
// Three rules shape this surface and are easy to lose in a redesign:
//
// - The channels are drawn as separate groups, never as one score-sorted list.
//   Merging cheap-blocking work (answer a prompt) with expensive-blocking work
//   (the plan is wrong) is the failure mode that trains people to ignore alarms.
// - Suppressed counts are always visible. An item the ranker held back that the
//   user cannot see is indistinguishable from a detector that silently broke.
// - Rationale is evidence-first. The narration, when it exists at all, is drawn
//   under the deterministic summary rather than in place of it.
//
// Nothing here is pushed. This is a surface you open; the daemon holds no push
// route for ranked items.
import { useEffect, useState } from 'preact/hooks'
import { GrantGate } from './GrantGate'
import {
  PROJECT_AUTOMATIONS_CHANGED,
  fetchProjectAutomations,
  forgetProjectAutomations,
} from './projectAutomations'
import type { Project } from './types'
import {
  CHANNEL_HINTS, CHANNEL_LABELS, CHANNEL_ORDER,
  budgetLine, decideAttentionRule, fanoutHeadline, fetchAttentionInbox,
  sendAttentionFeedback, suppressedLabel,
  type AttentionInbox as InboxData, type AttentionItem,
} from './attention'

export function AttentionInbox({onOpenSession,project}:{
  onOpenSession:(sessionId:string)=>void
  /** The Project whose opt-in is reported when nothing is ranked. The inbox itself is
   *  fleet-wide; ranking is permitted per Project, so the empty state can only name one. */
  project?:Project
}) {
  const [data,setData]=useState<InboxData|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [showDigest,setShowDigest]=useState(false)
  const [rankingOn,setRankingOn]=useState<boolean|null>(null)

  const load=()=>{fetchAttentionInbox().then(setData).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))}
  useEffect(()=>{
    load()
    const interval=window.setInterval(load,30_000)
    const refresh=()=>load()
    window.addEventListener('mux:turn-ended',refresh)
    return()=>{window.clearInterval(interval);window.removeEventListener('mux:turn-ended',refresh)}
  },[])

  useEffect(()=>{
    if(!project?.id){setRankingOn(null);return}
    let stale=false
    const read=()=>{
      fetchProjectAutomations(project.id)
        .then(state=>{if(!stale)setRankingOn(state.enabled.includes('attention_ranking'))})
        .catch(()=>{if(!stale)setRankingOn(null)})
    }
    read()
    window.addEventListener(PROJECT_AUTOMATIONS_CHANGED,read)
    return()=>{stale=true;window.removeEventListener(PROJECT_AUTOMATIONS_CHANGED,read)}
  },[project?.id])

  const write=async(operation:()=>Promise<unknown>)=>{
    if(busy)return
    setBusy(true);setError('')
    try{await operation();load()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }

  if(!data)return <p class="drawer-status">{error||'Ranking has produced nothing yet.'}</p>
  const channels=CHANNEL_ORDER.filter(channel=>channel!=='digest'||showDigest)
  const total=CHANNEL_ORDER.reduce((count,channel)=>count+(data.channels[channel]?.length||0),0)

  return <section class="attention-inbox">
    <p class="attention-fanout" aria-live="polite">{fanoutHeadline(data.fanout)}</p>
    <p class="attention-budget">{budgetLine(data.budget)}{data.resumption_lag.mean_seconds!==null?` · returning to interrupted work takes you ${Math.round(data.resumption_lag.mean_seconds)}s on average`:''}</p>
    {error&&<p class="drawer-status">{error}</p>}
    {data.rules.filter(rule=>rule.state==='proposed'&&rule.total>0).map(rule=><article class="attention-rule">
      <div><strong>Rule suggestion</strong><span>{rule.statement}</span></div>
      <div class="notification-row-actions">
        <button disabled={busy} onClick={()=>void write(()=>decideAttentionRule(rule.incident_class,rule.channel,true))}>accept</button>
        <button disabled={busy} onClick={()=>void write(()=>decideAttentionRule(rule.incident_class,rule.channel,false))}>reject</button>
      </div>
    </article>)}
    {data.rules.filter(rule=>rule.state==='accepted').map(rule=><p class="attention-rule-active">
      Your rule holds {rule.incident_class.replaceAll('_',' ')} items in the inbox{rule.expires_at?` until ${new Date(rule.expires_at*1000).toLocaleDateString()}, when it comes back for review`:''}.
      <button disabled={busy} onClick={()=>void write(()=>decideAttentionRule(rule.incident_class,rule.channel,false))}>drop rule</button>
    </p>)}
    {/* "Nothing is ranked" is two different statements. With ranking permitted it means a
        quiet fleet; without it, nothing will ever be ranked for this Project however busy it
        gets, and the switch is one click away. */}
    {total===0&&(rankingOn===false&&project
      ? <GrantGate ids={['project.attentionRanking']} projectId={project.id}
        heading={`Attention ranking is off for ${project.name}.`}
        onGranted={()=>{forgetProjectAutomations(project.id);void load()}}>
        <p>Findings from its sessions are recorded either way. Ranking is what decides
        which of them are worth interrupting you for, and routes those into this inbox.</p>
      </GrantGate>
      : <p class="notification-empty">Nothing is ranked. Enable attention ranking for a Project to route its findings here.</p>)}
    {channels.map(channel=>{
      const items=data.channels[channel]||[]
      if(!items.length)return null
      return <div class={`attention-channel ${channel}`}>
        <h4>{CHANNEL_LABELS[channel]} <span>{items.length}</span></h4>
        <p class="attention-channel-hint">{CHANNEL_HINTS[channel]}</p>
        {items.map(item=><AttentionRow key={item.id} item={item} busy={busy}
          onOpenSession={onOpenSession}
          onFeedback={(action)=>void write(()=>sendAttentionFeedback(item.id,action))} />)}
      </div>
    })}
    <div class="drawer-actions">
      <button aria-pressed={showDigest} onClick={()=>setShowDigest(value=>!value)}>{showDigest?'hide digest':'show digest'}</button>
      <span class="attention-suppressed">{data.suppressed_total?`${data.suppressed_total} held back: ${Object.entries(data.suppressed).map(([reason,count])=>`${count} ${suppressedLabel(reason)}`).join(' · ')}`:'nothing held back'}</span>
    </div>
  </section>
}

function AttentionRow({item,busy,onOpenSession,onFeedback}:{
  item:AttentionItem;busy:boolean;onOpenSession:(sessionId:string)=>void
  onFeedback:(action:'acted'|'dismissed')=>void
}) {
  return <article class="attention-item">
    <span class={`state-dot ${item.channel==='interrupt_now'?'awaiting':'idle'}`}/>
    <div>
      <strong>{item.title}</strong>
      <span>{item.summary}</span>
      {item.action&&<span class="attention-action">{item.action}</span>}
      {item.narration&&<span class="attention-narration">{item.narration}</span>}
      <small>
        {new Date(item.created_at*1000).toLocaleString()} · {item.kinds.join(', ')}
        {item.contributions>1?` · ${item.contributions} findings, one slot`:''}
        {item.suppressed_reason?` · ${suppressedLabel(item.suppressed_reason)}`:''}
        {` · confidence ${Math.round(item.confidence*100)}%`}
      </small>
    </div>
    <div class="notification-row-actions">
      {item.session_id&&<button onClick={()=>onOpenSession(item.session_id!)}>Open session</button>}
      <button disabled={busy} onClick={()=>onFeedback('acted')}>acted</button>
      <button disabled={busy} onClick={()=>onFeedback('dismissed')}>dismiss</button>
    </div>
  </article>
}
