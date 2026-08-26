// Notifications live in the utility drawer, not a modal: they are something you
// glance at while working, and the modal made "check what fired" a full-screen
// interruption. The list is unchanged; only its host moved.
//
// Attention records are dismissible here. They persist for the automation retention
// window (90 days) and this tab is where a human actually sees them, so without a
// dismiss the list was append-only from the only surface that reads it: one detector
// firing on a normal workflow buried every record that mattered. Dismissing marks
// read — the same flag the automation dashboard's inbox uses — and deletes nothing,
// so "show dismissed" brings the history back.
//
// Ranked attention leads this tab, and the raw records follow it. The records
// are every finding in arrival order; ranking is the judgement about which of
// them is worth your attention and when. Keeping both on one surface is
// deliberate: the ranked view is the one you read, and the raw list is how you
// check what it decided against.
//
// This is now the *only* home for attention items. The Automation dashboard used to draw
// the same ranked inbox and the same records under its "attend" group, so one inbox had two
// homes and neither said the other existed; that view is a link here now. The away report
// came with it, since it is a reading of this inbox rather than a fact about the pipeline
// that fills it.
//
// Which is also why this tab is not hidden by default while Processes is. It is the only
// drawer tab that draws an unread badge, and hiding the one surface that says something
// needs you is the opposite of a simplification.
import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { AttentionInbox } from './AttentionInbox'
import { CompactGrantFlag } from './GrantGate'
import { alertPreferences, setAlertPreferencesFor } from './alertPrefs'
import { currentProfile } from './deviceSettings'
import type { Project } from './types'

/**
 * The one device-scoped grant. It never reaches the daemon: an alert profile belongs to
 * this device class and nowhere else, so `GrantGate` hands the write back here rather
 * than inventing a server round trip for a local preference.
 */
function unmuteAlerts(): void {
  const profile = currentProfile()
  setAlertPreferencesFor(profile, { ...alertPreferences(), enabled: true })
  window.dispatchEvent(new CustomEvent('mux:settings-changed'))
}

export type UiNotification = {
  ts:number;channel:string;delivery_id:string;session_id?:string
  session_name?:string;type:string;source?:string;seq?:number
}

export type Delivery = {
  id:string;correlation_id:string;created_at:number;updated_at:number
  provider:string;channel:string;status:string;attempts:number
  session_id?:string;sender:string;reply_target?:string;event_type:string
}

export type AutomationNotification={id:string;session_id?:string;kind:string;title:string;message:string;severity:string;created_at:number;read_at?:number}
export type NotificationData = {notifications:UiNotification[];deliveries:Delivery[];automation?:AutomationNotification[]}

/**
 * "What happened while I was away?" — a summary of attention items and run notes since the
 * last terminal attach or input.
 *
 * It was a section of the Automation dashboard's health view, which is the last place
 * anyone looked for it: the dashboard is where you configure the pipeline, and this is a
 * reading of the inbox the pipeline fills. It reads the inbox; it lives with the inbox.
 *
 * Collapsed by default and fetched only on request, because generating it is a server-side
 * scan and nobody wants it every time they check what fired.
 */
function AwayReport() {
  const [report,setReport]=useState<unknown>(null)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const generate=async()=>{
    setBusy(true);setError('')
    try{setReport(await api('GET','/api/attention/absence'))}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  return <details class="attention-away">
    <summary>What happened while I was away?</summary>
    <p>Summarizes attention items and run notes since your last terminal attach or input.</p>
    <button disabled={busy} onClick={()=>void generate()}>{busy?'generating…':'generate away report'}</button>
    {error&&<p class="usage-error">{error}</p>}
    {report!==null&&<pre>{JSON.stringify(report,null,2)}</pre>}
  </details>
}

export function NotificationsTab({data,onOpenSession,onChanged,project}:{
  data:NotificationData;onOpenSession:(sessionId:string)=>void;onChanged:()=>void
  /** The active Project, for the ranked inbox's opt-in notice. */
  project?:Project
}) {
  const [showRead,setShowRead]=useState(false)
  const [view,setView]=useState<'now'|'review'|'history'>('now')
  const [alertsMuted,setAlertsMuted]=useState(()=>!alertPreferences().enabled)
  // The bell in the sidebar footer and the Alerts settings write the same store, so this
  // follows the one event that store emits rather than polling or re-reading on render.
  useEffect(()=>{
    const sync=()=>setAlertsMuted(!alertPreferences().enabled)
    window.addEventListener('mux:settings-changed',sync)
    return()=>window.removeEventListener('mux:settings-changed',sync)
  },[])
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const deliveries = new Map(data.deliveries.map(delivery=>[delivery.id,delivery]))
  const items = [...data.notifications].reverse()
  const records=[...(data.automation||[])]
  const open=records.filter(item=>!item.read_at)
  const automation=showRead?records:open

  const write=async(operation:()=>Promise<unknown>)=>{
    if(busy)return
    setBusy(true);setError('')
    try{await operation();onChanged()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const mark=(item:AutomationNotification)=>void write(()=>
    api('PATCH',`/api/automation/notifications/${item.id}`,{read:!item.read_at}))
  const dismissAll=()=>void write(()=>api('PATCH','/api/automation/notifications',{read:true}))

  return <>
    {/* Records keep arriving while alerts are muted — this panel is the history, not a
        delivery channel — so the mute is stated here rather than silently explaining why
        nothing made a sound. The bell in the sidebar footer is the same switch; this link
        is for the device-class settings behind it. */}
    {alertsMuted&&<CompactGrantFlag id="alerts.master"
      heading="Notifications are off on this device."
      consequence="Alerts are still recorded."
      confirmLabel="Unmute alerts on this device"
      applyDevice={unmuteAlerts}
      onGranted={()=>setAlertsMuted(!alertPreferences().enabled)}/>}
    <div class="alerts-view-tabs" role="tablist" aria-label="Alert urgency">
      <button role="tab" aria-selected={view==='now'} class={view==='now'?'active':''}
        title="Interrupt-worthy: actionable, worsening, and confident enough to spend attention budget"
        onClick={()=>setView('now')}>Now</button>
      <button role="tab" aria-selected={view==='review'} class={view==='review'?'active':''}
        title="Relevant but not urgent: wait until the next natural pause or task change"
        onClick={()=>setView('review')}>Review next</button>
      <button role="tab" aria-selected={view==='history'} class={view==='history'?'active':''}
        onClick={()=>setView('history')}>History</button>
    </div>
    {view==='now'&&<AttentionInbox onOpenSession={onOpenSession} project={project} visibleChannels={['interrupt_now']} />}
    {view==='review'&&<AttentionInbox onOpenSession={onOpenSession} project={project} visibleChannels={['next_breakpoint','inbox','digest']} />}
    {view==='history'&&<><AwayReport />
    <h4 class="attention-raw-heading">Every record</h4>
    <p class="drawer-status">{error||`${open.length} open · ${records.length-open.length} dismissed · ${items.length} delivered`}</p>
    <div class="notification-list">
      {automation.length===0&&items.length===0&&<p class="notification-empty">{showRead||!records.length
        ?'No UI notifications yet. Universal hook notifications and attention records appear here.'
        :'No open attention records. Show dismissed to review what fired earlier.'}</p>}
      {automation.map(item=><article class={item.read_at?'dismissed':undefined}>
        <span class={`state-dot ${item.severity==='warning'?'awaiting':'idle'}`}/>
        <div><strong>{item.title}</strong><span>{item.message}</span><small>{new Date(item.created_at*1000).toLocaleString()} · automation::{item.kind}</small></div>
        <div class="notification-row-actions">
          {item.session_id&&<button onClick={()=>onOpenSession(item.session_id!)}>Open session</button>}
          <button disabled={busy} onClick={()=>mark(item)}>{item.read_at?'Restore':'Dismiss'}</button>
        </div>
      </article>)}
      {items.map(item=>{const delivery=deliveries.get(item.delivery_id);return <article>
        <span class="state-dot idle" />
        <div><strong>{item.session_name||'daemon'} · {item.type.replaceAll('_',' ')}</strong><span>{new Date(item.ts*1000).toLocaleString()} · {item.channel}</span><small>delivery::{delivery?.status||'delivered'} · attempts::{delivery?.attempts??1} · correlation::{delivery?.correlation_id||item.seq||'—'}</small></div>
        {item.session_id&&<div class="notification-row-actions"><button onClick={()=>onOpenSession(item.session_id!)}>Open session</button></div>}
      </article>})}
    </div>
    <div class="drawer-actions">
      <button disabled={busy||!open.length} onClick={dismissAll}>dismiss all</button>
      <button aria-pressed={showRead} onClick={()=>setShowRead(value=>!value)}>{showRead?'hide dismissed':'show dismissed'}</button>
    </div></>}
  </>
}
