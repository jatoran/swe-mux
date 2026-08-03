export type QuotaWindowDisplay={used_percent:number;resets_at?:number|null}
export type QuotaAccountDisplay={quota?:{status:string;error?:string|null;session?:QuotaWindowDisplay|null;weekly?:QuotaWindowDisplay|null;fable?:QuotaWindowDisplay|null}|null}

export const percent=(window?:QuotaWindowDisplay|null)=>window?`${Math.round(window.used_percent)}%`:'—'

export const formatResetRemaining=(resetsAt?:number|null,nowSeconds=Date.now()/1000)=>{
  if(!resetsAt)return ''
  const seconds=Math.max(0,Math.ceil(resetsAt-nowSeconds))
  if(seconds<60)return '<1m'
  const minutes=Math.ceil(seconds/60),days=Math.floor(minutes/1440),hours=Math.floor((minutes%1440)/60)
  if(days)return `${days}d${hours}h`
  const mins=minutes%60
  return hours?`${hours}h${mins}m`:`${minutes}m`
}

export const quotaWindowSummary=(window?:QuotaWindowDisplay|null,nowSeconds=Date.now()/1000)=>window?`${percent(window)}${window.resets_at?` ${formatResetRemaining(window.resets_at,nowSeconds)}`:''}`:'—'
export const quotaSummary=(account?:QuotaAccountDisplay,nowSeconds=Date.now()/1000)=>account?.quota?.status==='error'?'unavailable':`${quotaWindowSummary(account?.quota?.session,nowSeconds)} - ${quotaWindowSummary(account?.quota?.weekly,nowSeconds)}${account?.quota?.fable?` · fable ${percent(account.quota.fable)}`:''}`

export type ProviderQuotaWindows={session:QuotaWindowDisplay|null;weekly:QuotaWindowDisplay|null;fable:QuotaWindowDisplay|null}

/** A window the provider actually reported a number for; anything else reads as absent. */
const readableWindow=(window?:QuotaWindowDisplay|null)=>
  window&&typeof window.used_percent==='number'?window:null

/** Every quota window of each active account, keyed by provider.
 *
 * A failed poll invalidates the whole account rather than the one window that
 * errored, so all three come back null instead of showing a stale mix that reads
 * as current.
 */
export function providerQuotaWindows(
  accounts:Array<{provider:string;id:string}&QuotaAccountDisplay>,
  selected:Record<string,string|null>,
):Record<string,ProviderQuotaWindows>{
  const result:Record<string,ProviderQuotaWindows>={}
  for(const account of accounts){
    if(selected[account.provider]!==account.id)continue
    const quota=account.quota?.status==='error'?null:account.quota
    result[account.provider]={
      session:readableWindow(quota?.session),
      weekly:readableWindow(quota?.weekly),
      fable:readableWindow(quota?.fable),
    }
  }
  return result
}

/** Shared severity band so every condensed indicator colours the same way. */
export type UsageBand='unknown'|'ok'|'warn'|'critical'
export const usageBand=(percent?:number|null):UsageBand=>
  typeof percent!=='number'?'unknown':percent>=90?'critical':percent>=75?'warn':'ok'

/** Band the number the surface actually prints, not the one behind it.
 *
 * Every condensed indicator rounds before showing, so banding the raw value paints a
 * chip that reads `90` in the warn colour whenever the truth is 89.6 — the colour then
 * contradicts the digits beside it, and 90 is exactly where a user starts watching. */
export const shownUsageBand=(percent?:number|null):UsageBand=>
  usageBand(typeof percent==='number'?Math.round(percent):percent)

export type QuotaSegment={key:'session'|'weekly'|'fable';label:string;text:string;band:UsageBand}

/** The mobile toolbar chip's reading: 5h, then weekly, then fable when the plan has one.
 *
 * No percent signs. Every value here is a percentage, so the sign distinguishes
 * nothing while costing a third of the chip's width — and the chip has to carry two
 * or three numbers on a phone toolbar that also holds nav, the Project title, Run,
 * and the drawer toggle. A window the provider does not report keeps its slot and
 * reads `—` (Codex has no 5-hour window today, so it shows `—/74`), because with the
 * signs gone it is a number's *position* that says which window it is.
 */
export function quotaChipSegments(windows?:ProviderQuotaWindows|null):QuotaSegment[]{
  const slots:Array<{key:QuotaSegment['key'];label:string;window:QuotaWindowDisplay|null}>=[
    {key:'session',label:'5h',window:windows?.session||null},
    {key:'weekly',label:'weekly',window:windows?.weekly||null},
  ]
  if(windows?.fable)slots.push({key:'fable',label:'fable',window:windows.fable})
  return slots.map(slot=>({
    key:slot.key,
    label:slot.label,
    text:slot.window?String(Math.round(slot.window.used_percent)):'—',
    band:shownUsageBand(slot.window?.used_percent),
  }))
}

/** The chip's own band: its hottest window, so the border escalates even when the
 *  window that is burning is not the one being glanced at. */
export const chipUsageBand=(windows?:ProviderQuotaWindows|null):UsageBand=>{
  const values=[windows?.session,windows?.weekly,windows?.fable]
    .filter((window):window is QuotaWindowDisplay=>!!window)
    .map(window=>window.used_percent)
  return values.length?shownUsageBand(Math.max(...values)):'unknown'
}

type PopoverRect={left:number;right:number;top:number;bottom:number}
export function anchoredPopoverStyle(rect:PopoverRect,compact:boolean,viewport:{width:number;height:number}):Record<string,string> {
  const width=Math.min(340,Math.max(240,viewport.width-16))
  const left=Math.max(8,Math.min(compact?rect.right-width:rect.left,viewport.width-width-8))
  if(compact)return {left:`${left}px`,top:`${Math.min(viewport.height-168,rect.bottom+4)}px`,width:`${width}px`,maxHeight:`${Math.max(160,viewport.height-rect.bottom-12)}px`}
  return {left:`${left}px`,bottom:`${Math.max(8,viewport.height-rect.top+4)}px`,width:`${width}px`,maxHeight:`${Math.max(160,rect.top-12)}px`}
}

export const accountPopoverStyle=anchoredPopoverStyle
