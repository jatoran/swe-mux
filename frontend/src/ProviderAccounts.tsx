import { createPortal } from 'preact/compat'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { alertPreferences, setAlertPreferencesFor } from './alertPrefs'
import { currentProfile } from './deviceSettings'
import { setSoundPreferences, soundPreferences } from './sessionSounds'
import { accountAbbreviation, accountPopoverStyle, formatResetRemaining, percent, providerQuotaWindows, quotaGridSegments, quotaSummary, quotaWindowSummary, shownUsageBand, type QuotaWindowDisplay } from './providerAccountDisplay'
import { emitTutorialAction } from './tutorial'
import { harnessDisplayName } from './harnessRegistry'

export type ProviderName=string
type QuotaWindow={used_percent:number;window_minutes:number;resets_at?:number|null}
type AccountQuota={session?:QuotaWindow|null;weekly?:QuotaWindow|null;fable?:QuotaWindow|null;status:string;error?:string|null;refreshed_at?:number;attempted_at?:number;source?:string;plan?:string|null}
type IdentitySource='token'|'cli'|'file'
type AccountConflict={kind:'duplicate_account';provider_account_id:string;primary_id:string;is_primary:boolean;account_ids:string[]}
type MatchHint={account_id:string;label?:string|null;reason:string}
export type ProviderAccount={id:string;provider:ProviderName;label:string;email?:string|null;organization?:string|null;provider_account_id?:string|null;identity_source?:IdentitySource|null;identity_verified_at?:number|null;created_at:number;updated_at:number;quota?:AccountQuota|null;conflict?:AccountConflict|null}
type CurrentProviderAccount={state:'saved'|'external'|'signed_out'|'unreadable';account_id:string|null;email?:string|null;organization?:string|null;provider_account_id?:string|null;identity_source?:IdentitySource|null;match_hint?:MatchHint|null}
type ResetEvidence={id:string;provider:ProviderName;account_id:string;window:string;before_value:number;after_value:number;confirmed_at?:number}
/** Every unreviewed confirmed reset, not just the newest: one provider rollover lands on
 *  every account of that provider, and triaging them one at a time was N alerts per fact. */
type ResetAlert={count:number;items:ResetEvidence[]}
type ResetResolution='seen'|'manual_usage'|'discarded'
type ResetReview={items:ResetEvidence[];reset_alert:ResetAlert}
export type ProviderAccountsStatus={providers:ProviderName[];selected:Record<ProviderName,string|null>;current:Record<ProviderName,CurrentProviderAccount>;accounts:ProviderAccount[];poll_minutes:number;stale_minutes:number;refreshing:boolean;reset_alert?:ResetAlert}

const providerEvent='swe-mux:provider-accounts-changed'
const notifyChanged=()=>window.dispatchEvent(new Event(providerEvent))
const quotaTitle=(account?:ProviderAccount)=>account?.quota?.error||`5h ${quotaWindowSummary(account?.quota?.session)} · weekly ${quotaWindowSummary(account?.quota?.weekly)}${account?.quota?.fable?` · fable weekly ${percent(account.quota.fable)}`:''}`
const identityLabel=(current?:CurrentProviderAccount)=>current?.email||current?.organization||current?.provider_account_id||'unknown identity'
const currentLabel=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?.label||(current?.state==='external'?identityLabel(current):current?.state==='unreadable'?'login unreadable':'signed out')
const currentSummary=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?quotaSummary(account):current?.state==='external'?'external · not saved':current?.state==='unreadable'?'check credentials':'no active login'
const currentDescription=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?`System login: ${account.label}`:current?.state==='external'?`System login: ${identityLabel(current)} (external / unsaved)`:current?.state==='unreadable'?'System credentials exist but cannot be read.':'No system login detected.'
// A name read from the provider CLI's cached profile is not proof of who the
// token belongs to, so an unverified match is offered as a relink, never applied.
const hintDescription=(current?:CurrentProviderAccount)=>current?.match_hint?`Looks like “${current.match_hint.label}” by ${current.match_hint.reason}, but that is unverified; relink only if this really is that account.`:''
const conflictDescription=(account:ProviderAccount)=>account.conflict?account.conflict.is_primary?`Another saved slot holds these same credentials; its usage is a duplicate of this one.`:`Holds the same provider account as another saved slot, so its usage mirrors that one. Quota polling is suspended; re-authenticate or remove it.`:''
const identityNote=(account:ProviderAccount)=>account.identity_source==='token'?'verified with the provider':account.identity_source?'unverified identity':'identity unverified'
const claudeMark=<svg class="provider-mark" viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true"><path d="M12 2v20M2 12h20M4.9 4.9l14.2 14.2M19.1 4.9 4.9 19.1"/></svg>
const openaiMark=<svg class="provider-mark" viewBox="0 0 24 24" width="1em" height="1em" fill="currentColor" aria-hidden="true"><path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5962 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997z"/></svg>
export const providerGlyph=(provider:ProviderName)=>provider==='claude'?claudeMark:provider==='codex'?openaiMark:harnessDisplayName(provider).slice(0,1).toUpperCase()
const formatRefreshAge=(seconds?:number,nowSeconds=Date.now()/1000)=>{
  if(!seconds)return ''
  const delta=Math.max(0,Math.floor(nowSeconds-seconds))
  if(delta<60)return 'now'
  const minutes=Math.floor(delta/60)
  if(minutes<60)return `${minutes}m`
  const hours=Math.floor(minutes/60)
  if(hours<24)return `${hours}h`
  return `${Math.floor(hours/24)}d`
}

function useProviderAccounts(intervalMs=60_000) {
  const [status,setStatus]=useState<ProviderAccountsStatus|null>(null)
  const [error,setError]=useState('')
  const load=()=>api<ProviderAccountsStatus>('GET','/api/provider-accounts').then(value=>{setStatus(value);setError('')}).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
  useEffect(()=>{
    void load()
    // Skip while hidden and catch up on return, matching every other poll in the
    // app. Two or three of these mount at once (sidebar plus collapsed rail), so
    // a backgrounded tab was making several provider round-trips a minute for a
    // view nobody could see.
    const timer=window.setInterval(()=>{if(!document.hidden)void load()},intervalMs)
    const changed=()=>void load()
    const onVisible=()=>{if(!document.hidden)void load()}
    window.addEventListener(providerEvent,changed)
    document.addEventListener('visibilitychange',onVisible)
    return()=>{
      window.clearInterval(timer)
      window.removeEventListener(providerEvent,changed)
      document.removeEventListener('visibilitychange',onVisible)
    }
  },[intervalMs])
  return {status,setStatus,error,setError,load}
}

export function AccountSwitcher({variant='full',placement,onManage}:{
  // `variant` picks the trigger; `placement` is independent because the collapsed
  // desktop rail wants a condensed trigger with an upward-opening popover.
  variant?:'full'|'compact'|'rail';placement?:'up'|'down';onManage:()=>void
}) {
  const compact=variant!=='full'
  const {status,setStatus,error,setError}=useProviderAccounts()
  const [open,setOpen]=useState(false)
  const [busy,setBusy]=useState('')
  const [resetSound,setResetSound]=useState(()=>alertPreferences().enabled&&soundPreferences().enabled&&soundPreferences().events.reset)
  const root=useRef<HTMLDivElement>(null)
  const popover=useRef<HTMLDivElement>(null)
  const [popoverStyle,setPopoverStyle]=useState<Record<string,string>>({})
  const providers=status?.providers||[]
  const selected=(provider:ProviderName)=>status?.accounts.find(account=>account.id===status.selected[provider])
  // Unread is now purely the server's unreviewed set. It used to be a localStorage
  // marker, which is why dismissing at the desk left the same alert waiting on the phone.
  const resetItems=status?.reset_alert?.items||[]
  const resetProviders=useMemo(()=>[...new Set(resetItems.map(item=>item.provider))],[resetItems])
  const resetUnread=resetItems.length>0
  const toggleResetSound=()=>setResetSound(value=>{const next=!value,prefs=soundPreferences();if(next){const alerts=alertPreferences();setAlertPreferencesFor(currentProfile(),{...alerts,enabled:true})}setSoundPreferences({...prefs,enabled:next||prefs.enabled,events:{...prefs.events,reset:next}});return next})
  // One click resolves the whole group: three accounts observing one provider rollover
  // is one judgement, not three.
  const reviewResets=async(resolution:ResetResolution)=>{
    const ids=resetItems.map(item=>item.id)
    if(!ids.length)return
    setBusy(`reset-${resolution}`);setError('')
    try{
      const result=await api<ResetReview>('POST','/api/telemetry/quota-resets/review',{ids,resolution})
      setStatus(current=>current?{...current,reset_alert:result.reset_alert}:current)
      notifyChanged()
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  // Switching is never gated on live sessions: they re-read the shared
  // credential file and follow the switch, and the daemon defends it against a
  // straggling rotation from the outgoing login.
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
  const opensDown=placement?placement==='down':compact
  const position=()=>{const rect=root.current?.getBoundingClientRect();if(rect)setPopoverStyle(accountPopoverStyle(rect,opensDown,{width:window.innerWidth,height:window.innerHeight}))}
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
  },[open,opensDown])
  const popup=open&&<div ref={popover} class="account-popover ui-portal" style={popoverStyle} role="dialog" aria-label="Provider account switcher">
      <header><div><strong>ACCOUNTS</strong><span>session · weekly</span></div><button aria-label="Close account switcher" onClick={()=>setOpen(false)}>×</button></header>
      {providers.map(provider=>{const current=status?.current[provider];const active=selected(provider);return <section><h4>{provider}</h4>{current?.state!=='saved'&&<p class={`account-current-notice ${current?.state||'signed_out'}`}>{currentDescription(current,active)}{current?.match_hint?` ${hintDescription(current)}`:''}</p>}{status?.accounts.filter(account=>account.provider===provider).map(account=><button class={`${status.selected[provider]===account.id?'active':''} ${account.conflict&&!account.conflict.is_primary?'conflicted':''}`} disabled={!!busy} onClick={()=>void choose(account)} title={account.conflict?conflictDescription(account):quotaTitle(account)}><span>{status.selected[provider]===account.id?'◆':'◇'}</span><strong>{account.label}</strong><small>{account.conflict&&!account.conflict.is_primary?'duplicate account · polling suspended':<>{quotaSummary(account)}{account.quota?.refreshed_at?<i class="account-refresh-age" title={`Quotas refreshed ${new Date(account.quota.refreshed_at*1000).toLocaleString()}`}> · {formatRefreshAge(account.quota.refreshed_at)}</i>:''}</>}</small></button>)}{!status?.accounts.some(account=>account.provider===provider)&&<p>No saved accounts</p>}</section>})}
      {error&&<p class="account-error" role="alert">{error}</p>}
      {resetUnread&&<section class="account-reset-alert"><h4>quota reset evidence</h4><p>{resetItems.length===1?'One confirmed unexpected reset:':`${status?.reset_alert?.count??resetItems.length} confirmed unexpected resets · one provider rollover reaches every account on that plan:`}</p><ul>{resetItems.map(item=><li key={item.id}><strong>{item.provider} {item.window}</strong> · {status?.accounts.find(account=>account.id===item.account_id)?.label||item.account_id} · {item.before_value}% → {item.after_value}%</li>)}</ul><div>{resetProviders.length===1&&resetProviders[0]==='codex'&&<button disabled={!!busy} onClick={()=>void reviewResets('manual_usage')}>{busy==='reset-manual_usage'?'marking…':resetItems.length>1?'all manual Codex usage':'manual Codex usage'}</button>}<button class="danger" disabled={!!busy} onClick={()=>void reviewResets('discarded')}>{busy==='reset-discarded'?'discarding…':resetItems.length>1?'discard all as errors':'discard as error'}</button><button disabled={!!busy} onClick={()=>void reviewResets('seen')}>{busy==='reset-seen'?'marking…':'mark seen'}</button><button disabled={!!busy} onClick={toggleResetSound}>{resetSound?'mute reset sound':'enable reset sound'}</button></div></section>}
      <footer><button disabled={!!busy} onClick={()=>void refresh()}>{busy==='refresh'?'refreshing…':'refresh quotas'}</button><button onClick={()=>{setOpen(false);onManage()}}>manage…</button></footer>
    </div>
  const quotas=providerQuotaWindows(status?.accounts||[],status?.selected||{})
  const weeklyTitle=(provider:ProviderName)=>{
    const window=quotas[provider]?.weekly
    if(!window)return `${provider} · weekly quota unavailable · open accounts`
    const remaining=formatResetRemaining(window.resets_at)
    return `${provider} weekly ${Math.round(window.used_percent)}% used${remaining?` · resets in ${remaining}`:''} · open accounts`
  }
  // The tooltip is also the chip's accessible name, so it expands the compact visual grid
  // into named windows and reset relationships for screen readers.
  const toolbarTitle=(provider:ProviderName)=>{
    const windows=quotas[provider]
    if(!windows)return `${provider} · quota unavailable · open accounts`
    const part=(label:string,window:QuotaWindowDisplay|null)=>{
      if(!window)return `${label} not reported`
      const remaining=formatResetRemaining(window.resets_at)
      return `${label} ${Math.round(window.used_percent)}% used${remaining?` (resets in ${remaining})`:''}`
    }
    const parts=[part('5h',windows.session),part('weekly',windows.weekly)]
    if(windows.fable)parts.push(part('fable',windows.fable))
    return `${provider} · ${parts.join(' · ')} · open accounts`
  }
  const quotaGrid=(provider:ProviderName)=>{
    const account=selected(provider)
    const current=status?.current[provider]
    const segments=quotaGridSegments(quotas[provider])
    return <span class={`quota-grid quota-grid-${segments.length}`}>
      <span class="quota-grid-column quota-grid-identity">
        <i class={`provider-glyph ${provider}`} aria-hidden="true">{providerGlyph(provider)}</i>
        <strong class="quota-account" title={currentLabel(current,account)}>{accountAbbreviation(currentLabel(current,account))}</strong>
      </span>
      {segments.map(segment=><span class="quota-grid-column quota-grid-metric" key={segment.key}>
        <small>{segment.heading}</small>
        <i class={`quota-window usage-${segment.band}`}>{segment.text}</i>
      </span>)}
    </span>
  }
  // One square per provider: the glyph above a single weekly percentage. Both condensed
  // surfaces — the collapsed sidebar rail and the mobile toolbar — wear it, and it is banded by
  // the number it prints rather than by its hottest window, or the digits would recolour to
  // contradict themselves. The caller supplies the tooltip because it is also the accessible
  // name, and the toolbar names every window there even though it draws only one.
  const quotaChip=(provider:ProviderName,title:string)=>{
    const weekly=quotas[provider]?.weekly||null
    return <button key={provider} class={`rail-quota usage-${shownUsageBand(weekly?.used_percent)} ${resetProviders.includes(provider)?'quota-reset-unread':''}`} aria-label={title} aria-expanded={open} title={title} onClick={toggle}>
      <span class={`provider-glyph ${provider}`} aria-hidden="true">{providerGlyph(provider)}</span><strong>{weekly?`${Math.round(weekly.used_percent)}%`:'—'}</strong>
    </button>
  }
  return <div ref={root} class={`account-switcher ${compact?'compact':''} ${variant==='rail'?'rail':''}`}>
    {variant==='rail'?providers.map(provider=>quotaChip(provider,weeklyTitle(provider)))
    // The mobile toolbar carries one chip per provider, in the same provider order as every
    // other surface — collapsing both into whichever weekly window was furthest along hid which
    // provider was burning. It wears the same square as the collapsed rail rather than the
    // expanded sidebar's usage breakdown: that breakdown is three columns of numbers competing
    // with the Project name and two run controls for one 44px row, and the number a phone is
    // glanced at for is how much of the week is gone. The breakdown is one tap away, in the
    // popover this chip opens, which is also where the tooltip's window-by-window text lands
    // for a device that cannot hover.
    :variant==='compact'?providers.map(provider=>quotaChip(provider,toolbarTitle(provider)))
    :<div class="account-summary">
      {providers.map(provider=>{const account=selected(provider);const current=status?.current[provider];const state=account?account.quota?.status||'pending':current?.state||'loading';return <button class={account?'tracked':current?.state==='external'?'external':'untracked'} aria-label={`${provider} account: ${currentLabel(current,account)}; ${currentSummary(current,account)}; ${state}`} aria-expanded={open} onClick={toggle} title={`${provider} · ${account?quotaTitle(account):currentDescription(current,account)}`}>{quotaGrid(provider)}{state!=='ready'&&<em>{state}</em>}</button>})}
      {resetUnread&&<button class="quota-reset-indicator" title="Confirmed unexpected reset; open for evidence" onClick={show}><span>RESET</span><strong>{resetItems.length>1?`${resetItems.length} unexpected quota resets`:'unexpected quota reset'}</strong><small>{resetProviders.join(' · ')}</small></button>}
    </div>}
    {popup&&createPortal(popup,document.body)}
  </div>
}

export function AccountSettings() {
  const {status,setStatus,error,setError}=useProviderAccounts(120_000)
  const [busy,setBusy]=useState('')
  const [message,setMessage]=useState('')
  const [labels,setLabels]=useState<Record<ProviderName,string>>({})
  const [confirmRemove,setConfirmRemove]=useState('')
  const providers=status?.providers||[]
  const grouped=useMemo(()=>Object.fromEntries(providers.map(provider=>[provider,status?.accounts.filter(account=>account.provider===provider)||[]])),[status,providers.join('\0')])
  const mutate=async(key:string,method:string,path:string,body?:unknown,tutorialAction=false)=>{
    setBusy(key);setError('');setMessage('')
    try{const next=await api<ProviderAccountsStatus>(method,path,body);setStatus(next);notifyChanged();setMessage('Account state updated.');if(tutorialAction)emitTutorialAction({action:'account-saved'})}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  const selectAccount=async(account:ProviderAccount)=>{
    setBusy(account.id);setError('');setMessage('')
    try{const next=await api<ProviderAccountsStatus>('POST',`/api/provider-accounts/${account.provider}/${account.id}/select`,{});setStatus(next);notifyChanged();setMessage('Account state updated.')}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  const add=(provider:ProviderName,login:boolean)=>void mutate(`${provider}-${login?'login':'capture'}`,'POST',`/api/provider-accounts/${provider}/${login?'login':'capture'}`,{label:labels[provider]||undefined},true)
  const reauthenticate=(account:ProviderAccount)=>void mutate(account.id,'POST',`/api/provider-accounts/${account.provider}/login`,{replace_id:account.id,label:account.label})
  const rename=(account:ProviderAccount,label:string)=>{if(label.trim()&&label.trim()!==account.label)void mutate(account.id,'PATCH',`/api/provider-accounts/${account.provider}/${account.id}`,{label})}
  const remove=(account:ProviderAccount)=>{if(confirmRemove!==account.id){setConfirmRemove(account.id);return}setConfirmRemove('');void mutate(account.id,'DELETE',`/api/provider-accounts/${account.provider}/${account.id}`)}
  return <section data-tutorial="provider-accounts" class="account-settings"><h3>Provider accounts</h3><p>Switching replaces only the provider's system authentication file. Global config, skills, projects, and histories remain shared. Sessions already running follow the switch too — they re-read that file — so switching is never blocked or confirmed. The switch also restores the account's cached CLI profile, so <code>/status</code> in new sessions names the right account immediately; panes already running keep the old display until restarted.</p>
    {providers.map(provider=>{const current=status?.current[provider];const accounts=grouped[provider]||[];const active=accounts.find(account=>account.id===current?.account_id);return <div class="account-provider-settings"><header><div><strong>{provider.toUpperCase()}</strong><small>{accounts.length} saved · quotas refresh every {status?.poll_minutes||15} minutes</small></div><div class="account-actions"><button disabled={!!busy} onClick={()=>void mutate('verify','POST','/api/provider-accounts/verify',{})}>{busy==='verify'?'verifying…':'verify identities'}</button><button disabled={!!busy} onClick={()=>void mutate('refresh','POST','/api/provider-accounts/refresh',{})}>refresh quotas</button></div></header>
      <div class={`account-current ${current?.state||'signed_out'}`}><span>LIVE SYSTEM AUTH</span><strong>{currentDescription(current,active)}</strong><small>swe-mux follows the daemon host credentials; startup never restores an older saved account. Credentials move into a saved account only on a provider-verified identity or an explicit relink.</small>{current?.match_hint&&<p class="account-relink"><span>{hintDescription(current)}</span><button disabled={!!busy} onClick={()=>void mutate(`adopt-${provider}`,'POST',`/api/provider-accounts/${provider}/${current.match_hint!.account_id}/adopt`)}>{busy===`adopt-${provider}`?'relinking…':`relink to ${current.match_hint.label}`}</button></p>}</div>
      <div data-tutorial="provider-account-actions" class="account-add"><input aria-label={`New ${provider} account label`} placeholder="optional label" value={labels[provider]||''} onInput={event=>setLabels(value=>({...value,[provider]:event.currentTarget.value}))}/><button class="primary" disabled={!!busy} onClick={()=>add(provider,true)}>{busy===`${provider}-login`?'waiting for sign-in…':'sign in + save'}</button><button disabled={!!busy} onClick={()=>add(provider,false)}>{busy===`${provider}-capture`?'saving…':'save current login'}</button></div>
      <p class="account-help">“Sign in + save” launches <code>{provider==='claude'?'claude auth login --claudeai':'codex login'}</code> on the daemon host. “Save current login” captures an account you signed into separately.</p>
      <div class="account-list">{accounts.map(account=><article class={`${status?.selected[provider]===account.id?'active':''} ${account.conflict?'conflicted':''}`}><span class="account-state">{status?.selected[provider]===account.id?'◆ active':'◇ saved'}</span><input aria-label={`${provider} account label`} defaultValue={account.label} onBlur={event=>rename(account,event.currentTarget.value)}/><small>{account.email||account.organization||account.provider_account_id||'identity unavailable'}<i class={`account-identity ${account.identity_source==='token'?'verified':'unverified'}`} title={account.identity_source==='token'?'Identity confirmed by asking the provider with these credentials.':'Identity has not been confirmed against the provider yet.'}>{identityNote(account)}</i></small><div class="account-quota" title={quotaTitle(account)}><span>session <b>{percent(account.quota?.session)}</b></span><span>weekly <b>{percent(account.quota?.weekly)}</b></span>{account.quota?.fable&&<span>fable <b>{percent(account.quota.fable)}</b></span>}<em>{account.quota?.status||'pending'}{account.quota?.error?` · ${account.quota.error}`:''}</em></div><div class="account-actions"><button disabled={!!busy||status?.selected[provider]===account.id} onClick={()=>void selectAccount(account)}>use</button><button disabled={!!busy} onClick={()=>reauthenticate(account)}>sign in again</button><button class={confirmRemove===account.id?'danger confirming':'danger'} disabled={!!busy} onClick={()=>remove(account)}>{confirmRemove===account.id?'confirm remove':'remove'}</button></div>{account.conflict&&<p class="account-conflict" role="alert">{conflictDescription(account)}</p>}</article>)}{!accounts.length&&<div class="account-empty">No {provider} accounts saved yet.</div>}</div>
    </div>})}
    {(error||message)&&<p class={error?'settings-inline-error':''} role={error?'alert':'status'}>{error||message}</p>}
  </section>
}
