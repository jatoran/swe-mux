// Notifications live in the utility drawer, not a modal: they are something you
// glance at while working, and the modal made "check what fired" a full-screen
// interruption. The list is unchanged; only its host moved.

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

export function NotificationsTab({data,onOpenSession}:{
  data:NotificationData;onOpenSession:(sessionId:string)=>void
}) {
  const deliveries = new Map(data.deliveries.map(delivery=>[delivery.id,delivery]))
  const items = [...data.notifications].reverse()
  const automation=[...(data.automation||[])]
  return <>
    <p class="drawer-status">{items.length+automation.length} retained attention records</p>
    <div class="notification-list">
      {items.length===0&&automation.length===0&&<p class="notification-empty">No UI notifications yet. Universal hook notifications and attention records appear here.</p>}
      {automation.map(item=><article><span class={`state-dot ${item.severity==='warning'?'awaiting':'idle'}`}/><div><strong>{item.title}</strong><span>{item.message}</span><small>{new Date(item.created_at*1000).toLocaleString()} · automation::{item.kind}</small></div>{item.session_id&&<button onClick={()=>onOpenSession(item.session_id!)}>Open session</button>}</article>)}
      {items.map(item=>{const delivery=deliveries.get(item.delivery_id);return <article>
        <span class="state-dot idle" />
        <div><strong>{item.session_name||'daemon'} · {item.type.replaceAll('_',' ')}</strong><span>{new Date(item.ts*1000).toLocaleString()} · {item.channel}</span><small>delivery::{delivery?.status||'delivered'} · attempts::{delivery?.attempts??1} · correlation::{delivery?.correlation_id||item.seq||'—'}</small></div>
        {item.session_id&&<button onClick={()=>onOpenSession(item.session_id!)}>Open session</button>}
      </article>})}
    </div>
  </>
}
