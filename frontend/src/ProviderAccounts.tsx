import { createPortal } from 'preact/compat'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { alertPreferences, setAlertPreferencesFor } from './alertPrefs'
import { currentProfile } from './deviceSettings'
import { setSoundPreferences, soundPreferences } from './sessionSounds'
import { accountAbbreviation, accountPopoverStyle, formatResetRemaining, hasFableWindow, loginOf, loginRunning, signInTitle, percent, providerQuotaWindows, quotaGridSegments, quotaRowCells, quotaSummary, quotaWindowSummary, shownUsageBand, spawnedSessionCount, strandedSessionNotice, strandedSessions, visibleProviders, type LoginDisplay, type QuotaWindowDisplay, type SessionCountsDisplay } from './providerAccountDisplay'
import { serverNow } from './serverClock.ts'
import { emitTutorialAction } from './tutorial'
// Provider marks live in `harnessIcons.tsx`, which every surface naming a harness reads. This
// module used to own them, which is how the two harnesses that have provider accounts came to
// be the only two with a drawing at all.
import { harnessMark } from './harnessIcons'
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
/** A sign-in the daemon is running, or how the last one ended. It lives on the server
 *  because the thing it describes is a provider CLI the daemon owns for up to five
 *  minutes: the tab that started it may close, reload, or be a phone, and every other
 *  client should still see the same one. */
type LoginState=LoginDisplay
/** Live sessions grouped by the account they were **spawned under**, counted by the
 *  daemon. Not "sessions using this account": mux stamps what it had selected when the
 *  process started, and cannot see a `/login` typed inside a pane. */
type AccountSessionCounts=SessionCountsDisplay
export type ProviderAccountsStatus={providers:ProviderName[];selected:Record<ProviderName,string|null>;current:Record<ProviderName,CurrentProviderAccount>;accounts:ProviderAccount[];poll_minutes:number;stale_minutes:number;refreshing:boolean;reset_alert?:ResetAlert;login?:Record<ProviderName,LoginState|null>;login_commands?:Record<ProviderName,string>;switch_reaches_live?:Record<ProviderName,boolean>;sessions?:AccountSessionCounts}

const providerEvent='swe-mux:provider-accounts-changed'
const notifyChanged=()=>window.dispatchEvent(new Event(providerEvent))
// A running sign-in is the one thing here that resolves on human time rather than on
// the poll's, so it gets its own cadence instead of leaving the outcome up to a minute
// stale. It reverts the moment nothing is running.
const LOGIN_POLL_MS=3_000
// The cadence for an install with no provider configured, where the only thing
// that can change this payload is a login run somewhere else entirely.
const IDLE_POLL_MS=300_000
const quotaTitle=(account?:ProviderAccount)=>account?.quota?.error||`5h ${quotaWindowSummary(account?.quota?.session)} · weekly ${quotaWindowSummary(account?.quota?.weekly)}${account?.quota?.fable?` · fable weekly ${percent(account.quota.fable)}`:''}`
const identityLabel=(current?:CurrentProviderAccount)=>current?.email||current?.organization||current?.provider_account_id||'unknown identity'
const currentLabel=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?.label||(current?.state==='external'?identityLabel(current):current?.state==='unreadable'?'login unreadable':'signed out')
const currentSummary=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?quotaSummary(account):current?.state==='external'?'external · not saved':current?.state==='unreadable'?'check credentials':'no active login'
const currentDescription=(current?:CurrentProviderAccount,account?:ProviderAccount)=>account?`System login: ${account.label}`:current?.state==='external'?`System login: ${identityLabel(current)} (external / unsaved)`:current?.state==='unreadable'?'System credentials exist but cannot be read.':'No system login detected.'
// A name read from the provider CLI's cached profile is not proof of who the
// token belongs to, so an unverified match is offered as a relink, never applied.
const hintDescription=(current?:CurrentProviderAccount)=>current?.match_hint?`Looks like “${current.match_hint.label}” by ${current.match_hint.reason}, but that is unverified; relink only if this really is that account.`:''
const conflictDescription=(account:ProviderAccount)=>account.conflict?account.conflict.is_primary?`Another saved slot holds these same credentials; its usage is a duplicate of this one.`:`Holds the same provider account as another saved slot, so its usage mirrors that one. Quota polling is suspended; re-authenticate or remove it.`:''
// Two states, so two words. This used to return three different strings for those two
// states, which read as three distinct conditions on a row that already carries a
// label, an identity and three quota figures. The sentence moved into the tooltip.
const identityNote=(account:ProviderAccount)=>account.identity_source==='token'?'verified':'unverified'
const identityTitle=(account:ProviderAccount)=>account.identity_source==='token'?'Identity confirmed by asking the provider with these credentials.':'Identity has not been confirmed against the provider yet.'
// Daemon clock: the refresh was stamped there, so ageing it locally would report
// a just-polled account as minutes stale on a client whose clock is behind.
const formatRefreshAge=(seconds?:number,nowSeconds=serverNow())=>{
  if(!seconds)return ''
  const delta=Math.max(0,Math.floor(nowSeconds-seconds))
  if(delta<60)return 'now'
  const minutes=Math.floor(delta/60)
  if(minutes<60)return `${minutes}m`
  const hours=Math.floor(minutes/60)
  if(hours<24)return `${hours}h`
  return `${Math.floor(hours/24)}d`
}

function useProviderAccounts(intervalMs=60_000,idleMs=intervalMs) {
  const [status,setStatus]=useState<ProviderAccountsStatus|null>(null)
  const [error,setError]=useState('')
  const load=()=>api<ProviderAccountsStatus>('GET','/api/provider-accounts').then(value=>{setStatus(value);setError('')}).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
  // A sign-in running on the daemon is the only state here that a human is
  // actively waiting on, so it tightens the poll for as long as it lasts.
  const awaitingLogin=loginRunning(status?.login)
  // With no provider configured there is nothing on this payload that changes
  // except by an act somewhere else - a `claude login` in an outside terminal -
  // so the switcher slows down rather than stopping. Stopping would mean such a
  // login never appeared at all; a minute of extra latency on a surface that is
  // currently one dismissable invitation costs nothing. Opt-in, because Settings
  // → Accounts is where a login *is* being waited for.
  const idle=!!status&&!visibleProviders(status).length
  const period=awaitingLogin?LOGIN_POLL_MS:idle?idleMs:intervalMs
  useEffect(()=>{
    void load()
    // Skip while hidden and catch up on return, matching every other poll in the
    // app. Two or three of these mount at once (sidebar plus collapsed rail), so
    // a backgrounded tab was making several provider round-trips a minute for a
    // view nobody could see.
    const timer=window.setInterval(()=>{if(!document.hidden)void load()},period)
    const changed=()=>void load()
    const onVisible=()=>{if(!document.hidden)void load()}
    window.addEventListener(providerEvent,changed)
    document.addEventListener('visibilitychange',onVisible)
    return()=>{
      window.clearInterval(timer)
      window.removeEventListener(providerEvent,changed)
      document.removeEventListener('visibilitychange',onVisible)
    }
  },[period])
  return {status,setStatus,error,setError,load}
}

/** Start a sign-in, or end one. Shared because both surfaces own the same two verbs:
 *  the popover so a first account can be added without finding Settings at all, and
 *  Settings for the re-authenticate path. Neither waits for the provider CLI - the
 *  request returns as soon as the daemon has the run, and progress arrives through the
 *  ordinary accounts poll. */
function useProviderLogin(
  setStatus:(value:ProviderAccountsStatus)=>void,
  setError:(value:string)=>void,
) {
  const [busy,setBusy]=useState('')
  const call=async(key:string,path:string,body?:unknown)=>{
    setBusy(key);setError('')
    try{setStatus(await api<ProviderAccountsStatus>('POST',path,body||{}));notifyChanged()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  return {
    busy,
    startLogin:(provider:ProviderName,replaceId?:string)=>void call(`login-${provider}`,`/api/provider-accounts/${provider}/login`,replaceId?{replace_id:replaceId}:{}),
    dismissLogin:(provider:ProviderName)=>void call(`login-dismiss-${provider}`,`/api/provider-accounts/${provider}/login/dismiss`),
  }
}

/** The whole of a sign-in's visible life: running, succeeded, or failed with the reason.
 *  A failure has no other home - the request that started it returned long ago - so it
 *  stays until somebody dismisses it. */
function LoginProgress({login,busy,onDismiss}:{login:LoginState|null;busy:boolean;onDismiss:()=>void}) {
  if(!login)return null
  const running=login.state==='running'
  // A `div`, not a `p`: the popover styles every direct `p` child of a section as muted
  // 8px marginalia, which is the opposite of what the one thing you are waiting on wants.
  return <div class={`account-login ${login.state}`} role={login.state==='failed'?'alert':'status'}>
    <strong>{running?`Signing in to ${login.provider}…`:login.state==='succeeded'?`Signed in${login.label?` as ${login.label}`:''}`:`${login.provider} sign-in did not finish`}</strong>
    <small>{running?`Finish the login in the browser on the daemon host. This keeps running if you close this panel, and shows up wherever you look next.`:login.state==='failed'?login.error||'no reason reported':'The account is saved and selected.'}</small>
    <button disabled={busy} onClick={onDismiss}>{running?'cancel':'dismiss'}</button>
  </div>
}

export function AccountSwitcher({variant='full',placement,onManage,promptDismissed,promptSuppressed,onDismissPrompt}:{
  // `variant` picks the trigger; `placement` is independent because the collapsed
  // desktop rail wants a condensed trigger with an upward-opening popover.
  variant?:'full'|'compact'|'rail';placement?:'up'|'down';onManage:()=>void
  // The empty-state invitation, which belongs to the expanded sidebar alone and
  // is therefore the host's to persist: it is machine config, not this
  // component's state. `promptSuppressed` is the first-run surfaces asking for
  // the floor - a tour whose account step is already on screen does not want a
  // second invitation competing with it.
  promptDismissed?:boolean;promptSuppressed?:boolean;onDismissPrompt?:()=>void
}) {
  const compact=variant!=='full'
  const {status,setStatus,error,setError}=useProviderAccounts(60_000,IDLE_POLL_MS)
  const {busy:loginBusy,startLogin,dismissLogin}=useProviderLogin(setStatus,setError)
  const [open,setOpen]=useState(false)
  const [ownBusy,setOwnBusy]=useState('')
  const busy=ownBusy||loginBusy
  const setBusy=setOwnBusy
  const [resetSound,setResetSound]=useState(()=>alertPreferences().enabled&&soundPreferences().enabled&&soundPreferences().events.reset)
  const root=useRef<HTMLDivElement>(null)
  const popover=useRef<HTMLDivElement>(null)
  const [popoverStyle,setPopoverStyle]=useState<Record<string,string>>({})
  // Two lists on purpose. `providers` is everything mux can manage and is what the
  // popover offers a sign-in for; `visible` is the subset a credential exists for,
  // and is what the status block, the rail and the phone's toolbar draw.
  const providers=status?.providers||[]
  const visible=useMemo(()=>visibleProviders(status),[status])
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
    // A click outside closes it, but not while a request this popover started is
    // still out: a switch or a dismissal that lands on an unmounted popover takes
    // its error with it, and the click that closed it is usually the impatient
    // second one.
    const dismiss=(event:PointerEvent)=>{const target=event.target as Node;if(busy)return;if(!root.current?.contains(target)&&!popover.current?.contains(target))setOpen(false)}
    const key=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!busy)setOpen(false)}
    window.addEventListener('resize',reposition);window.addEventListener('scroll',reposition,true);window.addEventListener('pointerdown',dismiss);window.addEventListener('keydown',key)
    return()=>{window.removeEventListener('resize',reposition);window.removeEventListener('scroll',reposition,true);window.removeEventListener('pointerdown',dismiss);window.removeEventListener('keydown',key)}
  },[open,opensDown,busy])
  const popup=open&&<div ref={popover} class="account-popover ui-portal" style={popoverStyle} role="dialog" aria-label="Provider account switcher">
      <header><div><strong>ACCOUNTS</strong><span>quota per account</span></div><button aria-label="Close account switcher" onClick={()=>setOpen(false)}>×</button></header>
      {providers.map(provider=>{const current=status?.current[provider];const active=selected(provider);const login=loginOf(status?.login,provider);const saved=status?.accounts.filter(account=>account.provider===provider)||[];
        // Decided once per provider, then handed to every row, so the columns are the
        // section's rather than each account's. See `hasFableWindow`.
        const sectionFable=hasFableWindow(saved)
        return <section>
        {/* Sign-in lives here, not only in Settings. With nothing saved this popover
            used to print "No saved accounts" and offer a `manage…` button, which is
            the one screen a new install always lands on and the one with no way
            forward on it. */}
        <div class="account-section-head"><h4>{provider}</h4><button class="account-signin" disabled={!!busy||login?.state==='running'} title={signInTitle(status?.login_commands,provider)} onClick={()=>startLogin(provider)}>+ sign in</button></div>
        {current?.state!=='saved'&&<p class={`account-current-notice ${current?.state||'signed_out'}`}>{currentDescription(current,active)}{current?.match_hint?` ${hintDescription(current)}`:''}</p>}
        <LoginProgress login={login} busy={!!busy} onDismiss={()=>dismissLogin(provider)}/>
        {saved.map(account=>{
          // Sessions this account was selected for when they started. Deliberately not
          // "sessions using it": mux cannot see a `/login` typed inside a pane, and a
          // CLI already running holds the credential it read at startup. It joins the
          // accessible name because `aria-label` replaces the row's content outright,
          // so a badge left out of it is a badge no screen reader can reach.
          // `data-provider-account` carries the account's id. The only other handle on
          // one row is the `active` class, which is exactly what choosing it changes - so
          // anything selecting on that (the site demo's walkthrough switches accounts and
          // switches back) would be naming a moving target.
          const spawned=spawnedSessionCount(status?.sessions,account.id)
          const spawnedTitle=`${spawned} live session${spawned===1?'':'s'} spawned under this account. That is what mux had selected when each started, not proof of what it authenticates as now.`
          const state=account.conflict&&!account.conflict.is_primary?'duplicate account, polling suspended':quotaTitle(account)
          return <button class={`${status?.selected[provider]===account.id?'active':''} ${account.conflict&&!account.conflict.is_primary?'conflicted':''}`} disabled={!!busy} data-provider-account={account.id} onClick={()=>void choose(account)} aria-label={`${account.label}: ${state}${spawned?`; ${spawnedTitle}`:''}`} title={account.conflict?conflictDescription(account):quotaTitle(account)}><span>{status?.selected[provider]===account.id?'◆':'◇'}</span><strong>{account.label}</strong>{account.quota?.refreshed_at?<i class="account-refresh-age" title={`Quotas refreshed ${new Date(account.quota.refreshed_at*1000).toLocaleString()}`}>{formatRefreshAge(account.quota.refreshed_at)}</i>:null}<small>{account.conflict&&!account.conflict.is_primary?'duplicate account · polling suspended':<span class={`quota-row${sectionFable?' has-fable':''}`}>
          {account.quota?.status==='error'
            ?<em class="quota-row-note">unavailable</em>
            :quotaRowCells(account,sectionFable).map((cell,index)=><span class={`quota-cell quota-cell-${cell.key}`} key={cell.key}>{index>0&&<i class="quota-separator" aria-hidden="true"></i>}<b>{cell.percent}</b>{' '}{cell.reset&&<i class="quota-reset">{cell.reset}</i>}<i class="quota-window-label">{cell.qualifier}</i></span>)}
        </span>}{spawned>0&&<i class="account-session-count" aria-hidden="true" title={spawnedTitle}>{spawned}×</i>}</small></button>
        })}
        {!saved.length&&!login&&<button class="account-empty-cta" disabled={!!busy} onClick={()=>startLogin(provider)}>No saved accounts — <strong>sign in to {provider}</strong></button>}
        {/* The point of the counts. A switch reaches the next process, not the ones
            already running, so this names the logins still being spent. The whole
            sentence is on screen rather than in a tooltip: this popover is the phone's
            account surface too, and a phone cannot hover. Keyed by position, because
            two accounts may carry the same label. */}
        {/* Amber when those sessions are still spending the login they started on;
            muted when the CLI follows the switch and the count is only history. The
            daemon says which (`switch_reaches_live`), because the answer is the
            vendor's CLI behaviour and differs between the two providers. */}
        {strandedSessions(status,provider).map((row,index)=>{const reachesLive=status?.switch_reaches_live?.[provider];return <p class={`account-session-notice${reachesLive===true?' follows':''}`} key={`${provider}-${index}`}>{strandedSessionNotice(row,{reachesLive,cli:harnessDisplayName(provider)})}</p>})}
      </section>})}
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
        <i class={`provider-glyph ${provider}`} aria-hidden="true">{harnessMark(provider)}</i>
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
      <span class={`provider-glyph ${provider}`} aria-hidden="true">{harnessMark(provider)}</span><strong>{weekly?`${Math.round(weekly.used_percent)}%`:'—'}</strong>
    </button>
  }
  // The expanded sidebar carries the invitation for all three surfaces: the 40px rail
  // and the phone's 44px toolbar have no room for a call to action, and a chip reading
  // `—` for a provider this machine has never signed in to is the thing being removed.
  // Held until the first payload lands, too, or a fresh load flashes the invitation at
  // an install that has two accounts.
  const invite=!compact&&!!status&&!visible.length&&!promptDismissed&&!promptSuppressed
  // Nothing to draw and nothing to invite with. `open` keeps the root mounted while
  // the popover is up, so nothing can yank its anchor away mid-request - including
  // the switch that removes the last saved account.
  if(!visible.length&&!invite&&!open)return null
  return <div ref={root} class={`account-switcher ${compact?'compact':''} ${variant==='rail'?'rail':''}`}>
    {variant==='rail'?visible.map(provider=>quotaChip(provider,weeklyTitle(provider)))
    // The mobile toolbar carries one chip per provider, in the same provider order as every
    // other surface — collapsing both into whichever weekly window was furthest along hid which
    // provider was burning. It wears the same square as the collapsed rail rather than the
    // expanded sidebar's usage breakdown: that breakdown is three columns of numbers competing
    // with the Project name and two run controls for one 44px row, and the number a phone is
    // glanced at for is how much of the week is gone. The breakdown is one tap away, in the
    // popover this chip opens, which is also where the tooltip's window-by-window text lands
    // for a device that cannot hover.
    :variant==='compact'?visible.map(provider=>quotaChip(provider,toolbarTitle(provider)))
    // The invitation replaces the rows rather than sitting above them: the moment a
    // credential exists there is something real here, and this is gone.
    :invite?<div class="account-prompt">
      <div><strong>Provider accounts</strong><small>Quota and one-click account switching for Claude and Codex, once one of them is signed in on this machine.</small></div>
      <div class="account-prompt-actions">
        <button type="button" class="primary" aria-expanded={open} onClick={show}>add provider</button>
        <button type="button" title="Puts away this invitation, not the feature. Sign in later and the quota rows come back by themselves." onClick={()=>onDismissPrompt?.()}>hide</button>
      </div>
    </div>
    :<div class="account-summary">
      {visible.map(provider=>{const account=selected(provider);const current=status?.current[provider];const state=account?account.quota?.status||'pending':current?.state||'loading';return <button class={account?'tracked':current?.state==='external'?'external':'untracked'} aria-label={`${provider} account: ${currentLabel(current,account)}; ${currentSummary(current,account)}; ${state}`} aria-expanded={open} onClick={toggle} title={`${provider} · ${account?quotaTitle(account):currentDescription(current,account)}`}>{quotaGrid(provider)}{state!=='ready'&&<em>{state}</em>}</button>})}
      {resetUnread&&<button class="quota-reset-indicator" title="Confirmed unexpected reset; open for evidence" onClick={show}><span>RESET</span><strong>{resetItems.length>1?`${resetItems.length} unexpected quota resets`:'unexpected quota reset'}</strong><small>{resetProviders.join(' · ')}</small></button>}
    </div>}
    {popup&&createPortal(popup,document.body)}
  </div>
}

export function AccountSettings() {
  const {status,setStatus,error,setError}=useProviderAccounts(120_000)
  const {busy:loginBusy,startLogin,dismissLogin}=useProviderLogin(setStatus,setError)
  const [ownBusy,setBusy]=useState('')
  const busy=ownBusy||loginBusy
  const [message,setMessage]=useState('')
  const [confirmRemove,setConfirmRemove]=useState('')
  const providers=status?.providers||[]
  const grouped=useMemo(()=>Object.fromEntries(providers.map(provider=>[provider,status?.accounts.filter(account=>account.provider===provider)||[]])),[status,providers.join('\0')])
  // The tour's account step waits for an account to actually exist. It used to fire on
  // the sign-in request returning, which was the same moment only while that request
  // blocked for the whole login; now the request returns as soon as the CLI is running,
  // so the gate follows the login to `succeeded` instead.
  const succeeded=providers.filter(provider=>loginOf(status?.login,provider)?.state==='succeeded').join('\0')
  useEffect(()=>{if(succeeded)emitTutorialAction({action:'account-saved'})},[succeeded])
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
  // No label field on the way in: the daemon names a new slot from the identity it just
  // verified (email, then organization), and the list row below renames in place. The
  // input only ever made an optional step look like a required one.
  const capture=(provider:ProviderName)=>void mutate(`${provider}-capture`,'POST',`/api/provider-accounts/${provider}/capture`,{},true)
  const reauthenticate=(account:ProviderAccount)=>startLogin(account.provider,account.id)
  const rename=(account:ProviderAccount,label:string)=>{if(label.trim()&&label.trim()!==account.label)void mutate(account.id,'PATCH',`/api/provider-accounts/${account.provider}/${account.id}`,{label})}
  const remove=(account:ProviderAccount)=>{if(confirmRemove!==account.id){setConfirmRemove(account.id);return}setConfirmRemove('');void mutate(account.id,'DELETE',`/api/provider-accounts/${account.provider}/${account.id}`)}
  return <section data-tutorial="provider-accounts" class="account-settings"><div class="account-settings-head"><h3>Provider accounts</h3><div class="account-actions">
      {/* One global control, once. `/verify` is a whole-install operation, so a copy of
          it under each provider heading was three buttons for one endpoint - and they
          shared a busy key, so pressing either read "verifying…" on both. */}
      <button disabled={!!busy} onClick={()=>void mutate('verify','POST','/api/provider-accounts/verify',{})}>{busy==='verify'?'verifying…':'verify identities'}</button>
      <button disabled={!!busy} onClick={()=>void mutate('refresh','POST','/api/provider-accounts/refresh',{})}>{busy==='refresh'?'refreshing…':'refresh quotas'}</button>
    </div></div>
    {/* Policy that never changes is reference, not instruction, and it was the first
        thing on the panel every time. Folded away, it is still one click from the
        control it describes. */}
    <details class="account-explainer"><summary>How switching works</summary><p>Switching replaces only the provider's system authentication file. Global config, skills, projects, and histories remain shared. It is never blocked and never confirmed. Whether it reaches sessions already running is up to the CLI: Claude Code re-reads its credential file when the file changes, so a running pane spends the new account from its next request, while Codex reads its login once at startup and keeps spending the outgoing account until it is restarted. The account switcher counts live sessions against the account each one was spawned under and says, per provider, which of the two applies.</p><p>The switch also restores the account's cached CLI profile, so <code>/status</code> in new sessions names the right account immediately; panes already running keep the old display until restarted, even where their requests already go to the new account.</p><p>swe-mux follows the daemon host credentials; startup never restores an older saved account. Credentials move into a saved account only on a provider-verified identity or an explicit relink.</p></details>
    {providers.map(provider=>{const current=status?.current[provider];const accounts=grouped[provider]||[];const active=accounts.find(account=>account.id===current?.account_id);const login=loginOf(status?.login,provider);return <div class="account-provider-settings"><header><div><strong>{provider.toUpperCase()}</strong><small>{accounts.length} saved · quotas refresh every {status?.poll_minutes||15} minutes</small></div></header>
      {/* Only the states that need explaining, and that carry an action. While the live
          login is a saved account, this block restated the row already marked ◆ active
          two elements below it, in a second vocabulary. */}
      {current?.state!=='saved'&&<div class={`account-current ${current?.state||'signed_out'}`}><span>LIVE SYSTEM AUTH</span><strong>{currentDescription(current,active)}</strong>{current?.match_hint&&<p class="account-relink"><span>{hintDescription(current)}</span><button disabled={!!busy} onClick={()=>void mutate(`adopt-${provider}`,'POST',`/api/provider-accounts/${provider}/${current.match_hint!.account_id}/adopt`)}>{busy===`adopt-${provider}`?'relinking…':`relink to ${current.match_hint.label}`}</button></p>}</div>}
      <div data-tutorial="provider-account-actions" class="account-add"><button class="primary" disabled={!!busy||login?.state==='running'} title={signInTitle(status?.login_commands,provider)} onClick={()=>startLogin(provider)}>sign in + save</button><button class="account-add-secondary" disabled={!!busy} title="Captures an account you signed in to separately, without starting a login." onClick={()=>capture(provider)}>{busy===`${provider}-capture`?'saving…':'already signed in? save current login'}</button></div>
      <LoginProgress login={login} busy={!!busy} onDismiss={()=>dismissLogin(provider)}/>
      <div class="account-list">{accounts.map(account=><article class={`${status?.selected[provider]===account.id?'active':''} ${account.conflict?'conflicted':''}`}><span class="account-state">{status?.selected[provider]===account.id?'◆ active':'◇ saved'}</span><input aria-label={`${provider} account label`} defaultValue={account.label} onBlur={event=>rename(account,event.currentTarget.value)}/><small>{account.email||account.organization||account.provider_account_id||'identity unavailable'}<i class={`account-identity ${account.identity_source==='token'?'verified':'unverified'}`} title={identityTitle(account)}>{identityNote(account)}</i></small><div class="account-quota" title={quotaTitle(account)}><span>session <b>{percent(account.quota?.session)}</b></span><span>weekly <b>{percent(account.quota?.weekly)}</b></span>{account.quota?.fable&&<span>fable <b>{percent(account.quota.fable)}</b></span>}<em>{account.quota?.status||'pending'}{account.quota?.error?` · ${account.quota.error}`:''}</em></div><div class="account-actions"><button disabled={!!busy||status?.selected[provider]===account.id} onClick={()=>void selectAccount(account)}>use</button><button disabled={!!busy||login?.state==='running'} onClick={()=>reauthenticate(account)}>sign in again</button><button class={confirmRemove===account.id?'danger confirming':'danger'} disabled={!!busy} onClick={()=>remove(account)}>{confirmRemove===account.id?'confirm remove':'remove'}</button></div>{account.conflict&&<p class="account-conflict" role="alert">{conflictDescription(account)}</p>}</article>)}{!accounts.length&&<div class="account-empty">No {provider} accounts saved yet.</div>}</div>
    </div>})}
    {(error||message)&&<p class={error?'settings-inline-error':''} role={error?'alert':'status'}>{error||message}</p>}
  </section>
}
