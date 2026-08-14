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
import { useState } from 'preact/hooks'
import { api } from './api'
import { AttentionInbox } from './AttentionInbox'

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

export function NotificationsTab({data,onOpenSession,onChanged}:{
  data:NotificationData;onOpenSession:(sessionId:string)=>void;onChanged:()=>void
}) {
  const [showRead,setShowRead]=useState(false)
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
    <AttentionInbox onOpenSession={onOpenSession} />
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
    </div>
  </>
}
