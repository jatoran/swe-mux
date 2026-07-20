export type QuotaWindowDisplay={used_percent:number;resets_at?:number|null}
export type QuotaAccountDisplay={quota?:{status:string;error?:string|null;session?:QuotaWindowDisplay|null;weekly?:QuotaWindowDisplay|null}|null}

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
export const quotaSummary=(account?:QuotaAccountDisplay,nowSeconds=Date.now()/1000)=>account?.quota?.status==='error'?'unavailable':`${quotaWindowSummary(account?.quota?.session,nowSeconds)} - ${quotaWindowSummary(account?.quota?.weekly,nowSeconds)}`

type PopoverRect={left:number;right:number;top:number;bottom:number}
export function anchoredPopoverStyle(rect:PopoverRect,compact:boolean,viewport:{width:number;height:number}):Record<string,string> {
  const width=Math.min(340,Math.max(240,viewport.width-16))
  const left=Math.max(8,Math.min(compact?rect.right-width:rect.left,viewport.width-width-8))
  if(compact)return {left:`${left}px`,top:`${Math.min(viewport.height-168,rect.bottom+4)}px`,width:`${width}px`,maxHeight:`${Math.max(160,viewport.height-rect.bottom-12)}px`}
  return {left:`${left}px`,bottom:`${Math.max(8,viewport.height-rect.top+4)}px`,width:`${width}px`,maxHeight:`${Math.max(160,rect.top-12)}px`}
}

export const accountPopoverStyle=anchoredPopoverStyle
