import { createPortal } from 'preact/compat'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { setSoundPreferences, soundPreferences } from './sessionSounds'
import { accountPopoverStyle, percent, quotaSummary, quotaWindowSummary } from './providerAccountDisplay'

export type ProviderName='claude'|'codex'
type QuotaWindow={used_percent:number;window_minutes:number;resets_at?:number|null}
type AccountQuota={session?:QuotaWindow|null;weekly?:QuotaWindow|null;status:string;error?:string|null;refreshed_at?:number;attempted_at?:number;source?:string;plan?:string|null}
export type ProviderAccount={id:string;provider:ProviderName;label:string;email?:string|null;organization?:string|null;provider_account_id?:string|null;created_at:number;updated_at:number;quota?:AccountQuota|null}
type CurrentProviderAccount={state:'saved'|'external'|'signed_out'|'unreadable';account_id:string|null;email?:string|null;organization?:string|null;provider_account_id?:string|null}
type ResetAlert={count:number;latest?:{id:string;provider:ProviderName;account_id:string;window:string;before_value:number;after_value:number;confirmed_at?:number}|null}
export type ProviderAccountsStatus={providers:ProviderName[];selected:Record<ProviderName,string|null>;current:Record<ProviderName,CurrentProviderAccount>;accounts:ProviderAccount[];poll_minutes:number;stale_minutes:number;refreshing:boolean;reset_alert?:ResetAlert}

const providerEvent='swe-mux:provider-accounts-changed'
const notifyChanged=()=>window.dispatchEvent(new Event(providerEvent))
const quotaTitle=(account?:ProviderAccount)=>account?.quota?.error||`5h ${quotaWindowSummary(account?.quota?.session)} · weekly ${quotaWindowSummary(account?.quota?.weekly)}`
const identityLabel=(current?:CurrentProviderAccount)=>current?.email||current?.organization||current?.provider_account_id||'unknown identity'
const currentLabel=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?.label||(current?.state==='external'?identityLabel(current):current?.state==='unreadable'?'login unreadable':'signed out')
const currentSummary=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?quotaSummary(account):current?.state==='external'?'external · not saved':current?.state==='unreadable'?'check credentials':'no active login'
const currentDescription=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?`System login: ${account.label}`:current?.state==='external'?`System login: ${identityLabel(current)} (external / unsaved)`:current?.state==='unreadable'?'System credentials exist but cannot be read.':'No system login detected.'
const providerGlyph=(provider:ProviderName)=>provider==='claude'?'✳':'⌬'

function useProviderAccounts(intervalMs=60_000) {
  const [status,setStatus]=useState<ProviderAccountsStatus|null>(null)
  const [error,setError]=useState('')
  const load=()=>api<ProviderAccountsStatus>('GET','/api/provider-accounts').then(value=>{setStatus(value);setError('')}).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
  useEffect(()=>{
    void load()
    const timer=window.setInterval(()=>void load(),intervalMs)
    const changed=()=>void load()
    window.addEventListener(providerEvent,changed)
    return()=>{window.clearInterval(timer);window.removeEventListener(providerEvent,changed)}
  },[intervalMs])
  return {status,setStatus,error,setError,load}
}

export function AccountSwitcher({compact=false,onManage}:{compact?:boolean;onManage:()=>void}) {
  const {status,setStatus,error,setError}=useProviderAccounts()
  const [open,setOpen]=useState(false)
  const [busy,setBusy]=useState('')
  const [resetUnread,setResetUnread]=useState(false)
  const [resetSound,setResetSound]=useState(()=>soundPreferences().enabled&&soundPreferences().events.reset)
  const root=useRef<HTMLDivElement>(null)
  const popover=useRef<HTMLDivElement>(null)
  const [popoverStyle,setPopoverStyle]=useState<Record<string,string>>({})
  const selected=(provider:ProviderName)=>status?.accounts.find(account=>account.id===status.selected[provider])
  const latestReset=status?.reset_alert?.latest
  useEffect(()=>{
    if(!latestReset?.id)return
    const seen=localStorage.getItem('swe-mux:last-seen-reset')
    if(seen===latestReset.id)return
    setResetUnread(true)
  },[latestReset?.id])
  const dismissReset=()=>{if(latestReset?.id)localStorage.setItem('swe-mux:last-seen-reset',latestReset.id);setResetUnread(false)}
  const toggleResetSound=()=>setResetSound(value=>{const next=!value,prefs=soundPreferences();setSoundPreferences({...prefs,enabled:next||prefs.enabled,events:{...prefs.events,reset:next}});return next})
  const choose=async(account:ProviderAccount)=>{
    setBusy(account.id);setError('')
    try{const next=await api<ProviderAccountsStatus>('POST',`/api/provider-accounts/${account.provider}/${account.id}/select`,{});setStatus(next);notifyChanged()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  const refresh=async()=>{
    setBusy('refresh');setError('')
    try{const next=await api<ProviderAccountsStatus>('POST','/api/provider-accounts/refresh',{});setStatus(next);notifyChanged()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  const position=()=>{const rect=root.current?.getBoundingClientRect();if(rect)setPopoverStyle(accountPopoverStyle(rect,compact,{width:window.innerWidth,height:window.innerHeight}))}
  const toggle=()=>{if(!open)position();setOpen(value=>!value)}
  const show=()=>{position();setOpen(true)}
  useEffect(()=>{
    if(!open)return
    position()
    const reposition=()=>position()
    const dismiss=(event:PointerEvent)=>{const target=event.target as Node;if(!root.current?.contains(target)&&!popover.current?.contains(target))setOpen(false)}
    const key=(event:KeyboardEvent)=>{if(event.key==='Escape')setOpen(false)}
    window.addEventListener('resize',reposition);window.addEventListener('scroll',reposition,true);window.addEventListener('pointerdown',dismiss);window.addEventListener('keydown',key)
    return()=>{window.removeEventListener('resize',reposition);window.removeEventListener('scroll',reposition,true);window.removeEventListener('pointerdown',dismiss);window.removeEventListener('keydown',key)}
  },[open,compact])
  const popup=open&&<div ref={popover} class="account-popover account-popover-portal" style={popoverStyle} role="dialog" aria-label="Provider account switcher">
      <header><div><strong>ACCOUNTS</strong><span>session · weekly</span></div><button aria-label="Close account switcher" onClick={()=>setOpen(false)}>×</button></header>
      {(['claude','codex'] as ProviderName[]).map(provider=>{const current=status?.current[provider];const active=selected(provider);return <section><h4>{provider}</h4>{current?.state!=='saved'&&<p class={`account-current-notice ${current?.state||'signed_out'}`}>{currentDescription(current,active)}</p>}{status?.accounts.filter(account=>account.provider===provider).map(account=><button class={status.selected[provider]===account.id?'active':''} disabled={!!busy} onClick={()=>void choose(account)} title={quotaTitle(account)}><span>{status.selected[provider]===account.id?'◆':'◇'}</span><strong>{account.label}</strong><small>{quotaSummary(account)}</small></button>)}{!status?.accounts.some(account=>account.provider===provider)&&<p>No saved accounts</p>}</section>})}
      {error&&<p class="account-error" role="alert">{error}</p>}
      {latestReset&&<section class="account-reset-alert"><h4>quota reset evidence</h4><p><strong>{latestReset.provider} {latestReset.window}</strong> moved {latestReset.before_value}% → {latestReset.after_value}% and was confirmed by a second fresh sample.</p><div><button onClick={dismissReset}>{resetUnread?'mark seen':'seen'}</button><button onClick={toggleResetSound}>{resetSound?'mute reset sound':'enable reset sound'}</button></div></section>}
      <footer><button disabled={!!busy} onClick={()=>void refresh()}>{busy==='refresh'?'refreshing…':'refresh quotas'}</button><button onClick={()=>{setOpen(false);onManage()}}>manage…</button></footer>
    </div>
  return <div ref={root} class={`account-switcher ${compact?'compact':''}`}>
    {compact?<button class={`account-mobile-trigger ${resetUnread?'quota-reset-unread':''}`} aria-label="Provider accounts" aria-expanded={open} title={resetUnread?'Confirmed unexpected quota reset':'Provider accounts'} onClick={toggle}>acct{resetUnread?'!':''}</button>:<div class="account-summary">
      {(['claude','codex'] as ProviderName[]).map(provider=>{const account=selected(provider);const current=status?.current[provider];const state=account?account.quota?.status||'pending':current?.state||'loading';return <button class={account?'tracked':current?.state==='external'?'external':'untracked'} aria-label={`${provider} account: ${currentLabel(current,account)}; ${currentSummary(current,account)}; ${state}`} aria-expanded={open} onClick={toggle} title={`${provider} · ${account?quotaTitle(account):currentDescription(current,account)}`}><span class={`provider-glyph ${provider}`} aria-hidden="true">{providerGlyph(provider)}</span><strong>{currentLabel(current,account)}</strong><small>{currentSummary(current,account)}</small><em>{state}</em></button>})}
      {resetUnread&&<button class="quota-reset-indicator" title="Confirmed unexpected reset; open for evidence" onClick={show}><span>RESET</span><strong>unexpected quota reset</strong><small>{latestReset?.provider} · {latestReset?.window}</small></button>}
    </div>}
    {popup&&createPortal(popup,document.body)}
  </div>
}

export function AccountSettings() {
  const {status,setStatus,error,setError}=useProviderAccounts(120_000)
  const [busy,setBusy]=useState('')
  const [message,setMessage]=useState('')
  const [labels,setLabels]=useState<Record<ProviderName,string>>({claude:'',codex:''})
  const [confirmRemove,setConfirmRemove]=useState('')
  const grouped=useMemo(()=>({claude:status?.accounts.filter(account=>account.provider==='claude')||[],codex:status?.accounts.filter(account=>account.provider==='codex')||[]}),[status])
  const mutate=async(key:string,method:string,path:string,body?:unknown)=>{
    setBusy(key);setError('');setMessage('')
    try{const next=await api<ProviderAccountsStatus>(method,path,body);setStatus(next);notifyChanged();setMessage('Account state updated.')}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  const add=(provider:ProviderName,login:boolean)=>void mutate(`${provider}-${login?'login':'capture'}`,'POST',`/api/provider-accounts/${provider}/${login?'login':'capture'}`,{label:labels[provider]||undefined})
  const reauthenticate=(account:ProviderAccount)=>void mutate(account.id,'POST',`/api/provider-accounts/${account.provider}/login`,{replace_id:account.id,label:account.label})
  const rename=(account:ProviderAccount,label:string)=>{if(label.trim()&&label.trim()!==account.label)void mutate(account.id,'PATCH',`/api/provider-accounts/${account.provider}/${account.id}`,{label})}
  const remove=(account:ProviderAccount)=>{if(confirmRemove!==account.id){setConfirmRemove(account.id);return}setConfirmRemove('');void mutate(account.id,'DELETE',`/api/provider-accounts/${account.provider}/${account.id}`)}
  return <section class="account-settings"><h3>Provider accounts</h3><p>Switching replaces only the provider's system authentication file. Global config, skills, projects, and histories remain shared. Existing Claude and Codex processes observe the same system-wide login.</p>
    {(['claude','codex'] as ProviderName[]).map(provider=>{const current=status?.current[provider];const active=grouped[provider].find(account=>account.id===current?.account_id);return <div class="account-provider-settings"><header><div><strong>{provider.toUpperCase()}</strong><small>{grouped[provider].length} saved · quotas refresh every {status?.poll_minutes||15} minutes</small></div><button disabled={!!busy} onClick={()=>void mutate('refresh','POST','/api/provider-accounts/refresh',{})}>refresh quotas</button></header>
      <div class={`account-current ${current?.state||'signed_out'}`}><span>LIVE SYSTEM AUTH</span><strong>{currentDescription(current,active)}</strong><small>swe-mux follows the daemon host credentials; startup never restores an older saved account.</small></div>
      <div class="account-add"><input aria-label={`New ${provider} account label`} placeholder="optional label" value={labels[provider]} onInput={event=>setLabels(value=>({...value,[provider]:event.currentTarget.value}))}/><button class="primary" disabled={!!busy} onClick={()=>add(provider,true)}>{busy===`${provider}-login`?'waiting for sign-in…':'sign in + save'}</button><button disabled={!!busy} onClick={()=>add(provider,false)}>{busy===`${provider}-capture`?'saving…':'save current login'}</button></div>
      <p class="account-help">“Sign in + save” launches <code>{provider==='claude'?'claude auth login --claudeai':'codex login'}</code> on the daemon host. “Save current login” captures an account you signed into separately.</p>
      <div class="account-list">{grouped[provider].map(account=><article class={status?.selected[provider]===account.id?'active':''}><span class="account-state">{status?.selected[provider]===account.id?'◆ active':'◇ saved'}</span><input aria-label={`${provider} account label`} defaultValue={account.label} onBlur={event=>rename(account,event.currentTarget.value)}/><small>{account.email||account.organization||account.provider_account_id||'identity unavailable'}</small><div class="account-quota" title={quotaTitle(account)}><span>session <b>{percent(account.quota?.session)}</b></span><span>weekly <b>{percent(account.quota?.weekly)}</b></span><em>{account.quota?.status||'pending'}{account.quota?.error?` · ${account.quota.error}`:''}</em></div><div class="account-actions"><button disabled={!!busy||status?.selected[provider]===account.id} onClick={()=>void mutate(account.id,'POST',`/api/provider-accounts/${provider}/${account.id}/select`,{})}>use</button><button disabled={!!busy} onClick={()=>reauthenticate(account)}>sign in again</button><button class={confirmRemove===account.id?'danger confirming':'danger'} disabled={!!busy} onClick={()=>remove(account)}>{confirmRemove===account.id?'confirm remove':'remove'}</button></div></article>)}{!grouped[provider].length&&<div class="account-empty">No {provider} accounts saved yet.</div>}</div>
    </div>})}
    {(error||message)&&<p class={error?'settings-inline-error':''} role={error?'alert':'status'}>{error||message}</p>}
  </section>
}
