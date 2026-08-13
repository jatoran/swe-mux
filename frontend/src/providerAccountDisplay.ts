// `resets_at` is a provider instant the daemon captured and stores, so every
// countdown here is aged on the daemon's clock (`serverNow`) rather than the
// browser's. A remote client a minute out of step would otherwise report a quota
// window as reset while the daemon still holds it throttled.
import { serverNow } from './serverClock.ts'

export type QuotaWindowDisplay={used_percent:number;resets_at?:number|null}
export type QuotaAccountDisplay={quota?:{status:string;error?:string|null;session?:QuotaWindowDisplay|null;weekly?:QuotaWindowDisplay|null;fable?:QuotaWindowDisplay|null}|null}

export const percent=(window?:QuotaWindowDisplay|null)=>window?`${Math.round(window.used_percent)}%`:'—'

export const accountAbbreviation=(label:string)=>[...label.trim()].slice(0,4).join('').toUpperCase()||'—'

export const formatResetRemaining=(resetsAt?:number|null,nowSeconds=serverNow())=>{
  if(!resetsAt)return ''
  const seconds=Math.max(0,Math.ceil(resetsAt-nowSeconds))
  if(seconds<60)return '<1m'
  const minutes=Math.ceil(seconds/60),days=Math.floor(minutes/1440),hours=Math.floor((minutes%1440)/60)
  if(days)return `${days}d${hours}h`
  const mins=minutes%60
  return hours?`${hours}h${mins}m`:`${minutes}m`
}

export const quotaWindowSummary=(window?:QuotaWindowDisplay|null,nowSeconds=serverNow())=>window?`${percent(window)}${window.resets_at?` ${formatResetRemaining(window.resets_at,nowSeconds)}`:''}`:'—'
export const quotaSummary=(account?:QuotaAccountDisplay,nowSeconds=serverNow())=>account?.quota?.status==='error'?'unavailable':`${quotaWindowSummary(account?.quota?.session,nowSeconds)} - ${quotaWindowSummary(account?.quota?.weekly,nowSeconds)}${account?.quota?.fable?` · ${percent(account.quota.fable)} Fable`:''}`

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

export type QuotaGridSegment={key:'session'|'weekly'|'fable';heading:string;text:string;band:UsageBand}

/** The shared desktop/mobile grid: reset or Fable heading, then usage percentage.
 *
 * Only windows the provider reports get columns. The visible percentage signs make
 * every value self-describing, so an unavailable session window does not need a dash
 * placeholder before the weekly value.
 */
export function quotaGridSegments(windows?:ProviderQuotaWindows|null,nowSeconds=serverNow()):QuotaGridSegment[]{
  const slots:Array<{key:QuotaGridSegment['key'];window:QuotaWindowDisplay|null}>=[
    {key:'session',window:windows?.session||null},
    {key:'weekly',window:windows?.weekly||null},
  ]
  if(windows?.fable)slots.push({key:'fable',window:windows.fable})
  return slots.filter((slot):slot is {key:QuotaGridSegment['key'];window:QuotaWindowDisplay}=>!!slot.window).map(slot=>({
    key:slot.key,
    heading:slot.key==='fable'?'Fable':formatResetRemaining(slot.window?.resets_at,nowSeconds)||'—',
    text:percent(slot.window),
    band:shownUsageBand(slot.window?.used_percent),
  }))
}

// A `chipUsageBand` once banded a chip by its *hottest* window so the border could escalate on
// a window the chip was not printing. It went out with the mobile toolbar's multi-window chip:
// every condensed indicator now draws exactly one number, and a border disagreeing with the
// digits beside it is the same contradiction `shownUsageBand` exists to prevent.

type PopoverRect={left:number;right:number;top:number;bottom:number}
export function anchoredPopoverStyle(rect:PopoverRect,compact:boolean,viewport:{width:number;height:number}):Record<string,string> {
  const width=Math.min(340,Math.max(240,viewport.width-16))
  const left=Math.max(8,Math.min(compact?rect.right-width:rect.left,viewport.width-width-8))
  if(compact)return {left:`${left}px`,top:`${Math.min(viewport.height-168,rect.bottom+4)}px`,width:`${width}px`,maxHeight:`${Math.max(160,viewport.height-rect.bottom-12)}px`}
  return {left:`${left}px`,bottom:`${Math.max(8,viewport.height-rect.top+4)}px`,width:`${width}px`,maxHeight:`${Math.max(160,rect.top-12)}px`}
}

export const accountPopoverStyle=anchoredPopoverStyle
