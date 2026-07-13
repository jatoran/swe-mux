import { useRef } from 'preact/hooks'
import { useModalFocus } from './modalFocus'

export type UiNotification = {
  ts:number;channel:string;delivery_id:string;session_id?:string
  session_name?:string;type:string;source?:string;seq?:number
}

export type Delivery = {
  id:string;correlation_id:string;created_at:number;updated_at:number
  provider:string;channel:string;status:string;attempts:number
  session_id?:string;sender:string;reply_target?:string;event_type:string
}

export type NotificationData = {notifications:UiNotification[];deliveries:Delivery[]}

export function Notifications({data,onClose,onOpenSession}:{
  data:NotificationData;onClose:()=>void;onOpenSession:(sessionId:string)=>void
}) {
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)
  const deliveries = new Map(data.deliveries.map(delivery=>[delivery.id,delivery]))
  const items = [...data.notifications].reverse()
  return <div class="notifications-layer" onPointerDown={event=>{if(event.target===event.currentTarget)onClose()}}>
    <section ref={panel} class="notifications-panel" role="dialog" aria-modal="true" aria-label="Notifications">
      <header><div><span>EVENTS::NOTIFICATIONS</span><strong>{items.length} retained this daemon run</strong></div><button aria-label="Close notifications" onClick={onClose}>×</button></header>
      <div class="notification-list">
        {items.length===0&&<p class="notification-empty">No UI notifications yet. Matching meta-hook notify actions appear here.</p>}
        {items.map(item=>{const delivery=deliveries.get(item.delivery_id);return <article>
          <span class="state-dot idle" />
          <div><strong>{item.session_name||'daemon'} · {item.type.replaceAll('_',' ')}</strong><span>{new Date(item.ts*1000).toLocaleString()} · {item.channel}</span><small>delivery::{delivery?.status||'delivered'} · attempts::{delivery?.attempts??1} · correlation::{delivery?.correlation_id||item.seq||'—'}</small></div>
          {item.session_id&&<button onClick={()=>onOpenSession(item.session_id!)}>Open session</button>}
        </article>})}
      </div>
    </section>
  </div>
}
